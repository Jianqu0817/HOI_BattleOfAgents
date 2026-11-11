"""Release the approved old cast and rebuild six all-reference voices without clone_prompt."""
import argparse
import copy
from pathlib import Path
import re
import subprocess
import wave

import requests

from minimax_audio import load_key, synthesize, write_json
from minimax_clone_cast import ROOT, ROLES, FF, WORK as OLD_WORK, read, api_call


WORK = ROOT / 'runs/minimax_cast_noprompt_rebuild_20261110'
OUT = ROOT / 'exports/tyrol_new_table_20261110/声音试听/minimax_noprompt_rebuild'
CAST = ROOT / 'configs/tts.minimax.cast.json'
GENERATION = 'noprompt_rebuild_20261110'
KNOWN_OLD_IDS = {f'IRON_{role}_allrefs_20261110' for role in ROLES} | {
    'moss_audio_eaaf47a8-dcf7-11f1-bf7a-5699d2bd1137',
    'moss_audio_7d237b65-dd04-11f1-bf7a-5699d2bd1137',
    'IRON_glm_allrefs_noprompt_20261110'}


def list_voices(key, receipt):
    response = requests.post('https://api.minimax.io/v1/get_voice',
                             headers={'Authorization': 'Bearer ' + key},
                             json={'voice_type': 'voice_cloning'},
                             timeout=(15, 45), allow_redirects=False)
    result = response.json()
    write_json(receipt, {'http_status': response.status_code, 'response': result})
    if response.status_code != 200 or (result.get('base_resp') or {}).get('status_code') != 0:
        raise RuntimeError(f'Voice inventory failed: {result.get("base_resp")}')
    return {v['voice_id'] for v in result.get('voice_cloning') or []}


def prepare_uploads(key):
    manifests = read(OLD_WORK / 'inventory.json')
    if {r['role'] for r in manifests} != set(ROLES):
        raise RuntimeError('Incomplete source cast')
    for row in manifests:
        role = row['role']
        sources = {p for p in (ROOT / 'assets/voices' / role / 'references').glob('*.wav')}
        if sources != {ROOT / s['path'] for s in row['segments']}:
            raise RuntimeError(f'{role}: source inventory changed; prepare the full reference bundle first')
        bundle = Path(row['bundle'])
        with wave.open(str(bundle), 'rb') as wav:
            if wav.getparams()[:3] != (1, 2, 24000) or not 10 <= wav.getnframes() / 24000 <= 300:
                raise RuntimeError(f'{role}: invalid reference bundle')
        if bundle.stat().st_size > 20_000_000:
            raise RuntimeError(f'{role}: oversized reference bundle')
        folder = WORK / role
        folder.mkdir(parents=True, exist_ok=True)
        # Secure fresh uploaded files for all roles before releasing any voice slot.
        uploaded = api_call(key, 'files/upload', folder / 'upload.json',
                            {'purpose': 'voice_clone', 'path': str(bundle)}, bundle)
        row['new_file_id'] = int(uploaded['file']['file_id'])
        row['new_voice_id'] = f'IRON_{role}_{GENERATION}'
        print('REFERENCE READY', role, row['reference_count'], flush=True)
    write_json(WORK / 'references.json', manifests)
    return manifests


def release_existing(key, approved):
    plan_path = WORK / 'release_plan.json'
    if not plan_path.exists() and not approved:
        raise RuntimeError('First release requires explicit user authorization and --release-existing')
    current = list_voices(key, WORK / 'voices_before_resume.json')
    if plan_path.exists():
        plan = read(plan_path)
    else:
        if current - KNOWN_OLD_IDS:
            raise RuntimeError('Unexpected voices found; review inventory before changing the release scope')
        plan = {'voice_ids': sorted(current), 'authorization': 'User: 全都释放，重新创建。'}
        write_json(WORK / 'previous_cast.json', read(CAST))
        write_json(plan_path, plan)
    cast = read(CAST)
    if cast.get('generation') != GENERATION:
        write_json(CAST, {'provider': 'minimax', 'model': 'speech-2.8-turbo', 'site': 'io',
                          'generation': GENERATION, 'state': 'rebuilding', 'roles': {},
                          'previous_cast': str(WORK / 'previous_cast.json')})
    folder = WORK / 'deletions'
    folder.mkdir(exist_ok=True)
    for voice_id in plan['voice_ids']:
        if voice_id not in current:
            continue  # Reconcile completed/uncertain deletions through actual inventory.
        api_call(key, 'delete_voice', folder / f'{voice_id}.json',
                 {'voice_type': 'voice_cloning', 'voice_id': voice_id})
        print('DELETED', voice_id, flush=True)
    remaining = list_voices(key, WORK / 'voices_after_release.json')
    if remaining & set(plan['voice_ids']):
        raise RuntimeError('Some planned old voices are still visible; stop before rebuilding')
    write_json(OLD_WORK / 'retired.json', {'reason': 'Old voice IDs released by user request',
                                         'release_plan': str(plan_path), 'replacement': str(CAST)})
    return plan


