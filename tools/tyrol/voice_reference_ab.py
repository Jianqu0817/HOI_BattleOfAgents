"""Concurrent cloned-voice versus system-voice trials with identical girl-voice directions."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from getpass import getpass
import os
from pathlib import Path
import re
import sys

import session_edit as edit
from voice_instruction_trial import request_take
from voice_preview import assemble, pitch_stats


def make_trial_rows(originals, source, profile):
    rows=[];jobs=[]
    for role,settings in profile['roles'].items():
        base=dict(originals[settings['line_id']]);old=edit.read(source/'takes'/(base['line_id']+'.json'))
        text=base['text']
        for before,after in settings.get('text_replace',{}).items():text=text.replace(before,after)
        common={**old['request']['input'],'text':edit.spoken(text),
                'instruction':profile['common']+settings['direction'],'seed':40,**settings['synthesis']}
        for variant in ('reference','system'):
            row={**base,'line_id':base['line_id']+'_'+variant,'source_line_id':base['line_id'],
                 'text':text,'source_text':base['text'],'variant':variant,
                 'speaker':settings.get('display_name',edit.NAMES[role]),'edit_tempo':1.0,'gap':.64,
                 'chapter':'A · 角色参考声线' if variant=='reference' else 'B · 无参考音频 / 系统女声',
                 'stamp':'重新合成 · 少女设定 / 改编试听'}
            for k in ('start_frame','end_frame','clip'):row.pop(k,None)
            if role=='gpt':row['board']='GPT · 白龙酱'
            body={'model':edit.MODEL,'input':{**common,'voice':old['request']['input']['voice'] if variant=='reference' else profile['system_voice']}}
            jobs.append(dict(row=row,body=body,sources=old['sources'] if variant=='reference' else []))
            rows.append(row)
    return rows,jobs


def concurrent_synthesis(jobs, takes, key, workers):
    """Keep only workers paid calls in flight; stop feeding the queue after an error."""
    items=iter(jobs)
    def submit(pool,job):
        return pool.submit(request_take,key,job['body'],takes/(job['row']['line_id']+'.wav'),job['sources'])
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending={submit(pool,job) for job in list(__import__('itertools').islice(items,workers))}
        while pending:
            done,pending=wait(pending,return_when=FIRST_COMPLETED)
            for future in done:future.result()
            for _ in done:
                job=next(items,None)
                if job is not None:pending.add(submit(pool,job))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    parser.add_argument('--profile',type=Path,default=edit.ROOT/'configs/tts.role-instructions.reference-ab.json')
    parser.add_argument('--workers',type=int,choices=(1,2,3),default=3)
    parser.add_argument('--prompt-key',action='store_true')
    parser.add_argument('--render-only',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.name):parser.error('Invalid table name')
    profile=edit.read(args.profile);source=edit.ROOT/'runs'/args.name/'edit'
    sys.path.insert(0,str(source.parent/'voice_preview_250hz/deps'))
    import parselmouth
    trial_name=args.name+'_reference_ab_v2';work=edit.ROOT/'runs'/trial_name/'edit'
    takes=work/'takes';takes.mkdir(parents=True,exist_ok=True)
    originals={r['line_id']:r for r in edit.read(source/'timeline.json')['rows']}
    rows,jobs=make_trial_rows(originals,source,profile)
    for a,b in zip(jobs[::2],jobs[1::2]):
        assert {k:v for k,v in a['body']['input'].items() if k!='voice'}=={k:v for k,v in b['body']['input'].items() if k!='voice'}
        assert not b['sources'] and b['body']['input']['voice']==profile['system_voice']
    if not args.render_only:
        key=os.getenv('DASHSCOPE_API_KEY')
        if not key and args.prompt_key:key=getpass('Qwen TTS API key (hidden): ')
        if not key:raise RuntimeError('Configure DASHSCOPE_API_KEY or use --prompt-key')
        concurrent_synthesis(jobs,takes,key,args.workers)
        key=None
    assert all((takes/(r['line_id']+'.wav')).exists() for r in rows)
    for row in rows:row['voice_request']=edit.read(takes/(row['line_id']+'.json'))['request']
    edit.write(work/'prepared.json',{'title':'少女指令：参考声线 A/B','rows':rows})
    edit.render(trial_name)
    timeline=edit.read(work/'timeline.json');segments=[];measurements=[]
    for row in timeline['rows']:
        clip=Path(row['clip']);frames=row['end_frame']-row['start_frame']
        segment=dict(line_id=row['line_id'],variant=row['variant'],role=row['role'],clip=str(clip),audio=str(clip.with_suffix('.audio.wav')),frames=frames)
        segments.append(segment)
        pcm=edit.read_pcm(takes/(row['line_id']+'.wav'),1)
        stats=pitch_stats(parselmouth.Sound(pcm.astype(float)/32768,sampling_frequency=edit.SR))
        metadata=edit.read(takes/(row['line_id']+'.json'))
        measurements.append(dict(line_id=row['line_id'],speaker=row['speaker'],role=row['role'],variant=row['variant'],
                                 text=row['text'],instruction=metadata['request']['input']['instruction'],
                                 hanzi_per_second=len(re.findall(r'[\u4e00-\u9fff]',row['text']))/(len(pcm)/edit.SR),pitch=stats,
                                 request=metadata['request'],reference_sources=metadata['sources'],
                                 started_utc=metadata.get('started_utc'),finished_utc=metadata.get('finished_utc')))
    out=edit.ROOT/'exports'/args.name/'声音试听/reference_ab_v2';out.mkdir(parents=True,exist_ok=True)
    files={};counts={}
    for key,title,selected in [('reference','A·少女指令·角色参考声线',[r for r in segments if r['variant']=='reference']),
                               ('system','B·少女指令·无参考音频系统女声',[r for r in segments if r['variant']=='system']),
                               ('comparison','少女指令·每人先A参考后B系统对比',segments)]:
        path=out/(title+'.mp4');counts[key]=assemble(selected,work,path);files[key]=str(path)
    edit.write(out/'实测与请求.json',dict(profile=profile,workers=args.workers,segments=segments,lines=measurements,files=files,frames=counts,
          comparison_rule='Within each role, only the voice ID differs: existing cloned voice vs one common system female voice. No reference audio or cloned ID in system requests.',
          audio_postprocessing='Silence trimming, loudness, resampling and pauses only. No pitch or tempo postprocessing.'))
    text=['共同少女指令：'+profile['common'],'','无参考音频版系统音色：'+profile['system_voice'],'同一角色两版仅voice字段不同，其他台词、instruction、rate、pitch与seed相同。','']
    for role,settings in profile['roles'].items():text.extend([edit.NAMES[role],profile['common']+settings['direction'],''])
    (out/'角色指令.txt').write_text('\n'.join(text),'utf-8-sig')
    print(out,flush=True)


if __name__=='__main__':
    main()
