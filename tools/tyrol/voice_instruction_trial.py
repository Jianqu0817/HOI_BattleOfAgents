"""Synthesize six small character-direction trials without replacing episode takes."""
import argparse
import base64
from datetime import datetime, timezone
from getpass import getpass
import os
from pathlib import Path
import re
import sys

import numpy as np
import requests

import session_edit as edit
from voice_preview import assemble, pitch_stats


def request_take(key, body, audio, sources):
    """One resumable synthesis; never resubmit an outcome-unknown paid request."""
    meta=audio.with_suffix('.json');line=audio.stem
    if audio.exists() and meta.exists() and edit.read(meta)['request']==body:
        print('REUSE',line,flush=True);return
    uncertain=audio.with_suffix('.uncertain.json')
    if uncertain.exists():raise RuntimeError(f'{line}: previous request outcome unknown; reconcile before retrying')
    started=datetime.now(timezone.utc).isoformat()
    try:
        response=requests.post(edit.API,json=body,headers={'Authorization':'Bearer '+key},timeout=(15,120),allow_redirects=False)
    except requests.RequestException:
        edit.write(uncertain,{'request':body,'state':'outcome_unknown','started_utc':started})
        raise RuntimeError(f'{line}: request interrupted; stopped without automatic retry') from None
    result=response.json()
    if response.status_code!=200 or 'output' not in result:
        edit.write(audio.with_suffix('.failed.json'),{'request':body,'http_status':response.status_code,'response':result})
        raise RuntimeError(f'{line}: HTTP {response.status_code}, {result.get("code")}: {str(result.get("message",""))[:160]}')
    edit.write(audio.with_suffix('.response.json'),{'request':body,'response':result})
    item=result['output']['audio'];url=item.get('url','')
    if item.get('data'):data=base64.b64decode(item['data'],validate=True)
    elif url.startswith('data:'):data=base64.b64decode(url.split(',',1)[1],validate=True)
    else:
        if url.startswith('http://'):url='https://'+url[7:]
        if not url.startswith('https://'):raise ValueError('Unexpected audio URL scheme')
        download=requests.get(url,timeout=(15,90));download.raise_for_status();data=download.content
    data,seconds=edit.finite_wav(data);audio.write_bytes(data)
    edit.write(meta,{'request':body,'sources':sources,'seconds':seconds,'started_utc':started,
                     'finished_utc':datetime.now(timezone.utc).isoformat(),
                     'request_id':result.get('request_id'),'usage':result.get('usage',{})})
    print('SYNTHESIZED',line,round(seconds,2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    parser.add_argument('--profile',type=Path,default=edit.ROOT/'configs/tts.role-instructions.trial.json')
    parser.add_argument('--prompt-key',action='store_true')
    parser.add_argument('--first-only',action='store_true')
    parser.add_argument('--render-only',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.name):parser.error('Invalid table name')
    profile=edit.read(args.profile)
    source=edit.ROOT/'runs'/args.name/'edit'
    trial_name=args.name+'_role_instruction_v1'
    work=edit.ROOT/'runs'/trial_name/'edit';takes=work/'takes';takes.mkdir(parents=True,exist_ok=True)
    out=edit.ROOT/'exports'/args.name/'声音试听/instruction_v1';out.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(source.parent/'voice_preview_250hz/deps'))
    import parselmouth
    originals={r['line_id']:r for r in edit.read(source/'timeline.json')['rows']}
    key=os.getenv('DASHSCOPE_API_KEY')
    if not key and not args.render_only and args.prompt_key:key=getpass('Qwen TTS API key (hidden): ')
    if not key and not args.render_only:raise RuntimeError('Configure DASHSCOPE_API_KEY or use --prompt-key')
    rows=[];requests_by_id={}
    for role,settings in profile['roles'].items():
        row=dict(originals[settings['line_id']]);line=row['line_id'];old=edit.read(source/'takes'/(line+'.json'))
        pcm=edit.read_pcm(source/'takes'/(line+'.wav'),1)
        before=pitch_stats(parselmouth.Sound(pcm.astype(float)/32768,sampling_frequency=edit.SR))
        chars=len(re.findall(r'[\u4e00-\u9fff]',row['text']))
        baseline_rate=chars/(len(pcm)/edit.SR)
        rate=round(min(2,max(.5,old['request']['input']['rate']*4/baseline_rate)),3)
        pitch=old['request']['input']['pitch']
        if settings['target_hz']:pitch=round(pitch*settings['target_hz']/before['median_hz'],3)
        body={'model':edit.MODEL,'input':{**old['request']['input'],'instruction':profile['common']+settings['direction'],
                                        'rate':rate,'pitch':pitch,'seed':40}}
        body['input'].update(settings.get('synthesis',{}))
        requests_by_id[line]=(body,old,before)
        row.update(chapter='角色专属 instruction · 重新合成试听',stamp='重新合成 · 专属表演指令',
                   edit_tempo=1.0,gap=.64,voice_direction_profile=str(args.profile),source_text=row['text'])
        row.pop('start_frame',None);row.pop('end_frame',None);row.pop('clip',None)
        rows.append(row)
    if not args.render_only:
        # Establish one real result before spending on the rest of the small trial.
        order=['L007']+[r['line_id'] for r in rows if r['line_id']!='L007']
        for line in order[:1] if args.first_only else order:
            body,old,_=requests_by_id[line]
            request_take(key,body,takes/(line+'.wav'),old['sources'])
    if args.first_only:return
    key=None
    assert all((takes/(r['line_id']+'.wav')).exists() for r in rows)
    for row in rows:
        # Re-synthesized audio must invalidate the picture/subtitle render cache.
        row['voice_request']=edit.read(takes/(row['line_id']+'.json'))['request']
    edit.write(work/'prepared.json',{'title':'角色专属 instruction 试听','rows':rows})
    edit.render(trial_name)
    timeline=edit.read(work/'timeline.json');new=[];comparison=[];report=[]
    for row in timeline['rows']:
        line=row['line_id'];clip=Path(row['clip']);n=row['end_frame']-row['start_frame']
        b=dict(line_id=line,clip=str(clip),audio=str(clip.with_suffix('.audio.wav')),frames=n)
        original=originals[line];old_frames=original['end_frame']-original['start_frame']
        a=dict(line_id=line,clip=original['clip'],audio=str(Path(original['clip']).with_suffix('.audio.wav')),frames=old_frames)
        new.append(b);comparison.extend([a,b])
        pcm=edit.read_pcm(takes/(line+'.wav'),1)
        stats=pitch_stats(parselmouth.Sound(pcm.astype(float)/32768,sampling_frequency=edit.SR))
        body,_,before=requests_by_id[line]
        report.append(dict(line_id=line,role=row['role'],speaker=row['speaker'],text=row['text'],
                           instruction=body['input']['instruction'],rate=body['input']['rate'],pitch=body['input']['pitch'],
                           seed=40,measured_hanzi_per_second=len(re.findall(r'[\u4e00-\u9fff]',row['text']))/(len(pcm)/edit.SR),
                           measured_pitch=stats,baseline_pitch=before))
    preview=out/'角色专属instruction·重新合成试听.mp4'
    frames=assemble(new,work,preview)
    compare=out/'角色专属instruction·旧配音与新合成对比.mp4'
    compare_frames=assemble(comparison,work,compare)
    edit.write(out/'实测与请求.json',dict(profile=profile,lines=report,frames=frames,comparison_frames=compare_frames,
                                       segments=new,comparison_segments=comparison,preview=str(preview),comparison=str(compare),
                                       audio_postprocessing='Silence trimming, padding, loudness and resampling only; no pitch or tempo processing'))
    text=['角色专属 instruction 试听；每段重新合成，不覆盖正式版。','共同指令：'+profile['common'],'']
    for row in report:text.extend([row['speaker'],row['instruction'],f"API rate={row['rate']}，pitch={row['pitch']}；实测 {row['measured_hanzi_per_second']:.2f}汉字/秒，{row['measured_pitch']['median_hz']:.1f}Hz",''])
    (out/'角色指令与实测.txt').write_text('\n'.join(text),'utf-8-sig')
    print(preview,flush=True)


if __name__=='__main__':main()
