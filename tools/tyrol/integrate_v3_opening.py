"""Attach the approved v3 MiniMax opening to the existing episode; no TTS calls."""
from functools import lru_cache
import argparse
import math
from pathlib import Path
import re
import subprocess
import wave

import cv2
import numpy as np
from PIL import Image, ImageDraw

import session_edit as edit
import lecture


ROOT = edit.ROOT
WORK = ROOT / 'runs/tyrol_new_table_20261110/v3_opening_video'
OUT = ROOT / 'exports/tyrol_new_table_20261110/正片/v3开场接入'
AUDIO_WORK = ROOT / 'runs/tyrol_new_table_20261110/v3_opening_28turbo_4cps'
OLD_WORK = ROOT / 'runs/tyrol_new_table_20261110/edit'
SCRIPT = ROOT / 'exports/tyrol_new_table_20261110/配音稿/v3/Gemini-v3_完整纯配音稿.json'
STAMP = 'v3改写 · 配音演绎'
PACING = False
SIGNAL_BREAK = False
FPS, SR, SPF = edit.FPS, edit.SR, edit.SPF
lecture.FF = edit.FF

# Existing visual scenes, now driven by v3 subtitle IDs rather than old timecodes.
SCENES = [(1,1,'cover'),(2,2,'map'),(3,5,'map'),(6,8,'italy'),(9,11,'paper'),
          (12,13,'cute'),(14,15,'confess'),(16,16,'meme'),(17,18,'case'),
          (19,21,'lecture'),(22,23,'arrow'),(24,25,'research'),(26,27,'stock'),
          (28,29,'factory'),(30,31,'order'),(32,32,'no_move'),(33,35,'supply'),
          (36,37,'truck'),(38,38,'zero'),(39,42,'inquiry'),(43,43,'sign'),
          (44,44,'more'),(45,45,'mods'),(46,48,'kaiser'),(49,50,'boast'),
          (51,53,'type'),(54,54,'draft'),(55,56,'invite'),(57,58,'five'),
          (59,60,'host'),(61,61,'cut')]


def pcm_read(path):
    data = subprocess.check_output([edit.FF, '-v', 'error', '-i', str(path), '-ar', str(SR),
                                    '-ac', '1', '-f', 's16le', '-'])
    return np.frombuffer(data, '<i2').copy()


def save_wav(path, pcm):
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((1, 2, SR, 0, 'NONE', 'not compressed'))
        wav.writeframes(pcm.astype('<i2').tobytes())


def native_bounds(parts, subtitles, frames):
    """Map exact input character offsets to the TTS response's word timestamps."""
    words=[w for segment in subtitles for w in segment['timestamped_words']]
    bounds=[0];offset=0
    for part in parts[:-1]:
        offset+=len(part)
        candidates=[w for w in words if w['word_end']>offset and w['word_begin']>=offset
                    and any(ch.isalnum() for ch in w['word'])]
        if not candidates:raise ValueError('Native subtitle does not cover input text offset '+str(offset))
        frame=round(candidates[0]['time_begin']*FPS/1000)
        if not bounds[-1]<frame<frames:raise ValueError('Non-monotonic native subtitle boundaries')
        bounds.append(frame)
    return bounds+[frames]


def insert_silences(pcm,events):
    """Insert whole frames of silence without removing or stretching any samples."""
    pieces=[];cursor=0
    for cut,frames,_ in sorted(events):
        sample=cut*SPF
        pieces.extend([pcm[cursor:sample],np.zeros(frames*SPF,dtype='<i2')])
        cursor=sample
    pieces.append(pcm[cursor:])
    def shifted(frame):return frame+sum(n for cut,n,_ in events if cut<=frame)
    return np.concatenate(pieces),shifted


