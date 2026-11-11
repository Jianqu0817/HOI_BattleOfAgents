"""Resynthesize a reference-voice audition with explicit instruction/rate changes."""
import argparse
from getpass import getpass
import os
from pathlib import Path
import re
import sys

import session_edit as edit
from voice_preview import assemble, pitch_stats
from voice_reference_ab import concurrent_synthesis


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    parser.add_argument('--profile',type=Path,default=edit.ROOT/'configs/tts.role-instructions.expression-v3.json')
    parser.add_argument('--baseline',type=Path,help='An existing audition measurement JSON to compare against')
    parser.add_argument('--label',default='expression_v3')
    parser.add_argument('--workers',type=int,choices=(1,2,3),default=3)
    parser.add_argument('--prompt-key',action='store_true')
    parser.add_argument('--render-only',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.name):parser.error('Invalid table name')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.label):parser.error('Invalid trial label')
    root=edit.ROOT/'runs';baseline=edit.read(args.baseline or edit.ROOT/'exports'/args.name/'声音试听/reference_ab_v2/实测与请求.json')
    base_segments={r['role']:r for r in baseline['segments'] if r.get('variant','reference')=='reference'}
    baseline_work=Path(next(iter(base_segments.values()))['clip']).parents[1]
    baseline_rows={r['line_id']:r for r in edit.read(baseline_work/'prepared.json')['rows']}
    profile=edit.read(args.profile);trial_name=args.name+'_'+args.label;work=root/trial_name/'edit'
    takes=work/'takes';takes.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(root/args.name/'voice_preview_250hz/deps'))
    import parselmouth
    jobs=[];rows=[];previous={}
    for old in baseline['lines']:
        if old.get('variant','reference')!='reference':continue
        role=old['role'];original=baseline_rows[old['line_id']]
        line=original['source_line_id']+('_expression' if args.label=='expression_v3' else '_'+args.label)
        changes=profile['roles'][role].get('synthesis',{})
        assert set(changes)<= {'rate','pitch'}
        body={'model':old['request']['model'],'input':{**old['request']['input'],
              'instruction':profile['common']+profile['roles'][role]['direction'],**changes}}
        allowed={'instruction',*changes}
        assert {k:v for k,v in body['input'].items() if k not in allowed}=={k:v for k,v in old['request']['input'].items() if k not in allowed}
        row={**original,'line_id':line,'variant':args.label,'chapter':profile.get('chapter','B · 轻咬字 / 情绪转折'),
             'stamp':profile.get('stamp','角色参考声线 · 仅修改 instruction'),'edit_tempo':1.0,'gap':.64}
        row.pop('voice_request',None)
        sources=old.get('reference_sources',old.get('baseline',{}).get('reference_sources'))
        assert sources
        rows.append(row);jobs.append(dict(row=row,body=body,sources=sources));previous[role]=old
    if not args.render_only:
        key=os.getenv('DASHSCOPE_API_KEY')
        if not key and args.prompt_key:key=getpass('Qwen TTS API key (hidden): ')
        if not key:raise RuntimeError('Configure DASHSCOPE_API_KEY or use --prompt-key')
        concurrent_synthesis(jobs,takes,key,args.workers)
        key=None
    for row in rows:row['voice_request']=edit.read(takes/(row['line_id']+'.json'))['request']
    edit.write(work/'prepared.json',{'title':profile.get('preview_title','参考声线：轻咬字与情绪转折'),'rows':rows})
    edit.render(trial_name)
    timeline=edit.read(work/'timeline.json');new=[];comparison=[];measurements=[]
    for row in timeline['rows']:
        line=row['line_id'];clip=Path(row['clip']);frames=row['end_frame']-row['start_frame']
        segment=dict(line_id=line,role=row['role'],clip=str(clip),audio=str(clip.with_suffix('.audio.wav')),frames=frames)
        new.append(segment);comparison.extend([base_segments[row['role']],segment])
        pcm=edit.read_pcm(takes/(line+'.wav'),1);audio=parselmouth.Sound(pcm.astype(float)/32768,sampling_frequency=edit.SR)
        measured=pitch_stats(audio);metadata=edit.read(takes/(line+'.json'))
        measurements.append(dict(line_id=line,role=row['role'],speaker=row['speaker'],text=row['text'],request=metadata['request'],
             reference_sources=metadata['sources'],
             instruction=metadata['request']['input']['instruction'],baseline=previous[row['role']],
             hanzi_per_second=len(re.findall(r'[\u4e00-\u9fff]',row['text']))/(len(pcm)/edit.SR),pitch=measured,
             started_utc=metadata.get('started_utc'),finished_utc=metadata.get('finished_utc')))
    out=edit.ROOT/'exports'/args.name/'声音试听'/args.label;out.mkdir(parents=True,exist_ok=True)
    preview=out/(profile.get('preview_title','角色参考声线·轻咬字情绪版')+'.mp4');frames=assemble(new,work,preview)
    compare=out/(profile.get('comparison_title','逐人对比·原参考版A到情绪版B')+'.mp4');compare_frames=assemble(comparison,work,compare)
    edit.write(out/'实测与请求.json',dict(profile=profile,workers=args.workers,lines=measurements,segments=new,comparison_segments=comparison,
          preview=str(preview),comparison=str(compare),frames=frames,comparison_frames=compare_frames,
          comparison_rule=profile.get('comparison_rule','Only instruction changes; text, cloned voice, rate, pitch, seed, format and sample rate match the preferred reference baseline.'),
          audio_postprocessing='Silence trimming, pauses, loudness and resampling only; no pitch or tempo processing.'))
    lines=['共同指令：'+profile['common'],'',profile.get('comparison_rule','每位只改instruction，其余模型、正文、参考声线ID、rate、pitch与seed均与上一版A参考声线相同。'),'']
    for r in measurements:lines.extend([r['speaker'],r['instruction'],f"实测 {r['hanzi_per_second']:.2f}汉字/秒，基频中位数{r['pitch']['median_hz']:.1f}Hz。",''])
    (out/'角色情绪指令.txt').write_text('\n'.join(lines),'utf-8-sig')
    print(preview,frames/25,flush=True)


if __name__=='__main__':main()
