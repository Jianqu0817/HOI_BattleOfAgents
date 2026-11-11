"""Insert the reviewed 20 GLM inner monologues, preserving existing dialogue."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
from functools import lru_cache
import math
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import wave

import numpy as np
from PIL import Image, ImageDraw

import session_edit as edit
import integrate_v3_opening as opening
import continue_v3_episode as episode
from add_episode_bgm import playlist
from add_opening_bgm import mix, write_wave
from minimax_audio import load_key

ROOT=edit.ROOT
WORK=ROOT/'runs/tyrol_new_table_20261110/v3_7_glm_inner'
OUT=ROOT/'exports/tyrol_new_table_20261110/正片/v3.7二十段内心解说'
SCRIPT_OUT=ROOT/'exports/tyrol_new_table_20261110/配音稿/v3.7'
SOURCE=ROOT/'exports/tyrol_new_table_20261110/正片/v3.6后半段轮播配乐/这份初稿，下轮一定·四曲轮播配乐版.mp4'
MASTER=ROOT/'runs/tyrol_new_table_20261110/v3_5_complete/full.mixed.wav'
FPS,SR,SPF=edit.FPS,edit.SR,edit.SPF


def prepare(markdown):
    for p in (WORK,OUT,SCRIPT_OUT,WORK/'source',WORK/'takes',WORK/'clips',WORK/'layers'):p.mkdir(parents=True,exist_ok=True)
    original=markdown.read_text('utf-8-sig')
    archive=WORK/'source'/markdown.name
    if archive.resolve()!=markdown.resolve():shutil.copy2(markdown,archive)
    fixed=original.replace('龙娘酱','白龙酱')
    (SCRIPT_OUT/'GLM内心解说与吐槽_20段接片稿.md').write_text(fixed,'utf8')
    entries=[]
    for number,title,body in re.findall(r'^### (\d{2})｜([^\n]+)\n(.*?)(?=^### |^## |\Z)',fixed,re.M|re.S):
        anchor=re.search(r'\*\*锚点\*\*：`([^`]+)`',body)[1]
        mode=re.search(r'\*\*方式\*\*：([^\n]+)',body)[1].strip()
        text=body.split('**文案：**',1)[1].strip()
        assert '\n\n' not in text,'Unexpected additional prose in spoken field'
        entries.append({'line_id':'INNER'+number,'number':int(number),'title':title.strip(),
                        'anchor':anchor,'mode':mode,'role':'glm','speaker':'智谱娘',
                        'text':text,'spoken_text':re.sub(r'\d',lambda m:'零一二三四五六七八九'[int(m[0])],text)})
    assert [e['number'] for e in entries]==list(range(1,21))
    assert [e['anchor'] for e in entries if e['mode']=='替换']==['M001','M002','L049']
    assert next(e for e in entries if e['number']==20)['anchor']=='L057'
    base=edit.read(SOURCE.with_suffix('.timeline.json'));by_id={r['line_id']:r for r in base['rows']}
    for e in entries:
        assert e['anchor'] in by_id
        parent=by_id[e['anchor']]
        e.update(chapter=parent['chapter'],date_label=parent.get('date_label',''),
                 backdrop='map' if parent.get('style') in ('map','brief','result') else
                          'closing' if parent.get('kind')=='closing' else 'table',
                 plate=parent.get('plate'),clip=str(WORK/'clips'/(e['line_id']+'.mkv')))
    report={'source':str(archive),'review':'20 anchors and facts retained; two character-name corrections to 白龙酱.',
            'name_corrections':original.count('龙娘酱'),'entries':entries}
    edit.write(WORK/'reviewed_script.json',report);edit.write(SCRIPT_OUT/'GLM内心解说_接片表.json',report)
    return entries,base


def voices(entries):
    key=load_key()
    if not key:raise RuntimeError('MINIMAX_API_KEY missing')
    baseline=edit.read(episode.WORK/'selected_audio_validation.json')['segments']
    speed=round(statistics.median(r['speed']*4/r['hanzi_per_second'] for r in baseline if r['role']=='glm'),2)
    first=episode.take(key,entries[0],speed,WORK/'takes')
    selected={first['id']:first};attempts=[first]
    speed=round(min(2,max(.5,speed*4/first['hanzi_per_second'])),2)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for i in range(1,len(entries),3):
            futures=[pool.submit(episode.take,key,e,speed,WORK/'takes') for e in entries[i:i+3]]
            for future in futures:
                result=future.result();selected[result['id']]=result;attempts.append(result)
            edit.write(WORK/'synthesis_checkpoint.json',{'segments':list(selected.values())})
    off=[e for e in entries if not 3.4<=selected[e['line_id']]['hanzi_per_second']<=4.6]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for i in range(0,len(off),3):
            futures=[]
            for e in off[i:i+3]:
                old=selected[e['line_id']];rate=round(min(2,max(.5,old['speed']*4/old['hanzi_per_second'])),2)
                futures.append(pool.submit(episode.take,key,e,rate,WORK/'retake1'))
            for future in futures:
                new=future.result();attempts.append(new)
                if abs(new['hanzi_per_second']-4)<abs(selected[new['id']]['hanzi_per_second']-4):selected[new['id']]=new
    report={'segments':[selected[e['line_id']] for e in entries],'attempts':attempts,
            'calls':len(attempts),'usage_characters':sum(a['usage_characters'] for a in attempts),
            'model':'speech-2.8-turbo','pitch':0,'post_tempo_or_pitch_processing':False}
    edit.write(WORK/'selected_audio_validation.json',report)
    return report


@lru_cache(30)
def clean_background(backdrop,plate):
    if backdrop=='map':
        p=edit.read(episode.BASE/'map_plates.json')[str(plate)]
        return Image.open(p['file']).convert('RGB').resize((1280,720))
    return edit.background('closing' if backdrop=='closing' else 'table').convert('RGB')


def inner_image(row,caption,talking):
    clean=clean_background(row['backdrop'],row.get('plate')).convert('RGBA')
    im=Image.alpha_composite(clean,Image.new('RGBA',clean.size,(0,0,0,56)))
    art=edit.portrait('glm',talking,440)
    im.alpha_composite(art,(round(640-art.width/2),540-art.height))
    d=ImageDraw.Draw(im)
    d.rounded_rectangle((27,24,27+edit.font(24,True).getlength(row['chapter'])+36,68),9,fill='#132b32')
    d.text((45,30),row['chapter'],font=edit.font(24,True),fill='#f0d899')
    d.rounded_rectangle((1080,25,1253,65),8,fill='#132b32')
    d.text((1166,45),'内心解说',font=edit.font(25,True),fill='#f4dfad',anchor='mm')
    date=row.get('date_label','')
    if date:
        d.rounded_rectangle((28,82,54+edit.font(20).getlength(date),117),6,fill='#132b32')
        d.text((41,87),date,font=edit.font(20),fill='#e8e5d5')
    if row['backdrop']=='map':
        for x,text,color in [(53,'北 671 · 奥地利控制','#efc66d'),(907,'南 6673 · 意大利控制','#8bddbf')]:
            d.rounded_rectangle((x-14,194,x+323,248),8,fill='#132b32')
            d.text((x,207),text,font=edit.font(25,True),fill=color)
    d.rectangle((0,520,1279,719),fill='#41352f');d.rectangle((0,520,1279,523),fill='#bfa374')
    d.rounded_rectangle((36,533,1244,700),13,fill='#132b32',outline='#839c92',width=2)
    d.text((67,545),'智谱娘 / 内心解说',font=edit.font(23,True),fill='#edcf8c')
    lines=edit.wrap(caption,36,1120)
    if len(lines)>2:raise ValueError('Inner caption too long: '+caption)
    y=595 if len(lines)==1 else 580
    for j,line in enumerate(lines):d.text((67,y+j*45),line,font=edit.font(36,True),fill='#fff7e5')
    return im.convert('RGB')


def map_hold_image(parent):
    im=clean_background('map',parent['plate']).copy();d=ImageDraw.Draw(im)
    for y,text in [(25,parent['date_label']),(78,'671 奥地利控制 / 6673 意大利控制')]:
        width=edit.font(23,True).getlength(text)+32
        d.rounded_rectangle((27,y,27+width,y+43),7,fill='#132b32')
        d.text((43,y+6),text,font=edit.font(23,True),fill='#f4dfad')
    return im


def still_clip(image,path,frames):
    image_path=path.with_suffix('.png');image.save(image_path)
    edit.ff(['-loop','1','-i',image_path,'-vf','fps=25,format=yuv420p,setsar=1','-an',
             '-frames:v',frames,'-c:v','libx264','-threads','4','-preset','veryfast','-crf','19',path])


def render_inner(entries,report):
    takes={r['id']:r for r in report['segments']}
    for row in entries:
        selected=takes[row['line_id']];target=Path(row['clip']);receipt=target.with_suffix('.json')
        art=WORK/'layers'/row['line_id'];art.mkdir(exist_ok=True)
        pcm=opening.pcm_read(selected['selected_audio']);n=math.ceil(len(pcm)/SPF)
        pcm=np.pad(pcm,(0,n*SPF-len(pcm)));lead=5;gap=19;frames=lead+n+gap
        parts=edit.caption_parts(row['text']);bounds=opening.native_bounds(parts,edit.read(selected['native_subtitles']),n)
        local=[[a+lead,b+lead,text] for a,b,text in zip(bounds,bounds[1:],parts)]
        if not (target.exists() and receipt.exists() and edit.read(receipt)['selected']==selected):
            energy=np.sqrt((pcm.reshape(-1,SPF).astype(float)**2).mean(axis=1))
            pieces=[]
            def add(file,count):
                if count<=0:return
                if pieces and pieces[-1][0]==file:pieces[-1]=(file,pieces[-1][1]+count)
                else:pieces.append((file,count))
            clean=clean_background(row['backdrop'],row.get('plate'))
            idle=inner_image(row,'',False);idle.save(art/'idle.png',compress_level=2)
            for f in range(5):
                name=f'in{f}.png';Image.blend(clean,idle,(f+1)/5).save(art/name,compress_level=2);add(name,1)
            for j,(a,b,caption) in enumerate(local):
                for mouth in (False,True):inner_image(row,caption,mouth).save(art/f'{j}-{int(mouth)}.png',compress_level=2)
                for f in range(a-lead,b-lead,4):
                    mouth=bool(energy[f]>250 and (f//4)%3!=2)
                    add(f'{j}-{int(mouth)}.png',min(4,b-lead-f))
            add('idle.png',gap-5)
            for f in range(5):
                name=f'out{f}.png';Image.blend(idle,clean,(f+1)/5).save(art/name,compress_level=2);add(name,1)
            assert sum(n for _,n in pieces)==frames
            listing='ffconcat version 1.0\n'+''.join(f"file '{file}'\nduration {count/FPS:.2f}\n" for file,count in pieces)+f"file '{pieces[-1][0]}'\n"
            (art/'frames.ffconcat').write_text(listing,'ascii')
            opening.save_wav(art/'voice.wav',np.concatenate([np.zeros(lead*SPF,dtype='<i2'),pcm,np.zeros(gap*SPF,dtype='<i2')]))
            edit.ff(['-f','concat','-safe','0','-i',art/'frames.ffconcat','-vf','fps=25,format=yuv420p,setsar=1',
                     '-an','-frames:v',frames,'-c:v','libx264','-threads','4','-preset','veryfast','-crf','19',target])
            audio=target.with_suffix('.audio.wav')
            edit.ff(['-i',art/'voice.wav','-af',f'loudnorm=I=-16:TP=-2:LRA=11,aresample=48000,aformat=channel_layouts=stereo,apad,atrim=end_sample={frames*1920}',
                     '-c:a','pcm_s16le',audio])
            temp=target.with_suffix('.complete.mkv')
            edit.ff(['-i',target,'-i',audio,'-map','0:v:0','-map','1:a:0','-c','copy',temp]);temp.replace(target)
            edit.write(receipt,{'selected':selected,'row':row,'frames':frames,'cue_local':local})
        row.update(kind='inner',frames=frames,audio_frames=lead+n,gap_frames=gap,
                   cue_local=local,approved_audio=selected['selected_audio'])
        print('INNER_RENDERED',row['line_id'],frames/FPS,flush=True)


def build(entries,base):
    by_anchor={r['anchor']:r for r in entries};pieces=[]
    def source(row,a=0,b=None,ident=None):
        total=row['end_frame']-row['start_frame'];b=total if b is None else b
        item=copy.deepcopy(row)
        item.update(line_id=ident or row['line_id'],kind='source',frames=b-a,
                    source_kind=row['kind'],source_global_a=row['start_frame']+a,source_global_b=row['start_frame']+b,
                    source_local_a=a,source_local_b=b)
        if a or b!=total:
            target=WORK/'clips'/(item['line_id']+'.mkv')
            edit.ff(['-i',row['clip'],'-vf',f'trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS',
                     '-an','-frames:v',b-a,'-c:v','libx264','-threads','4','-preset','veryfast','-crf','19',target])
            item['clip']=str(target)
        pieces.append(item)
    def pause(row,count,ident):
        target=WORK/'clips'/(ident+'.mkv')
        still_clip(edit.frame_image(row,'',False,episode.BASE),target,count)
        pieces.append({'line_id':ident,'kind':'pause','chapter':row['chapter'],'frames':count,'clip':str(target),'cue_local':[]})
    for row in base['rows']:
        inner=by_anchor.get(row['line_id'])
        if not inner:
            source(row);continue
        if inner['mode']=='句内插入':
            cut=next(b for a,b,text in row['cue_local'] if '是我自己把控制和归属搅一块儿了' in text)
            pcm=opening.pcm_read(row['approved_audio']);pcm=np.pad(pcm,(0,math.ceil(len(pcm)/SPF)*SPF-len(pcm)))
            cut=opening.quiet_cut(pcm,cut)
            edit.write(WORK/'L057_split.json',{'local_frame':cut,'local_seconds':cut/FPS,'original_frames':row['end_frame']-row['start_frame'],
                                            'policy':'Quiet frame immediately before 不过千问; every original sample retained on the two sides.'})
            source(row,0,cut,'L057a');pause(row,38,'L057_THINK_PAUSE');pieces.append(inner)
            source(row,cut,None,'L057b')
        elif inner['mode']=='替换':
            edit.situation_image(row,episode.BASE)
            pieces.append(inner)
            target=WORK/'clips'/(inner['line_id']+'_MAP_HOLD.mkv')
            still_clip(map_hold_image(row),target,50)
            pieces.append({'line_id':inner['line_id']+'_MAP_HOLD','kind':'map_hold','chapter':row['chapter'],
                           'frames':50,'clip':str(target),'cue_local':[],'plate':row['plate'],'date_label':row['date_label']})
        else:
            source(row)
            desired=38 if inner['number'] in (8,17) else 20
            extra=max(0,desired-row.get('gap_frames',0))
            if extra:pause(row,extra,inner['line_id']+'_PRE_PAUSE')
            pieces.append(inner)
    position=0;cues=[]
    for piece in pieces:
        piece.update(start_frame=position,end_frame=position+piece['frames'])
        if piece['kind']=='source':
            a=piece['source_global_a'];b=piece['source_global_b']
            cues.extend([position+max(x,a)-a,position+min(y,b)-a,text] for x,y,text in base['cues'] if x<b and y>a)
        else:cues.extend([position+a,position+b,text] for a,b,text in piece.get('cue_local',[]))
        position+=piece['frames']
    timeline={'fps':FPS,'frames':position,'rows':pieces,'cues':cues}
    edit.write(WORK/'timeline.json',timeline)
    return timeline


def assemble(timeline):
    master=WORK/'dialogue_and_opening_bgm.wav'
    with wave.open(str(MASTER),'rb') as original,wave.open(str(master),'wb') as out:
        out.setparams(original.getparams())
        for row in timeline['rows']:
            count=row['frames']*1920
            if row['kind']=='source':
                original.setpos(row['source_global_a']*1920)
                remaining=count
                while remaining:
                    n=min(48000,remaining);data=original.readframes(n);assert len(data)==n*4;out.writeframesraw(data);remaining-=n
            elif row['kind']=='inner':
                with wave.open(str(Path(row['clip']).with_suffix('.audio.wav')),'rb') as audio:
                    assert audio.getnframes()==count
                    while data:=audio.readframes(48000):out.writeframesraw(data)
            else:out.writeframesraw(bytes(count*4))
    with wave.open(str(master),'rb') as audio:assert audio.getnframes()==timeline['frames']*1920
    music_sources=[Path(p) for p in edit.read(ROOT/'runs/tyrol_new_table_20261110/v3_6_playlist_bgm/mix_report.json')['playlist_order']]
    music_offset=5913*1920;bed,entries=playlist(music_sources,timeline['frames']*1920-music_offset)
    write_wave(WORK/'playlist_stem.wav',bed)
    metrics=mix(master,WORK/'full.mixed.wav',bed,music_offset,np.zeros((0,2),dtype='float32'),0)
    listing=WORK/'full.ffconcat'
    listing.write_text('ffconcat version 1.0\n'+''.join("file '"+Path(r['clip']).as_posix()+"'\n" for r in timeline['rows']),'utf8')
    chapters=[]
    for row in timeline['rows']:
        if not chapters or chapters[-1][1]!=row['chapter']:chapters.append((row['start_frame'],row['chapter']))
    meta=[';FFMETADATA1']
    for (a,title),b in zip(chapters,[x[0] for x in chapters[1:]]+[timeline['frames']]):
        meta.extend(['[CHAPTER]','TIMEBASE=1/25',f'START={a}',f'END={b}','title='+title])
    (WORK/'chapters.txt').write_text('\n'.join(meta)+'\n','utf8')
    target=OUT/'这份初稿，下轮一定·20段GLM内心解说版.mp4'
    edit.ff(['-f','concat','-safe','0','-i',listing,'-i',WORK/'full.mixed.wav','-f','ffmetadata','-i',WORK/'chapters.txt',
             '-map','0:v:0','-map','1:a:0','-map_metadata','2','-map_chapters','2','-c:v','copy',
             '-c:a','aac','-b:a','192k','-metadata:s:a:0','language=zho','-movflags','+faststart',target])
    target.with_suffix('.srt').write_text('\n\n'.join(f'{i}\n{edit.srt_time(a)} --> {edit.srt_time(b)}\n{text}'
        for i,(a,b,text) in enumerate(timeline['cues'],1))+'\n','utf-8-sig')
    edit.write(target.with_suffix('.timeline.json'),timeline)
    report={'video':str(target),'seconds':timeline['frames']/FPS,'inner_count':20,'insertions':17,'replacements':3,
            'original_dialogue':'Canonical v3.5 PCM preserved, except the three replaced map narrations; L057 split without losing samples.',
            'look':'Clean same-scene background darkened by 56/255; one bright GLM centered at x640, height440; 0.2s entrances/exits.',
            'map_holds':3,'map_hold_seconds':2,'playlist':'Original four tracks, continuous ordered loop from 236.52s to new ending; no restart at inner cuts.',
            'music_playlist':entries,'mix':metrics,'script':str(SCRIPT_OUT/'GLM内心解说与吐槽_20段接片稿.md')}
    edit.write(OUT/'交付.json',report)
    print('DELIVERED',target,timeline['frames']/FPS,flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script',type=Path,required=True)
    parser.add_argument('--stage',choices=['prepare','voices','render','all'],default='all')
    args=parser.parse_args();entries,base=prepare(args.script)
    if args.stage=='prepare':return
    report=voices(entries) if args.stage in ('voices','all') else edit.read(WORK/'selected_audio_validation.json')
    if args.stage=='voices':return
    render_inner(entries,report);assemble(build(entries,base))


if __name__=='__main__':main()