def quiet_cut(pcm,anchor):
    """Prefer the short low-energy space immediately before the native word onset."""
    energy=np.sqrt((pcm.reshape(-1,SPF).astype(float)**2).mean(axis=1))
    candidates=range(max(1,anchor-3),min(anchor,len(energy)-1)+1)
    return min(candidates,key=lambda f:energy[f]+abs(anchor-f)*2)


def prepare():
    WORK.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (WORK / 'clips').mkdir(exist_ok=True)
    report = edit.read(AUDIO_WORK / 'selected_audio_validation.json')
    chunks = {r['id']: r for r in edit.read(AUDIO_WORK / 'synthesis_chunks.json')}
    script = edit.read(SCRIPT)
    text_rows = {r['id']: r for r in script['segments']}
    old_rows = {r['line_id']: r for r in edit.read(OLD_WORK / 'timeline.json')['rows']}
    rows, cues, position, lecture_parts = [], [], 0, []
    for i, segment in enumerate(report['segments']):
        line = segment['id']
        chunk = chunks[line]
        pcm = pcm_read(segment['selected_audio'])
        n = math.ceil(len(pcm) / SPF)
        gap = round((.28 if i < 7 else .7) * FPS)
        # Preserve all speech and pad to the next video frame, never cut its tail.
        padded = np.pad(pcm, (0, n*SPF-len(pcm)))
        row_part_start=len(lecture_parts)
        if line.startswith('L004-'):
            parts = [text_rows[k]['selected_text'] for k in chunk['source_ids']]
            bounds = (native_bounds(parts,edit.read(segment['native_subtitles']),n)
                      if segment.get('native_subtitles') else edit.align(parts,pcm,n))
            local = []
            for source_id, text, a, b in zip(chunk['source_ids'], parts, bounds, bounds[1:]):
                cue = {'source_id': source_id, 'text': text, 'a': position+a, 'b': position+b}
                lecture_parts.append(cue)
                local.append([a,b,text])
            # Keep the single flowing description readable across its subtitle split.
            if 'L004-K007' in chunk['source_ids'] and 'L004-K008' in chunk['source_ids']:
                k=chunk['source_ids'].index('L004-K007')
                local[k:k+2]=[[local[k][0],local[k+1][1],parts[k]+parts[k+1]]]
            if segment.get('native_subtitles') and any(k in chunk['source_ids'] for k in
                    ('L004-K024','L004-K039','L004-K050','L004-K055')):
                display_parts=[]
                for source_id,part in zip(chunk['source_ids'],parts):
                    display_parts.extend([p for p in re.split(r'(?<=[。！？])',part) if p]
                                         if source_id in ('L004-K024','L004-K039','L004-K050','L004-K055') else [part])
                display_bounds=native_bounds(display_parts,edit.read(segment['native_subtitles']),n)
                local=[[a,b,text] for text,a,b in zip(display_parts,display_bounds,display_bounds[1:])]
            row = {'line_id':line,'role':'glm','speaker':'智谱娘','kind':'lecture',
                   'chapter':'大狐狸开课 · 入坑邀请','text':chunk['text'],'source_ids':chunk['source_ids']}
        else:
            row = dict(old_rows[line])
            row.update(text=chunk['text'], speaker=edit.NAMES[row['role']], edit_tempo=1,
                       stamp=STAMP)
            if row['role']=='gpt':
                row['board']='GPT · 白龙酱'
            parts = edit.caption_parts(chunk['text'])
            bounds = (native_bounds(parts,edit.read(segment['native_subtitles']),n)
                      if segment.get('native_subtitles') else edit.align(parts,pcm,n))
            local = [[a,b,text] for text,a,b in zip(parts,bounds,bounds[1:])]
        pause_events=[]
        if PACING and line.startswith('L004-'):
            anchors=[]
            if SIGNAL_BREAK and line=='L004-A01':anchors.append((bounds[chunk['source_ids'].index('L004-K011')],.72,'电视无信号转场'))
            if line=='L004-A02':anchors.append((bounds[chunk['source_ids'].index('L004-K019')],2.0,'梗图额外停留'))
            if line=='L004-A03':anchors.append((next(a for a,b,text in local if text.startswith('比如')), .8,'战略供给讲解后的留白'))
            if line=='L004-A04':anchors.append((bounds[chunk['source_ids'].index('L004-K032')],.8,'箭头不动前的疑惑'))
            if line=='L004-A06':anchors.append((bounds[chunk['source_ids'].index('L004-K051')],.8,'没人陪玩的反应拍'))
            events=[(quiet_cut(padded,anchor),round(seconds*FPS),label) for anchor,seconds,label in anchors]
            original_frames=n
            if events:
                padded,shifted=insert_silences(padded,events)
                local=[[shifted(a),shifted(b),text] for a,b,text in local]
                for cue in lecture_parts[row_part_start:]:
                    cue.update(a=position+shifted(cue['a']-position),b=position+shifted(cue['b']-position))
                for cut,count,label in events:
                    before=sum(fr for at,fr,_ in events if at<cut)
                    pause_events.append({'label':label,'local_start_frame':cut+before,'frames':count})
                n+=len(padded)//SPF-original_frames
            if line=='L004-A04':
                pause_events.append({'label':'将军放行后的反应拍','local_start_frame':n+gap,'frames':FPS})
                gap+=FPS
        if PACING and not line.startswith('L004-'):
            order=['glm','deepseek','gpt','gemini','claude','qwen']
            index=order.index(row['role'])
            before=order[:max(1,index)]
            after=order[:index+1]
            reveal_at=0 if index in (0,1) else 6
            if index>0 and segment.get('native_subtitles'):
                offset=chunk['spoken_text'].find(edit.NAMES[row['role']])
                words=[w for s in edit.read(segment['native_subtitles']) for w in s['timestamped_words']]
                candidates=[w for w in words if w['word_begin']>=offset and any(c.isalnum() for c in w['word'])]
                if offset>=0 and candidates:reveal_at=round(candidates[0]['time_begin']*FPS/1000)
            row.update(style='roster',reveal_before=before,reveal_after=after,reveal_at_frame=reveal_at)
        frames = n+gap
        row.update(start_frame=position,end_frame=position+frames,audio_frames=n,gap_frames=gap,
                   approved_audio=segment['selected_audio'],cue_local=local,
                   alignment='tts_word_timestamps' if segment.get('native_subtitles') else 'waveform_guided',
                   pause_events=pause_events,
                   clip=str(WORK/'clips'/(line+'.mkv')))
        signal=next((e for e in pause_events if e['label']=='电视无信号转场'),None)
        if signal:
            a=signal['local_start_frame'];b=a+signal['frames']
            row['signal_break']={'a':a,'b':b}
            # The bumper carries its own message, not the previous spoken subtitle.
            local[:]=[[x,min(y,a) if x<a<y else y,text] for x,y,text in local]
        save_wav(WORK/(line+'.wav'), np.concatenate([padded,np.zeros(gap*SPF,dtype='<i2')]))
        cues.extend([position+a,position+b,text] for a,b,text in local)
        rows.append(row)
        position += frames
        if PACING and line=='L004-A08':
            transition={'line_id':'TRANSITION','kind':'transition','style':'roster','role':'glm',
                        'speaker':'智谱娘','chapter':'合照是幌子 · 新朋友入场','board':'','text':'',
                        'stamp':'选手入场 · 依次亮相','revealed_roles':['glm'],
                        'start_frame':position,'end_frame':position+40,'audio_frames':0,'gap_frames':40,
                        'cue_local':[[0,40,'']],'clip':str(WORK/'clips/TRANSITION.mkv')}
            save_wav(WORK/'TRANSITION.wav',np.zeros(40*SPF,dtype='<i2'))
            rows.append(transition);position+=40
    scenes = []
    for first,last,scene in SCENES:
        matching = [p for p in lecture_parts if first <= int(p['source_id'][-3:]) <= last]
        if not matching:continue
        scenes.append({'scene':scene,'a':matching[0]['a'],'b':matching[-1]['b']})
    if PACING:
        research=next(s for s in scenes if s['scene']=='research')
        sample_row=next(r for r in rows if r['line_id']=='L004-A03')
        example=sample_row['start_frame']+next(a for a,b,text in sample_row['cue_local'] if text.startswith('比如'))
        scenes.insert(scenes.index(research),{'scene':'lecture','a':research['a'],'b':example})
        research['a']=example
    result = {'fps':FPS,'frames':position,'rows':rows,'cues':cues,'lecture_scenes':scenes,
              'subtitle_alignment':'Native TTS word timestamps for revised takes; existing waveform-guided alignment for unchanged takes',
              'audio_policy':'Approved selected takes, no TTS, no pitch or tempo processing; frame-end silence padding only'}
    edit.write(WORK/'opening_timeline.json',result)
    return result


