"""Produce the v3 remainder with MiniMax voices, thinking beats and dated map briefs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import math
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw
import requests

import session_edit as edit
import integrate_v3_opening as opening

sys.path.insert(0,str(edit.ROOT/'tools'))
from minimax_audio import load_key, synthesize

ROOT=edit.ROOT
BASE=ROOT/'runs/tyrol_new_table_20261110/edit'
WORK=ROOT/'runs/tyrol_new_table_20261110/v3_5_complete'
OUT=ROOT/'exports/tyrol_new_table_20261110/正片/v3.5全片重配与回合转场'
SCRIPT=ROOT/'exports/tyrol_new_table_20261110/配音稿/v3.2/Gemini-v3.2_完整纯配音稿.json'
FPS,SR,SPF=edit.FPS,edit.SR,edit.SPF
CAST={v:k for k,v in edit.NAMES.items()}
PAUSES={'L023':1.2,'L026':1.,'L031':1.3,'L035':1.2,'L036':1.,'L038':1.15,
        'L040':1.2,'L042':1.25,'L043':1.,'L045':1.5,'L046':1.6,'L049':1.4,
        'L051':1.,'L054':1.3,'L057':1.4,'L058':1.2,'L060':1.2,'L063':1.5,
        'M001':1.2,'M002':1.2}
RECAPS={
 'M001':'先把全局摊开。北边671还是奥地利，南边6673还是意大利。两家已经口头同意停火，德国承诺不进山口，瑞士守中立，法国追问生效时间。地图没换色，存档里奥意仍在交战。接下来要看的，是停火怎么核实，不是谁喊得更响。',
 'M002':'第五天，地图上的两面旗仍在原位。桌边的争点换了：瑞士已经接受山脊和垭口这条界线，法国还在等前沿番号，德国继续承诺不进山口。千问把初稿约在第七天，番号说下轮补。现在缺的，是能落纸、能核对的东西。'}
NEXT={0:('选国结束','五张国家牌，开始落地'),1:('第1回合结束','先停枪，还是先谈归属？'),
      2:('第2回合结束','线认下了，几点生效？'),3:('第3回合结束','口头签了，接着查证'),
      4:('第4回合结束','番号与初稿，谁先上桌？'),5:('第5回合结束','下轮到了，准数呢？'),
      6:('第6回合结束','第七天，等初稿上桌'),7:('第7回合结束','散场大会 · 每人重说一句')}


def prepare():
    for folder in (WORK,OUT,WORK/'takes',WORK/'clips',WORK/'layers'):folder.mkdir(parents=True,exist_ok=True)
    script=edit.read(SCRIPT)
    selected={r['id']:r for r in script['segments'] if not r['deleted'] and int(r['id'][1:4])>=13}
    rows=[]
    for source in edit.read(BASE/'timeline.json')['rows']:
        ident=source['line_id']
        if ident.startswith('L') and int(ident[1:])<13:continue
        if ident=='L061' or ident=='L050':continue
        row=copy.deepcopy(source)
        if ident in selected:
            part=selected[ident]
            assert row['role']==CAST[part['role']]
            row.update(text=part['selected_text'],spoken_text=part['spoken_text'],
                       speaker=edit.NAMES[row['role']],edit_tempo=1,
                       gap=PAUSES.get(ident,.76),stamp='v3改写 · 配音演绎')
            if row.get('round'):row['stamp']='同轮发言选编 · v3配音演绎'
            if row['role']=='gpt' and row.get('board'):row['board']=row['board'].replace('白龙 →','白龙酱 →')
            if row['kind']=='closing':
                row['stamp']='公开复盘改写 · 分别陈述' if int(ident[1:])<=56 else '公开散场接话 · v3配音演绎'
            if row['kind']=='narration':row['stamp']='v3剪辑旁白'
        elif source.get('text'):
            raise ValueError('Unmatched source speech: '+ident)
        if ident in RECAPS:
            row.update(kind='narration',style='brief',role='glm',speaker='智谱娘',
                       text=RECAPS[ident],spoken_text=re.sub(r'\d',lambda m:'零一二三四五六七八九'[int(m[0])],RECAPS[ident]),
                       gap=PAUSES[ident],stamp='剪辑补充讲解 · 依据公开记录')
            row['chapter']=rows[-1]['chapter']
        if ident=='L049':
            final=next(r for r in edit.read(BASE/'timeline.json')['rows'] if r['line_id']=='M003')
            row.update(style='brief',state_day=7,headline=final['headline'],status=final['status'],
                       footer=final['footer'],evidence=final['evidence'])
        if ident=='M003':continue  # The final narrated brief already presents these facts.
        row['clip']=str(WORK/'clips'/(ident+'.mkv'))
        rows.append(row)
        day_end={'L017':0,'L023':1,'L027':2,'M001':3,'L036':4,'M002':5,'L042':6,'L049':7}.get(ident)
        if day_end is not None:
            title,question=NEXT[day_end]
            rows.append({'line_id':f'END{day_end}','kind':'transition','style':'round_transition',
                         'day':day_end,'plate':'initial' if day_end==0 else day_end,
                         'chapter':row['chapter'],'headline':title,'board':question,
                         'seconds':2.0 if day_end==7 else 1.6,'text':'',
                         'clip':str(WORK/'clips'/f'END{day_end}.mkv')})
    assert set(selected)=={r['line_id'] for r in rows if r['line_id'] in selected}
    edit.write(WORK/'selection.json',{'source_script':str(SCRIPT),'rows':rows,
        'policy':'Approved v3 script; native word subtitles; 0.76–1.6s speech-end beats; all seven daily turns have a transition; new GLM briefs after turns 3 and 5, final readback after turn 7.'})
    (WORK/'后半段纯配音稿.txt').write_text('\n\n'.join(
        f'[{r["line_id"]}｜{r.get("speaker","")}]\n{r["text"]}' for r in rows if r.get('text'))+'\n','utf-8-sig')
    script_out=ROOT/'exports/tyrol_new_table_20261110/配音稿/v3.5';script_out.mkdir(parents=True,exist_ok=True)
    full_script=[{'id':r['id'],'speaker':r['role'],'text':r['selected_text'],'spoken_text':r['spoken_text']}
                 for r in script['segments'] if not r['deleted'] and int(r['id'][1:4])<13]
    full_script.extend({'id':r['line_id'],'speaker':r['speaker'],'text':r['text'],
                        'spoken_text':r['spoken_text'],'evidence':r.get('evidence',[])} for r in rows if r.get('text'))
    edit.write(script_out/'完整纯配音稿.json',{'version':'v3.5','source':str(SCRIPT),'segments':full_script})
    (script_out/'完整纯配音稿.txt').write_text('\n\n'.join(
        f'[{r["id"]}｜{r["speaker"]}]\n{r["text"]}' for r in full_script)+'\n','utf-8-sig')
    return rows


def take(key,row,speed,directory):
    audio=directory/(row['line_id']+'.mp3')
    body={'model':'speech-2.8-turbo','text':row['spoken_text'],'stream':False,
          'language_boost':'Chinese','output_format':'hex',
          'voice_setting':{'voice_id':edit.read(ROOT/'configs/tts.minimax.cast.json')['roles'][row['role']]['voice_id'],
                           'speed':speed,'vol':1,'pitch':0},
          'audio_setting':{'sample_rate':32000,'bitrate':128000,'format':'mp3','channel':1},
          'subtitle_enable':True,'subtitle_type':'word'}
    receipt=synthesize(key,body,audio,'io')
    subtitle=audio.with_suffix('.subtitles.json')
    if not subtitle.exists():
        url=receipt['response'].get('data',{}).get('subtitle_file','')
        if not url.startswith('https://'):raise RuntimeError('Missing native subtitle URL: '+row['line_id'])
        response=requests.get(url,timeout=(15,45));response.raise_for_status();edit.write(subtitle,response.json())
    if not edit.read(subtitle)[0].get('timestamped_words'):raise RuntimeError('Missing word timestamps')
    pcm=opening.pcm_read(audio);hanzi=len(re.findall(r'[\u4e00-\u9fff]',row['spoken_text']))
    return {'id':row['line_id'],'role':row['role'],'selected_audio':str(audio),
            'native_subtitles':str(subtitle),'duration_seconds':len(pcm)/SR,'hanzi':hanzi,
            'hanzi_per_second':hanzi/(len(pcm)/SR),'speed':speed,
            'usage_characters':receipt['response']['extra_info']['usage_characters']}


def synthesize_rows(rows):
    key=load_key()
    if not key:raise RuntimeError('MINIMAX_API_KEY missing')
    baseline=edit.read(ROOT/'runs/tyrol_new_table_20261110/v3_2_pacing/selected_audio_validation.json')['segments']
    speeds={role:round(statistics.median(r['speed']*4/r['hanzi_per_second'] for r in baseline
                                       if (r['id'].startswith('L004') if role=='glm' else r['id']=={'deepseek':'L006','gpt':'L007','gemini':'L008','claude':'L009','qwen':'L010'}[role])),2)
            for role in edit.NAMES}
    spoken=[r for r in rows if r.get('text')]
    pilots=[next(r for r in spoken if r['role']==role) for role in edit.NAMES]
    results={};attempts=[]
    def run(row,speed,directory=WORK/'takes'):
        result=take(key,row,speed,directory)
        return result
    # Prove the current API/voice route with one request before the bounded batch.
    first=pilots[0];result=run(first,speeds[first['role']]);results[result['id']]=result;attempts.append(result)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for start in range(1,len(pilots),3):
            futures=[pool.submit(run,r,speeds[r['role']]) for r in pilots[start:start+3]]
            for future in futures:
                result=future.result();results[result['id']]=result;attempts.append(result)
    for row in pilots:
        result=results[row['line_id']]
        speeds[row['role']]=round(min(2,max(.5,result['speed']*4/result['hanzi_per_second'])),2)
    edit.write(WORK/'pilot_validation.json',{'segments':list(results.values()),'calibrated_speeds':speeds})
    pending=[r for r in spoken if r['line_id'] not in results]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for start in range(0,len(pending),3):
            futures=[pool.submit(run,r,speeds[r['role']]) for r in pending[start:start+3]]
            for future in futures:
                result=future.result();results[result['id']]=result;attempts.append(result)
            edit.write(WORK/'synthesis_checkpoint.json',{'segments':list(results.values())})
    # One measured correction only, never change the old waveform's rate or pitch.
    corrections=[r for r in spoken if results[r['line_id']]['hanzi']>=25 and
                 not 3.4<=results[r['line_id']]['hanzi_per_second']<=4.6]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for start in range(0,len(corrections),3):
            futures=[]
            for row in corrections[start:start+3]:
                old=results[row['line_id']];speed=round(min(2,max(.5,old['speed']*4/old['hanzi_per_second'])),2)
                futures.append(pool.submit(run,row,speed,WORK/'retake1'))
            for future in futures:
                new=future.result();attempts.append(new);old=results[new['id']]
                if abs(new['hanzi_per_second']-4)<abs(old['hanzi_per_second']-4):results[new['id']]=new
    report={'model':'speech-2.8-turbo','segments':[results[r['line_id']] for r in spoken],
            'attempts':attempts,'tts_calls':len(attempts),'usage_characters':sum(r['usage_characters'] for r in attempts),
            'new_briefs':list(RECAPS),'audio_policy':'New synthesis; pitch 0; no post tempo/pitch processing; native TTS word timestamps.'}
    edit.write(WORK/'selected_audio_validation.json',report)
    return report


def brief_image(row,caption,talking):
    # Keep the military focus large, with the host outside the front-line image.
    plate=edit.read(BASE/'map_plates.json')[str(row['plate'])]
    im=Image.new('RGBA',(1280,720),'#10232d');d=ImageDraw.Draw(im)
    d.text((28,24),row['headline'],font=edit.font(36,True),fill='#f4dfad')
    d.text((29,77),row['date_label'],font=edit.font(22),fill='#c2d4d3')
    actual=Image.open(plate['file']).convert('RGBA').crop((150,65,1100,600)).resize((840,410),Image.Resampling.LANCZOS)
    im.alpha_composite(actual,(28,127));d=ImageDraw.Draw(im)
    d.rectangle((28,127,868,164),fill='#193541')
    d.text((42,132),'山口近景 · 对应日期游戏实录',font=edit.font(21,True),fill='#edf2e6')
    for i,(label,value) in enumerate(row['status']):
        y=127+i*71
        d.text((900,y),label,font=edit.font(19,True),fill='#8dbfb9')
        for j,line in enumerate(edit.wrap(value,23,353)):
            d.text((900,y+26+j*28),line,font=edit.font(23,True),fill='#fff4da')
    art=edit.portrait('glm',talking,217);im.alpha_composite(art,(round(1070-art.width/2),560-art.height))
    d=ImageDraw.Draw(im)
    for x,text,color in [(43,'北 671 · 奥地利控制','#efc66d'),(469,'南 6673 · 意大利控制','#8bddbf')]:
        d.rectangle((x-8,493,x+380,532),fill='#193541')
        d.text((x,497),text,font=edit.font(24,True),fill=color)
    d.rectangle((0,547,1279,719),fill='#132b32');d.rectangle((0,547,1279,550),fill='#bfa374')
    d.text((38,559),'智谱娘 / 全局梳理',font=edit.font(22,True),fill='#edcf8c')
    d.text((1240,565),'剪辑旁白 · 公开记录',font=edit.font(18),fill='#bfcfcf',anchor='rt')
    for j,line in enumerate(edit.wrap(caption,33,1200)):
        if j>=2:raise ValueError('Brief subtitle too long: '+caption)
        d.text((38,603+j*43),line,font=edit.font(33,True),fill='#fff7e5')
    return im.convert('RGB')


def transition_image(row):
    plate=edit.read(BASE/'map_plates.json')[str(row['plate'])]
    im=Image.open(plate['file']).convert('RGBA').resize((1280,720))
    im=Image.alpha_composite(im,Image.new('RGBA',im.size,(8,25,34,210)))
    d=ImageDraw.Draw(im)
    d.line((480,264,800,264),fill='#8dbfb9',width=3)
    d.text((640,319),row['headline'],font=edit.font(60,True),fill='#f4dfad',anchor='mm')
    d.text((640,402),row['board'],font=edit.font(32),fill='#e0ebe5',anchor='mm')
    year,month,day,hour=map(int,plate['date'].split('.'))
    d.text((640,590),f'{year:04}.{month:02}.{day:02} {hour:02}时 · 回合结束',font=edit.font(21),fill='#a9bfbd',anchor='mm')
    return im.convert('RGB')


def render(rows,report):
    takes={r['id']:r for r in report['segments']};position=0;cues=[]
    for row in rows:
        ident=row['line_id'];target=Path(row['clip'])
        if row['kind']=='game':
            source=next(r for r in edit.read(BASE/'timeline.json')['rows'] if r['line_id']==ident)
            frames=source['end_frame']-source['start_frame']
            shutil.copy2(source['clip'],target);shutil.copy2(Path(source['clip']).with_suffix('.audio.wav'),target.with_suffix('.audio.wav'))
            local=[];n=0;gap=frames
        else:
            art=WORK/'layers'/ident;art.mkdir(exist_ok=True)
            if row['style']=='brief':edit.situation_image(row,BASE)  # Verify this dated state once per brief.
            if row.get('text'):
                selected=takes[ident];pcm=opening.pcm_read(selected['selected_audio'])
                n=math.ceil(len(pcm)/SPF);pcm=np.pad(pcm,(0,n*SPF-len(pcm)))
                assert len(row['text'])==len(row['spoken_text']),ident
                parts=edit.caption_parts(row['text']);bounds=opening.native_bounds(parts,edit.read(selected['native_subtitles']),n)
                local=[[a,b,text] for a,b,text in zip(bounds,bounds[1:],parts)]
                gap=round(row['gap']*FPS);frames=n+gap
                energy=np.sqrt((pcm.reshape(-1,SPF).astype(float)**2).mean(axis=1))
                opening.save_wav(art/'voice.wav',np.concatenate([pcm,np.zeros(gap*SPF,dtype='<i2')]))
                row.update(approved_audio=selected['selected_audio'],native_subtitles=selected['native_subtitles'],
                           measured_hanzi_per_second=selected['hanzi_per_second'])
            else:
                frames=round(row['seconds']*FPS);n=0;gap=frames;local=[]
            receipt=target.with_suffix('.json')
            if not (target.exists() and receipt.exists() and edit.read(receipt)['row']==row):
                if row['kind']=='transition':
                    transition_image(row).save(art/'transition.png')
                    edit.ff(['-loop','1','-i',art/'transition.png','-t',frames/FPS,
                             '-vf',f'fps=25,fade=t=in:d=0.16,fade=t=out:st={(frames-4)/FPS}:d=0.16,format=yuv420p,setsar=1',
                             '-an','-c:v','libx264','-threads','4','-preset','veryfast','-crf','19',target])
                else:
                    painter=brief_image if row['style']=='brief' else lambda r,c,t:edit.frame_image(r,c,t,BASE)
                    pieces=[]
                    def add(filename,count):
                        if count<=0:return
                        if pieces and pieces[-1][0]==filename:pieces[-1]=(filename,pieces[-1][1]+count)
                        else:pieces.append((filename,count))
                    for j,(a,b,caption) in enumerate(local):
                        for mouth in (False,True):painter(row,caption,mouth).save(art/f'{j}-{int(mouth)}.png',compress_level=2)
                        for f in range(a,b,4):
                            mouth=bool(energy[f]>250 and (f//4)%3!=2)
                            add(f'{j}-{int(mouth)}.png',min(4,b-f))
                    painter(row,'',False).save(art/'idle.png',compress_level=2);add('idle.png',gap)
                    listing='ffconcat version 1.0\n'+''.join(f"file '{file}'\nduration {count/FPS:.2f}\n" for file,count in pieces)+f"file '{pieces[-1][0]}'\n"
                    (art/'frames.ffconcat').write_text(listing,'ascii')
                    edit.ff(['-f','concat','-safe','0','-i',art/'frames.ffconcat','-vf','fps=25,format=yuv420p,setsar=1',
                             '-an','-frames:v',frames,'-c:v','libx264','-threads','4','-preset','veryfast','-crf','19',target])
                audio=target.with_suffix('.audio.wav')
                edit.complete_audio(art/'voice.wav' if row.get('text') else None,audio,frames)
                temp=target.with_suffix('.complete.mkv')
                edit.ff(['-i',target,'-i',audio,'-map','0:v:0','-map','1:a:0','-c','copy',temp]);temp.replace(target)
                edit.write(receipt,{'row':row,'frames':frames,'audio_samples':frames*1920})
        row.update(start_frame=position,end_frame=position+frames,audio_frames=n,gap_frames=gap,cue_local=local)
        cues.extend([position+a,position+b,text] for a,b,text in local)
        position+=frames
        print('RENDERED',ident,round(position/FPS,2),flush=True)
    result={'fps':FPS,'frames':position,'rows':rows,'cues':cues}
    edit.write(WORK/'remainder_timeline.json',result)
    return result


def assemble(timeline):
    opening.WORK=WORK
    tail=opening.compose(timeline['rows'],timeline['cues'],OUT/'选国到散场·MiniMax2.8.mp4','remainder')
    approved=ROOT/'exports/tyrol_new_table_20261110/正片/v3.4配乐版/开场与六人入场·配乐版.mp4'
    opening_master=ROOT/'runs/tyrol_new_table_20261110/v3_4_bgm/opening.mixed.wav'
    clip=WORK/'clips/APPROVED_OPENING.mkv'
    edit.ff(['-i',approved,'-i',opening_master,'-map','0:v:0','-map','1:a:0','-c','copy',clip])
    first=edit.read(ROOT/'runs/tyrol_new_table_20261110/v3_3_signal_break/opening_timeline.json')
    offset=first['frames'];rows=copy.deepcopy(timeline['rows'])
    for row in rows:row['start_frame']+=offset;row['end_frame']+=offset
    all_rows=first['rows']+rows
    cues=first['cues']+[[a+offset,b+offset,text] for a,b,text in timeline['cues']]
    frames=offset+timeline['frames']
    master=WORK/'full.mixed.wav'
    with wave.open(str(master),'wb') as out:
        out.setparams((2,2,48000,0,'NONE','not compressed'))
        for source in (opening_master,WORK/'remainder.normalized.wav'):
            with wave.open(str(source),'rb') as audio:
                while data:=audio.readframes(48000):out.writeframesraw(data)
    assert round(float(edit.probe(master)['format']['duration'])*FPS)==frames
    listing=WORK/'full.ffconcat'
    listing.write_text("ffconcat version 1.0\nfile '"+clip.as_posix()+"'\n"+''.join(
        "file '"+Path(r['clip']).as_posix()+"'\n" for r in rows),'utf8')
    chapters=[]
    for row in all_rows:
        if not chapters or chapters[-1][1]!=row['chapter']:chapters.append((row['start_frame'],row['chapter']))
    meta=[';FFMETADATA1']
    for (a,title),b in zip(chapters,[x[0] for x in chapters[1:]]+[frames]):
        meta.extend(['[CHAPTER]','TIMEBASE=1/25',f'START={a}',f'END={b}','title='+title])
    (WORK/'full.chapters.txt').write_text('\n'.join(meta)+'\n','utf8')
    target=OUT/'这份初稿，下轮一定·全片MiniMax2.8与回合转场.mp4'
    edit.ff(['-f','concat','-safe','0','-i',listing,'-i',master,'-f','ffmetadata','-i',WORK/'full.chapters.txt',
             '-map','0:v:0','-map','1:a:0','-map_metadata','2','-map_chapters','2',
             '-c:v','copy','-c:a','aac','-b:a','192k','-movflags','+faststart',target])
    target.with_suffix('.srt').write_text('\n\n'.join(f'{i}\n{edit.srt_time(a)} --> {edit.srt_time(b)}\n{text}'
        for i,(a,b,text) in enumerate(cues,1))+'\n','utf-8-sig')
    edit.write(target.with_suffix('.timeline.json'),{'fps':FPS,'frames':frames,'rows':all_rows,'cues':cues})
    report={'episode':str(target),'remainder':tail,'seconds':frames/FPS,'opening_seconds':offset/FPS,
            'opening_preserved':'v3.4 video and PCM mix, no further normalization','new_bgm_after_opening':False,
            'daily_transitions':7,'new_glm_briefs':['M001','M002'],'final_glm_readback':'L049',
            'script':str(SCRIPT),'deleted_lines':['L061'],'voice_model':'speech-2.8-turbo'}
    edit.write(OUT/'交付.json',report)
    print('DELIVERED',target,frames/FPS,flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['prepare','voices','render','all'],default='all')
    args=parser.parse_args();rows=prepare()
    if args.stage=='prepare':return
    report=synthesize_rows(rows) if args.stage in ('voices','all') else edit.read(WORK/'selected_audio_validation.json')
    if args.stage=='voices':return
    assemble(render(rows,report))


if __name__=='__main__':main()
