"""Clone all six roles from every project reference WAV; preserve existing voices."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import wave

import requests

from minimax_audio import load_key, synthesize, write_json


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'runs/minimax_cast_allrefs_20261110'
OUT = ROOT / 'exports/tyrol_new_table_20261110/声音试听/minimax_allrefs'
FF = 'D:/Shotcut/ffmpeg.exe'
ROLES = {'glm': '智谱娘', 'deepseek': '大肥鱼', 'gpt': '白龙酱',
         'gemini': '基米', 'claude': '克莱德', 'qwen': '千问'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def prepare(role):
    folder = ROOT / 'assets/voices' / role
    sources = sorted((folder / 'references').glob('*.wav'))
    if not sources:
        raise RuntimeError(f'{role}: no reference audio')
    primary = read(folder / 'manifest.json')['references'][0]
    prompt = ROOT / primary['path']
    if prompt not in sources:
        raise RuntimeError(f'{role}: primary reference missing')
    work = WORK / role
    work.mkdir(parents=True, exist_ok=True)
    audio = work / 'all_references.wav'
    pcm = []
    segments = []
    frames = 0
    for source in sources:
        result = subprocess.run([FF, '-v', 'error', '-i', str(source), '-ar', '24000',
                                 '-ac', '1', '-f', 's16le', '-'], capture_output=True, check=True)
        raw = result.stdout
        if not raw:
            raise RuntimeError(f'{source}: empty decoded audio')
        if pcm:
            pcm.append(bytes(12000))  # 0.25s separation, no trimming or speed/pitch changes.
            frames += 6000
        segments.append({'path': str(source.relative_to(ROOT)), 'bytes': source.stat().st_size,
                         'start_seconds': frames / 24000, 'seconds': len(raw) / 48000})
        pcm.append(raw)
        frames += len(raw) // 2
    if not 10 <= frames / 24000 <= 300 or frames * 2 + 44 > 20_000_000:
        raise RuntimeError(f'{role}: all references exceed MiniMax limits; nothing omitted')
    combined = b''.join(pcm)
    if audio.exists():
        with wave.open(str(audio), 'rb') as wav:
            if (wav.getparams()[:3] != (1, 2, 24000)
                    or wav.readframes(wav.getnframes()) != combined):
                raise RuntimeError(f'{role}: reference bundle changed; preserve prior upload receipts')
    else:
        with wave.open(str(audio), 'wb') as wav:
            wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            wav.writeframes(combined)
    with wave.open(str(prompt), 'rb') as wav:
        if wav.getnframes() / wav.getframerate() >= 8:
            raise RuntimeError(f'{role}: prompt must be shorter than 8s')
    manifest = {'role': role, 'name': ROLES[role], 'voice_id': f'IRON_{role}_allrefs_20261110',
                'reference_count': len(sources), 'duration_seconds': frames / 24000,
                'bundle': str(audio), 'segments': segments, 'prompt': primary,
                'processing': 'All source audio retained; mono PCM24kHz, 0.25s gaps; no pitch/tempo changes'}
    write_json(work / 'references.json', manifest)
    return manifest


def api_call(key, operation, record_path, request, file=None):
    if record_path.exists():
        record = read(record_path)
        if record['state'] == 'succeeded' and record['request'] == request:
            return record['response']
        raise RuntimeError(f'Inspect existing failed/uncertain receipt: {record_path}')
    record = {'state': 'outcome_unknown', 'request': request,
              'started_utc': datetime.now(timezone.utc).isoformat()}
    write_json(record_path, record)
    kwargs = {'json': request} if file is None else {
        'data': {'purpose': request['purpose']}, 'files': {'file': (file.name, file.open('rb'), 'audio/wav')}}
    try:
        response = requests.post('https://api.minimax.io/v1/' + operation,
                                 headers={'Authorization': 'Bearer ' + key},
                                 timeout=(15, 180), allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise RuntimeError(f'{operation}: interrupted; no automatic resubmission') from None
    finally:
        if file is not None:
            kwargs['files']['file'][1].close()
    record['http_status'] = response.status_code
    try:
        result = response.json()
    except ValueError:
        write_json(record_path, record)
        raise RuntimeError(f'{operation}: non-JSON response; outcome unknown') from None
    record['response'] = result
    status = result.get('base_resp') or {}
    if response.status_code == 200 and status.get('status_code') == 0:
        record['state'] = 'succeeded'
    elif status.get('status_code') not in (None, 0):
        record['state'] = 'failed'
    write_json(record_path, record)
    if record['state'] != 'succeeded':
        raise RuntimeError(f'{operation}: HTTP {response.status_code}, {status}')
    return result


def clone_role(key, manifest, text):
    role = manifest['role']
    work = WORK / role
    prompt = ROOT / manifest['prompt']['path']
    full = Path(manifest['bundle'])
    uploaded = api_call(key, 'files/upload', work / 'upload.json',
                        {'purpose': 'voice_clone', 'path': str(full)}, full)
    prompt_upload = api_call(key, 'files/upload', work / 'prompt_upload.json',
                             {'purpose': 'prompt_audio', 'path': str(prompt)}, prompt)
    body = {'file_id': int(uploaded['file']['file_id']), 'voice_id': manifest['voice_id'],
            'clone_prompt': {'prompt_audio': int(prompt_upload['file']['file_id']),
                             'prompt_text': manifest['prompt']['prompt_text']},
            'need_noise_reduction': False, 'need_volume_normalization': False}
    api_call(key, 'voice_clone', work / 'clone.json', body)
    print('CLONED', role, manifest['voice_id'], flush=True)
    request = {'model': 'speech-2.6-turbo', 'text': text, 'stream': False,
               'language_boost': 'Chinese', 'output_format': 'hex',
               'voice_setting': {'voice_id': manifest['voice_id'], 'speed': 1.1, 'vol': 1, 'pitch': 0},
               'audio_setting': {'sample_rate': 32000, 'bitrate': 128000, 'format': 'mp3', 'channel': 1}}
    audio = OUT / f'{role}·{ROLES[role]}.mp3'
    receipt = synthesize(key, request, audio, 'io')
    subprocess.run([FF, '-v', 'error', '-i', str(audio), '-f', 'null', '-'], check=True)
    return {'name': ROLES[role], 'voice_id': manifest['voice_id'],
            'reference_count': manifest['reference_count'], 'reference_seconds': manifest['duration_seconds'],
            'references': str(work / 'references.json'), 'clone_receipt': str(work / 'clone.json'),
            'audition': str(audio), 'usage': receipt['response'].get('extra_info')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--role', choices=list(ROLES))
    args = parser.parse_args()
    if (WORK / 'retired.json').exists() and not args.prepare_only:
        raise RuntimeError('Old voice IDs were retired; use tools/minimax_rebuild_cast.py for the current cast')
    manifests = [prepare(role) for role in ROLES]
    write_json(WORK / 'inventory.json', manifests)
    print(json.dumps([{k: r[k] for k in ('role', 'reference_count', 'duration_seconds')}
                      for r in manifests], ensure_ascii=False), flush=True)
    if args.prepare_only:
        return
    key = load_key()
    if not key:
        raise RuntimeError('MINIMAX_API_KEY missing')
    OUT.mkdir(parents=True, exist_ok=True)
    profile = read(ROOT / 'exports/tyrol_new_table_20261110/声音试听/basic_v4/实测与请求.json')
    texts = {r['role']: r['request']['input']['text'] for r in profile['lines']}
    cast_path = ROOT / 'configs/tts.minimax.cast.json'
    cast = read(cast_path) if cast_path.exists() else {
        'provider': 'minimax', 'model': 'speech-2.6-turbo', 'site': 'io', 'roles': {}}
    for manifest in manifests:
        if args.role and manifest['role'] != args.role:
            continue
        role = manifest['role']
        cast['roles'][role] = clone_role(key, manifest, texts[role])
        write_json(cast_path, cast)
        write_json(OUT / '音色清单.json', cast)
    print('CAST', cast_path, flush=True)


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc))