@lru_cache(1)
def transition_frame():
    im=Image.fromarray(cv2.cvtColor(lecture.bgr(lecture.rack(5)),cv2.COLOR_BGR2RGB)).convert('RGBA')
    im=Image.alpha_composite(im,Image.new('RGBA',im.size,(8,22,30,80)))
    d=ImageDraw.Draw(im)
    d.text((640,125),'选手入场',font=edit.font(64,True),fill='#f4dfad',anchor='mm')
    d.text((640,184),'五位朋友，依次亮相',font=edit.font(27),fill='#e0ebe5',anchor='mm')
    return cv2.cvtColor(np.asarray(im.convert('RGB')),cv2.COLOR_RGB2BGR)


@lru_cache(1)
def no_signal_frame():
    colors=['#bdbdbd','#bdbd00','#00bdbd','#00bd00','#bd00bd','#bd0000','#0000bd']
    im=Image.new('RGB',(1280,720),'#111111');d=ImageDraw.Draw(im)
    for i,color in enumerate(colors):d.rectangle((round(i*1280/7),0,round((i+1)*1280/7),510),fill=color)
    for i,color in enumerate(['#0000bd','#111111','#bd00bd','#111111','#00bdbd','#111111','#bdbdbd']):
        d.rectangle((round(i*1280/7),510,round((i+1)*1280/7),580),fill=color)
    for i,color in enumerate(['#142946','#eeeeee','#3e1648','#090909','#202020','#101010']):
        d.rectangle((round(i*1280/6),580,round((i+1)*1280/6),720),fill=color)
    pixels=np.asarray(im).astype(np.int16)
    pixels+=np.random.default_rng(113).normal(0,1.6,pixels.shape).astype(np.int16)
    pixels[::4]=(pixels[::4]*.86).astype(np.int16)
    im=Image.fromarray(np.clip(pixels,0,255).astype('uint8'))
    d=ImageDraw.Draw(im)
    d.rectangle((360,270,920,444),fill='#0b0b0b')
    d.text((640,320),'NO SIGNAL',font=lecture.ft(67,'mono'),fill='#eeeeee',anchor='mm')
    d.text((640,398),'信号中断',font=edit.font(29),fill='#cfcfcf',anchor='mm')
    return cv2.cvtColor(np.asarray(im),cv2.COLOR_RGB2BGR)