def build_role(key, row, baseline):
    role = row['role']
    body = {'file_id': row['new_file_id'], 'voice_id': row['new_voice_id'],
            'need_noise_reduction': False, 'need_volume_normalization': False}
    api_call(key, 'voice_clone', WORK / role / 'clone.json', body)
    request = copy.deepcopy(baseline['request'])
    request['voice_setting']['voice_id'] = row['new_voice_id']
    assert request['model'] == 'speech-2.8-turbo'
    audio = OUT / f'{role}·{ROLES[role]}.mp3'
    receipt = synthesize(key, request, audio, 'io')
    pcm = subprocess.run([FF, '-v', 'error', '-i', str(audio), '-ar', '32000', '-ac', '1',
                          '-f', 's16le', '-'], capture_output=True, check=True).stdout
    if not pcm or not any(pcm):
        raise RuntimeError(f'{role}: empty/silent decoded audition')
    seconds = len(pcm) / 64000
    result = {'name': ROLES[role], 'voice_id': row['new_voice_id'],
              'reference_count': row['reference_count'], 'reference_seconds': row['duration_seconds'],
              'references': str(WORK / 'references.json'), 'clone_receipt': str(WORK / role / 'clone.json'),
              'clone_prompt': None, 'audition': str(audio), 'audition_seconds': seconds,
              'hanzi_per_second': len(re.findall(r'[\u4e00-\u9fff]', request['text'])) / seconds,
              'usage': receipt['response'].get('extra_info')}
    return result, pcm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-existing', action='store_true')
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    if not (WORK / 'release_plan.json').exists() and not args.release_existing:
        parser.error('Use --release-existing only after explicit user authorization')
    key = load_key()
    if not key:
        raise RuntimeError('MINIMAX_API_KEY missing')
    manifests = prepare_uploads(key)
    plan = release_existing(key, args.release_existing)
    cast = read(CAST)
    pcm_all = []
    notes = ['# 六角色：无 clone_prompt 重建版', '',
             '全部49条参考原声保留；不传额外参考提示和日文转录。',
             '统一2.8 Turbo、Chinese、speed=1.1、pitch=0、自动情绪，无后期变速或变调。', '',
             '|角色|参考数|Voice ID|试听秒数|汉字/秒|', '|---|---:|---|---:|---:|']
    position = 0
    for row in manifests:
        role = row['role']
        baseline = read(ROOT / 'exports/tyrol_new_table_20261110/声音试听/minimax_28_turbo' /
                        f'{role}·{ROLES[role]}.json')
        result, pcm = build_role(key, row, baseline)
        if pcm_all:
            pcm_all.append(bytes(64000))
            position += 1
        result['preview_start_seconds'] = position
        position += result['audition_seconds']
        pcm_all.append(pcm)
        cast['roles'][role] = result
        write_json(CAST, cast)
        write_json(OUT / '音色清单.json', cast)
        notes.append(f'|{ROLES[role]}|{result["reference_count"]}|`{result["voice_id"]}`|'
                     f'{result["audition_seconds"]:.2f}|{result["hanzi_per_second"]:.2f}|')
        print('READY', role, result['voice_id'], flush=True)
    current = list_voices(key, WORK / 'voices_final.json')
    expected = {r['voice_id'] for r in cast['roles'].values()}
    if current != expected or current & set(plan['voice_ids']):
        raise RuntimeError('Final voice inventory does not match the six rebuilt voices')
    preview = OUT / '六角色·无日文提示重建试听.mp3'
    subprocess.run([FF, '-v', 'error', '-f', 's16le', '-ar', '32000', '-ac', '1', '-i', '-',
                    '-c:a', 'libmp3lame', '-b:a', '128k', '-y', str(preview)],
                   input=b''.join(pcm_all), check=True)
    cast.update(state='ready', active_voice_ids_verified=True, preview=str(preview),
                preview_order=list(ROLES), preview_seconds=position,
                total_tts_usage_characters=sum(r['usage']['usage_characters'] for r in cast['roles'].values()),
                deleted_voice_ids=plan['voice_ids'])
    write_json(CAST, cast)
    write_json(OUT / '音色清单.json', cast)
    notes.extend(['', '试听顺序：智谱娘、大肥鱼、白龙酱、基米、克莱德、千问。', '', '## 单人试听', ''])
    for role, result in cast['roles'].items():
        notes.extend([f'### {result["name"]}', f'![{result["name"]}]({Path(result["audition"]).as_posix()})', ''])
    (OUT / '试听说明.md').write_text('\n'.join(notes), encoding='utf-8')
    print('COMPLETE', len(plan['voice_ids']), 'deleted;', len(current), 'rebuilt;', preview, flush=True)


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc))
