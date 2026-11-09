"""Render an IRON dub sheet with an existing GPT-SoVITS v2 server."""
import argparse,csv,json,os,shutil
from collections import Counter
from pathlib import Path
from gpt_sovits import synthesize,voice_state

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lines',type=Path,default=Path(__file__).with_name('voice_lines.jsonl'))
    parser.add_argument('--config',type=Path,help='Server cast JSON with weights and reference audio paths')
    parser.add_argument('--out',type=Path,default=Path('wav'))
    parser.add_argument('--check-only',action='store_true',help='Check roles and edit windows without synthesis')
    args=parser.parse_args()
    lines=[json.loads(s) for s in args.lines.read_text('utf-8-sig').splitlines() if s]
    assert len(set(r['line_id'] for r in lines))==len(lines)
    for r in lines:
        assert r['start_ms']<r['end_ms'] and r['duration_ms']==r['end_ms']-r['start_ms']
        assert r['end_frame']*40==r['end_ms'] and Path(r['wav_name']).name==r['wav_name']
    assert all(b['start_frame']>=a['end_frame'] for a,b in zip(lines,lines[1:]))
    print('Voice lines:',len(lines),dict(Counter(r['character_id'] for r in lines)))
    if args.check_only:return
    if not args.config:parser.error('--config is required for synthesis')
    cfg=json.loads(args.config.read_text('utf-8-sig'))
    for role in {r['character_id'] for r in lines}:
        voice=cfg['voices'][role]
        for field in ('gpt_weights','sovits_weights','ref_audio_path'):
            voice[field]=str((args.config.resolve().parent/voice[field]).resolve())
        voice['aux_ref_audio_paths']=[str((args.config.resolve().parent/p).resolve()) for p in voice.get('aux_ref_audio_paths',[])]
        status=voice_state(cfg,role)
        if status['status']!='available':raise SystemExit(str(status))
    args.out.mkdir(parents=True,exist_ok=True)
    def export_report(results):
        dest=args.out/('durations_'+args.lines.stem+'.csv')
        temp=dest.with_suffix('.tmp')
        with temp.open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=['sequence','wav_name','start_timecode','end_timecode','available_ms','audio_ms','over_by_ms','status']);writer.writeheader();writer.writerows(results)
        temp.replace(dest)
    results=[]
    for r in lines:
        settings=cfg
        if r['line_id'] in cfg.get('pickup_line_ids',[]):
            settings={**cfg,'request_parameters':{**cfg['request_parameters'],**cfg['pickup_parameters']}}
        receipt=synthesize(settings,{**r,'text':r['tts_text']},args.out/'.cache')
        if receipt['status'] not in ('generated','cached'):raise SystemExit(f"{r['sequence']}: {receipt}")
        dur=round(receipt['duration_seconds']*1000)
        target=args.out/r['wav_name'];tmp=target.with_suffix('.tmp')
        shutil.copyfile(receipt['path'],tmp);os.replace(tmp,target)
        results.append({'sequence':r['sequence'],'wav_name':r['wav_name'],'start_timecode':r['start_timecode'],'end_timecode':r['end_timecode'],'available_ms':r['duration_ms'],'audio_ms':dur,'over_by_ms':max(0,dur-r['duration_ms']),'status':receipt['status']})
        export_report(results)
        print(f"{r['sequence']:03} {target.name}  {dur} ms",flush=True)
if __name__=='__main__':main()