def state_at(scene,u,frame):
    if scene=='map':return int(u>.19)
    if scene=='italy':return int(u>.48)
    if scene=='paper':return int(u>.32)
    if scene=='cute':return 0 if u<.40 else 1 if u<.67 else 2
    if scene=='confess':return int(u>.72)
    if scene in ('factory','supply','more','boast','draft'):return int(u>.54)
    if scene=='type':return int(u>.70)
    if scene=='invite':return int(u>.84)
    if scene=='inquiry':return min(3,int(u*3.6))
    if scene=='five':return min(5,1+int(frame/6.75))
    return 0


def subtitle(frame,text):
    if not text:return frame
    image=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
    draw=ImageDraw.Draw(image)
    lines=edit.wrap(text,36,1150)
    if len(lines)>2:raise ValueError('Lecture caption needs a new break: '+text)
    y=657 if len(lines)==1 else 627
    for j,line in enumerate(lines):
        draw.text((640,y+j*45),line,font=edit.font(36,True),fill='#fff7e5',anchor='mm',
                  stroke_width=2,stroke_fill='#152328')
    return cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2BGR)


@lru_cache(40)
def lecture_still(scene,mouth,state,text):
    return subtitle(lecture.graded(scene,mouth,state),text)


def render(timeline):
    cap=cv2.VideoCapture(str(lecture.M/'maproll.mp4'))
    if not cap.isOpened():raise RuntimeError('Existing lecture map footage unavailable')
    scene_index=0
    for row in timeline['rows']:
        target=Path(row['clip']);receipt=target.with_suffix('.json')
        if target.exists() and receipt.exists() and edit.read(receipt)['row']==row:
            continue
        frames=row['end_frame']-row['start_frame']
        with wave.open(str(WORK/(row['line_id']+'.wav'))) as wav:
            pcm=np.frombuffer(wav.readframes(wav.getnframes()),'<i2')
        energy=np.sqrt((pcm.reshape(-1,SPF).astype(float)**2).mean(axis=1))
        video=target.with_name(target.stem+'.picture.mkv')
        process=subprocess.Popen([edit.FF,'-v','error','-nostdin','-y','-f','rawvideo',
            '-pixel_format','bgr24','-video_size','1280x720','-framerate',str(FPS),'-i','pipe:0',
            '-an','-c:v','libx264','-threads','4','-preset','veryfast','-crf','19',
            '-pix_fmt','yuv420p',str(video)],stdin=subprocess.PIPE)
        cue_index=0
        images={}
        try:
            for f in range(frames):
                t=row['start_frame']+f
                while cue_index+1<len(row['cue_local']) and f>=row['cue_local'][cue_index][1]:cue_index+=1
                a,b,caption=row['cue_local'][cue_index]
                if not a<=f<b:caption=''
                mouth=bool(f<row['audio_frames'] and energy[f]>max(200,float(np.percentile(energy,70))*.22)
                           and (f//3)%3!=0)
                signal=row.get('signal_break')
                if signal and signal['a']<=f<signal['b']:
                    frame=no_signal_frame()
                elif row['kind']=='transition':
                    frame=transition_frame()
                elif row['kind']=='lecture':
                    while scene_index+1<len(timeline['lecture_scenes']) and t>=timeline['lecture_scenes'][scene_index+1]['a']:
                        scene_index+=1
                    shot=timeline['lecture_scenes'][scene_index]
                    scene=shot['scene'];u=max(0,min(1,(t-shot['a'])/max(1,shot['b']-shot['a'])))
                    state=state_at(scene,u,t-shot['a'])
                    if scene in ('map','italy','arrow'):
                        ok,frame=cap.read()
                        if not ok:cap.set(cv2.CAP_PROP_POS_FRAMES,0);ok,frame=cap.read()
                        if not ok:raise RuntimeError('Unreadable existing lecture map footage')
                        if scene=='arrow':lecture.arrow(frame,min(1,max(0,(u-.28)/.40)))
                        ov,alpha=lecture.layer(scene,mouth,state)
                        frame=subtitle(lecture.grade(lecture.blend(frame,ov,alpha),scene),caption)
                    elif scene=='order':
                        frame=lecture.bgr(lecture.fit(lecture.src(lecture.mapstill),(1280,720))).copy()
                        lecture.arrow(frame,min(1,max(0,(u-.36)/.40)))
                        frame=subtitle(lecture.grade(frame,scene),caption)
                    else:
                        frame=lecture_still(scene,mouth,state,caption)
                else:
                    reveal=(row['reveal_after'] if f>=row['reveal_at_frame'] else row['reveal_before']) if row.get('reveal_after') else None
                    key=(caption,mouth,tuple(reveal) if reveal else None)
                    if key not in images:
                        art_row={**row,'revealed_roles':reveal} if reveal else row
                        im=edit.frame_image(art_row,caption,mouth,OLD_WORK)
                        images[key]=cv2.cvtColor(np.asarray(im),cv2.COLOR_RGB2BGR)
                    frame=images[key]
                process.stdin.write(frame.tobytes())
        finally:
            process.stdin.close()
        if process.wait():raise RuntimeError('Opening picture encode failed: '+row['line_id'])
        # Flush normalization separately from frame-limited picture encoding.
        audio=target.with_suffix('.audio.wav')
        edit.complete_audio(None if row['kind']=='transition' else WORK/(row['line_id']+'.wav'),audio,frames)
        edit.ff(['-i',video,'-i',audio,'-map','0:v:0','-map','1:a:0','-c','copy',target])
        video.unlink()
        edit.write(receipt,{'row':row,'frames':frames,'audio_samples':frames*1920})
        print('RENDERED',row['line_id'],round(row['end_frame']/FPS,2),flush=True)
    cap.release()


def compose(rows,cues,target,prefix):
    frames=rows[-1]['end_frame']
    listing=WORK/(prefix+'.ffconcat')
    listing.write_text('ffconcat version 1.0\n'+''.join(
        "file '"+r['clip'].replace('\\','/')+"'\n"+
        f"duration {(r['end_frame']-r['start_frame'])/FPS:.2f}\n" for r in rows),encoding='utf-8')
    master=WORK/(prefix+'.wav')
    with wave.open(str(master),'wb') as output:
        output.setparams((2,2,48000,0,'NONE','not compressed'))
        for row in rows:
            with wave.open(str(Path(row['clip']).with_suffix('.audio.wav'))) as audio:
                assert audio.getnframes()==(row['end_frame']-row['start_frame'])*1920
                while data:=audio.readframes(48000):output.writeframesraw(data)
    normalized=WORK/(prefix+'.normalized.wav')
    edit.ff(['-i',master,'-af',f'loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,apad,atrim=end_sample={frames*1920}',
             '-c:a','pcm_s16le',normalized])
    chapters=[]
    for row in rows:
        if not chapters or chapters[-1][1]!=row['chapter']:chapters.append((row['start_frame'],row['chapter']))
    metadata=[';FFMETADATA1']
    for (a,title),b in zip(chapters,[x[0] for x in chapters[1:]]+[frames]):
        metadata.extend(['[CHAPTER]','TIMEBASE=1/25',f'START={a}',f'END={b}','title='+title])
    meta=WORK/(prefix+'.chapters.txt');meta.write_text('\n'.join(metadata)+'\n',encoding='utf-8')
    edit.ff(['-f','concat','-safe','0','-i',listing,'-i',normalized,'-f','ffmetadata','-i',meta,
             '-map','0:v:0','-map','1:a:0','-map_metadata','2','-map_chapters','2',
             '-c:v','copy','-c:a','aac','-b:a','160k','-movflags','+faststart',target])
    srt=target.with_suffix('.srt')
    srt.write_text('\n\n'.join(f'{i}\n{edit.srt_time(a)} --> {edit.srt_time(b)}\n{text}'
                   for i,(a,b,text) in enumerate(cues,1))+'\n',encoding='utf-8-sig')
    edit.write(target.with_suffix('.timeline.json'),{'fps':FPS,'frames':frames,'rows':rows,'cues':cues})
    return {'video':str(target),'srt':str(srt),'frames':frames,'seconds':frames/FPS}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision',choices=['v3','v3_1','v3_2','v3_3'],default='v3')
    args=parser.parse_args()
    global WORK,OUT,AUDIO_WORK,SCRIPT,SCENES,STAMP,PACING,SIGNAL_BREAK
    if args.revision in ('v3_1','v3_2','v3_3'):
        WORK=ROOT/'runs/tyrol_new_table_20261110/v3_1_opening_video'
        OUT=ROOT/'exports/tyrol_new_table_20261110/正片/v3.1开场修订'
        AUDIO_WORK=ROOT/'runs/tyrol_new_table_20261110/v3_1_revision'
        SCRIPT=ROOT/'exports/tyrol_new_table_20261110/配音稿/v3.1/Gemini-v3.1_完整纯配音稿.json'
        STAMP='v3.1改写 · 配音演绎'
        SCENES=[(1,1,'cover'),(2,2,'map'),(3,5,'map'),(6,10,'italy'),(11,13,'cute'),
                (14,15,'confess'),(16,16,'meme'),(19,21,'lecture'),(22,23,'arrow'),
                (24,25,'research'),(26,27,'stock'),(28,29,'factory'),(30,31,'order'),
                (32,32,'no_move'),(33,35,'supply'),(36,37,'truck'),(38,38,'zero'),
                (39,42,'inquiry'),(43,43,'sign'),(45,45,'mods'),(46,48,'kaiser'),
                (49,50,'boast'),(51,53,'type'),(54,54,'draft'),(55,56,'invite'),
                (57,58,'five'),(59,60,'host'),(61,61,'cut')]
    if args.revision in ('v3_2','v3_3'):
        PACING=True
        WORK=ROOT/'runs/tyrol_new_table_20261110/v3_2_opening_video'
        OUT=ROOT/'exports/tyrol_new_table_20261110/正片/v3.2节奏与逐位入场'
        AUDIO_WORK=ROOT/'runs/tyrol_new_table_20261110/v3_2_pacing'
        SCRIPT=ROOT/'exports/tyrol_new_table_20261110/配音稿/v3.2/Gemini-v3.2_完整纯配音稿.json'
        STAMP='v3.2改写 · 配音演绎'
    if args.revision=='v3_3':
        SIGNAL_BREAK=True
        WORK=ROOT/'runs/tyrol_new_table_20261110/v3_3_signal_break'
        OUT=ROOT/'exports/tyrol_new_table_20261110/正片/v3.3无信号转场'
        STAMP='v3.3改写 · 配音演绎'
    timeline=prepare()
    render(timeline)
    preview=compose(timeline['rows'],timeline['cues'],OUT/'开场与六人入场·MiniMax2.8.mp4','opening')
    old=edit.read(OLD_WORK/'timeline.json')
    position=timeline['frames'];rows=list(timeline['rows']);cues=list(timeline['cues'])
    for source in old['rows']:
        if source['line_id'] in ['L004','L005','L006','L007','L008','L009','L010','L011','L012']:continue
        row=dict(source);n=source['end_frame']-source['start_frame']
        row.update(start_frame=position,end_frame=position+n)
        rows.append(row)
        for a,b,text in old['cues']:
            if source['start_frame']<=a<source['end_frame']:
                cues.append([position+a-source['start_frame'],position+b-source['start_frame'],text])
        position+=n
    full=compose(rows,cues,OUT/'这份初稿，下轮一定·开场接入版（后段原配音）.mp4','full')
    edit.write(OUT/'交付.json',{'opening':preview,'episode':full,
        'scope':'L004 and L005-L010 replaced with approved v3 MiniMax audio; L011/L012 removed; L013 onward reuses original picture/audio',
        'revision':args.revision,'audio_revision_report':str(AUDIO_WORK/'selected_audio_validation.json'),
        'subtitle_alignment':timeline['subtitle_alignment'],
        'tts_calls':0,'bgm_added':False,'voice_changed_ids':[r['line_id'] for r in timeline['rows'] if r['kind']!='transition']})
    print(preview,full,flush=True)


if __name__=='__main__':main()
