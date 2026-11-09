from collections import Counter
import argparse
import csv
from functools import lru_cache
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import wave
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance
from script import WORK, ROOT, OUT, rows, voices, groups
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cut import CAST, province_panel, FF, PROBE as FP

FPS, SR, SPF=25,24000,960
art=WORK/'art'
art.mkdir(exist_ok=True,parents=True)
OUT.mkdir(exist_ok=True,parents=True)
R=Image.Resampling.LANCZOS
gold='#F4CE88'
white='#FFF7E8'
ink=(11,25,32,235)
muted='#CBDAD7'
pair={4:'AUS',5:'ITA',6:'ITA',7:'AUS',8:'ITA',9:'AUS',10:'GER',11:'GLM',12:'GER',
      15:'AUS',16:'GER',17:'FRA',18:'FRA',19:'ITA',20:'ITA',21:'FRA',22:'AUS',23:'ITA'}

@lru_cache(None)
def font(px,bold=False):
    return ImageFont.truetype('C:/Windows/Fonts/msyh'+('bd' if bold else '')+'.ttc',px)

def word(d,xy,txt,n,col=white,bold=False,anchor=None):
    d.text(xy,txt,font=font(n,bold),fill=col,anchor=anchor)

@lru_cache(None)
def portrait(tag,talk,listener):
    pose,face=CAST[tag][2:4]
    if listener:face='neutral'
    p=ROOT/f'assets/characters/poses/{pose}/{face}{"_talk" if talk else ""}.png'
    im=Image.open(p).convert('RGBA')
    im=ImageOps.contain(im.crop((0,0,im.width,1040)),(415,446) if not listener else (285,305),R)
    if listener:
        alpha=im.getchannel('A')
        im=ImageEnhance.Brightness(im.convert('RGB')).enhance(.76).convert('RGBA')
        im.putalpha(alpha)
    return im

@lru_cache(None)
def panel():
    return province_panel().resize((596,312),R)

@lru_cache(None)
def shade():
    v=np.zeros((720,1280,4),dtype=np.uint8)
    y=np.arange(720)
    alpha=np.interp(y,[0,100,405,520,605,720],[82,0,0,60,190,248]).astype('uint8')
    v[:,:,:3]=[9,22,29]
    v[:,:,3]=alpha[:,None]
    return Image.fromarray(v)

def pill(d,x,y,txt,n=22):
    w=math.ceil(font(n,True).getlength(txt))
    d.rounded_rectangle((x,y,x+w+32,y+42),9,fill=(14,30,39,224))
    word(d,(x+16,y+5),txt,n,gold,True)

def actor(im,tag,cx,bottom,talk,listener=False):
    p=portrait(tag,talk,listener)
    im.alpha_composite(p,(int(cx-p.width/2),bottom-p.height))

def image(scene,index,caption='',talk=False,card=False):
    day,section,title,date=groups[scene]
    im=Image.new('RGBA',(1280,720))
    im.alpha_composite(shade())
    d=ImageDraw.Draw(im)
    if card:
        im.alpha_composite(Image.new('RGBA',im.size,(9,24,32,102)))
        d=ImageDraw.Draw(im)
        d.rounded_rectangle((56,189,770,463),20,fill=(13,30,38,237))
        d.rectangle((82,220,88,251),fill=gold)
        word(d,(106,217),section,25,gold,True)
        word(d,(82,276),title,53,white,True)
        word(d,(86,392),date,24,muted)
        return im
    row=rows[index-1]
    tag=row['tag']
    pill(d,43,32,section+'   /   '+title)
    speaker=voices[tag][0]
    if row['style']=='map':
        im.alpha_composite(panel(),(98,140))
        actor(im,'GLM',1002,563,talk)
    elif row['style']=='intro':
        d.rounded_rectangle((63,197,718,394),17,fill=ink)
        word(d,(94,222),'蒂罗尔的两面旗',61,white,True)
        word(d,(98,329),'1936 年 9 月 4 日  ·  奥意交战中',27,gold)
        actor(im,'GLM',1003,568,talk)
    else:
        right=tag in ('ITA','GLM','GER','SWI')
        actor(im,tag,1002 if right else 266,568,talk)
        other=pair.get(index)
        if other:actor(im,other,233 if right else 1049,560,False,True)
    d=ImageDraw.Draw(im)
    if caption:
        d.rounded_rectangle((45,554,1235,686),16,fill=(12,27,35,239))
        color=CAST[tag][4]
        d.rounded_rectangle((66,574,71,660),2,fill=color)
        label='观众解说' if row['kind']=='观众解说' else ('桌边主持' if tag=='GLM' else voices[tag][1])
        word(d,(90,566),speaker+'   /   '+label,22,color,True)
        size=37
        while font(size,True).getlength(caption)>1115 and size>27:size-=1
        if font(size,True).getlength(caption)>1115:
            raise ValueError('Caption too wide: '+caption)
        word(d,(89,612),caption,size,white,True)
    return im

def read_take(i):
    with wave.open(str(WORK/'takes'/f'{i:02d}.wav')) as w:
        assert (w.getframerate(),w.getnchannels(),w.getsampwidth())==(SR,1,2)
        pcm=np.frombuffer(w.readframes(w.getnframes()),'<i2')
    # Remove only surplus digital silence at the two outside edges; leave every internal pause intact.
    nz=np.flatnonzero(np.abs(pcm.astype('i4'))>48)
    if len(nz)==0:raise ValueError(f'Silent take {i}')
    return pcm[max(0,nz[0]-1920):min(len(pcm),nz[-1]+2881)].copy()

def caption_parts(text):
    pieces=re.findall(r'.+?(?:[，。！？；：、,;:?!]+[”」’]*|——|$)',text)
    assert ''.join(pieces)==text
    parts=[]
    for p in pieces:
        if parts and len(parts[-1]+p)<=25 and not re.search('[。！？]$',parts[-1]):
            parts[-1]+=p
        else:parts.append(p)
    return parts

def align(parts,pcm,frames):
    weight=[max(1,len(re.sub(r'\W','',t))) for t in parts]
    bounds=[0]
    v=pcm.astype(float)
    for j in range(1,len(parts)):
        estimate=frames*sum(weight[:j])/sum(weight)
        lo=max(bounds[-1]+8,round(estimate-11));hi=min(frames-8*(len(parts)-j),round(estimate+11))
        def score(f):
            x=v[max(0,f*SPF-360):min(len(v),f*SPF+360)]
            return math.log1p(np.sqrt(np.mean(x*x)))+.08*abs(f-estimate) if len(x) else 999
        if hi<lo:raise ValueError('Cannot align subtitle fragments')
        bounds.append(min(range(lo,hi+1),key=score))
    return bounds+[frames]

def timecode(f):
    s,fr=divmod(f,25);m,s=divmod(s,60);h,m=divmod(m,60)
    return f'{h:02}:{m:02}:{s:02}:{fr:02}'

def srt_time(f):
    d,ms=divmod(f*40,1000);m,s=divmod(d,60);h,m=divmod(m,60)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'

def ff(cmd):
    subprocess.run([FF,'-hide_banner','-nostdin','-v','error','-y',*map(str,cmd)],check=True)

def render(reference=False):
    pieces=[];cues=[];schedule=[];chapters=[];frame=0;current=-1;day2=None
    baseline={}
    if reference:
        previous=WORK.with_name('qwen_day12')/'schedule.json'
        if previous.exists():
            baseline={(r['speaker'],r['text']):r['audio_samples']
                      for r in json.loads(previous.read_text('utf8'))['lines']}
    drawn=set()
    def add(name,n,make):
        if n<=0:return
        dest=art/(name+'.png')
        if dest not in drawn:
            make().save(dest,compress_level=2)
            drawn.add(dest)
        if pieces and pieces[-1][0]==dest.name:pieces[-1]=(dest.name,pieces[-1][1]+n)
        else:pieces.append((dest.name,n))
    wav_path=WORK/'pilot.wav'
    with wave.open(str(wav_path),'wb') as track:
        track.setparams((1,2,SR,0,'NONE','not compressed'))
        for i,r in enumerate(rows,1):
            sc=r['scene']
            if sc!=current:
                if sc==4:day2=frame
                hold=([32,34,39,44,68,39,52] if reference else [36,36,43,48,72,43,57])[sc]
                chapters.append((frame,groups[sc][1]+' · '+groups[sc][2]))
                add(f'card-{sc}',hold,lambda:image(sc,i,card=True))
                track.writeframesraw(bytes(hold*SPF*2))
                frame+=hold;current=sc
            pcm=read_take(i)
            original=len(pcm)
            rate=1.0
            if reference:
                prior=baseline.get((voices[r['tag']][0],r['text']))
                rate=min(1.10,max(1.0,original/(prior*0.94))) if prior else 1.06
                if rate>1.005:
                    compressed=subprocess.check_output([FF,'-v','error','-f','s16le','-ar',str(SR),'-ac','1',
                                                        '-i','pipe:0','-af',f'atempo={rate:.6f}',
                                                        '-f','s16le','pipe:1'],input=pcm.tobytes())
                    pcm=np.frombuffer(compressed,'<i2')
                else:rate=1.0
            n=math.ceil(len(pcm)/SPF)
            txt=caption_parts(r['text'])
            bounds=align(txt,pcm,n)
            start=frame
            for j,t in enumerate(txt):
                a,b=bounds[j:j+2]
                cues.append((start+a,start+b,voices[r['tag']][0],t))
                for k in range(a,b,4):
                    amt=min(b-k,4)
                    block=pcm[k*SPF:(k+amt)*SPF].astype(float)
                    speaking=bool(len(block) and np.sqrt(np.mean(block*block))>200 and ((k-a)//4)%3!=2)
                    add(f'{i:02d}-{j}-{int(speaking)}',amt,lambda:image(sc,i,t,speaking))
            track.writeframesraw(pcm.tobytes())
            gap=round(FPS*r['gap']*(0.86 if reference else 1.0))
            track.writeframesraw(bytes((n*SPF-len(pcm)+gap*SPF)*2))
            frame+=n
            add(f'{i:02d}-listen',gap,lambda:image(sc,i))
            voice_id=json.loads((WORK/'takes'/f'{i:02d}.json').read_text('utf8'))['voice'] if reference else voices[r['tag']][2]
            schedule.append(dict(sequence=i,day=groups[sc][0],scene=groups[sc][2],speaker=voices[r['tag']][0],kind=r['kind'],
                                 text=r['text'],event_ids=' '.join(r['refs']),start=timecode(start),end=timecode(frame),
                                 start_frame=start,end_frame=frame,audio_samples=len(pcm),pause_frames=gap,voice=voice_id))
            if reference:
                schedule[-1].update(source_audio_samples=original,edit_tempo=round(rate,6))
            frame+=gap
    assert sum(n for _,n in pieces)==frame and day2
    ffconcat=['ffconcat version 1.0']
    for name,n in pieces:ffconcat += [f"file 'art/{name}'",f'duration {n/FPS:.2f}']
    ffconcat += [f"file 'art/{pieces[-1][0]}'"]
    (WORK/'overlay.ffconcat').write_text('\n'.join(ffconcat)+'\n','ascii')
    text=[';FFMETADATA1']
    for (a,title),b in zip(chapters,[c[0] for c in chapters[1:]]+[frame]):
        text+=['[CHAPTER]','TIMEBASE=1/25',f'START={a}',f'END={b}','title='+title]
    (WORK/'chapters.txt').write_text('\n'.join(text)+'\n','utf8')
    recording=next((ROOT/'recordings/tyrol_two_flags_20261109').glob('*11-42*.mp4'))
    for day,sec in [(1,17),(2,201)]:
        target=WORK/f'day{day}-plate.mp4'
        if not target.exists():
            ff(['-ss',sec,'-i',recording,'-t','4.8','-vf','crop=784:440:112:74,scale=1280:720:flags=lanczos,setsar=1,fps=25',
                '-an','-c:v','libx264','-crf','17','-preset','veryfast',target])
    filt=(f'[0:v]trim=end_frame={day2},setpts=PTS-STARTPTS[a];[1:v]trim=end_frame={frame-day2},setpts=PTS-STARTPTS[b];'
          '[a][b]concat=n=2:v=1:a=0[game];[2:v]fps=25,format=rgba[overlay];[game][overlay]overlay=repeatlast=1,format=yuv420p[v]')
    outfile=OUT/('蒂罗尔的两面旗·前两天·参考声线版.mp4' if reference else '蒂罗尔的两面旗·前两天·Qwen节奏试片.mp4')
    ff(['-stream_loop','-1','-i',WORK/'day1-plate.mp4','-stream_loop','-1','-i',WORK/'day2-plate.mp4',
        '-f','concat','-safe','0','-i',WORK/'overlay.ffconcat','-i',wav_path,'-f','ffmetadata','-i',WORK/'chapters.txt',
        '-filter_complex_threads','4','-filter_complex',filt,'-map','[v]','-map','3:a:0','-map_metadata','4','-map_chapters','4',
        '-frames:v',frame,'-r','25','-c:v','libx264','-threads','6','-preset','veryfast','-crf','18',
        '-c:a','aac','-b:a','128k','-metadata:s:a:0','language=zho','-movflags','+faststart',outfile])
    (OUT/'前两天.srt').write_text('\n\n'.join(f'{j}\n{srt_time(a)} --> {srt_time(b)}\n{s}　{t}' for j,(a,b,s,t) in enumerate(cues,1))+'\n','utf-8-sig')
    with (OUT/'台词与停顿.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(schedule[0]));w.writeheader();w.writerows(schedule)
    summary=dict(frames=frame,day2_start=day2,chapters=chapters,role_counts=Counter(r['speaker'] for r in schedule),lines=len(rows))
    (WORK/'schedule.json').write_text(json.dumps(dict(summary=summary,lines=schedule),ensure_ascii=False,indent=2),'utf8')
    print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--reference',dest='reference',action='store_true')
    mode.add_argument('--preset',dest='reference',action='store_false',help='Use the original system-voice pilot')
    parser.set_defaults(reference=True)
    reference=parser.parse_args().reference
    if reference:
        WORK=WORK.with_name(WORK.name+'_ref')
        OUT=OUT.with_name('前两日参考声线试片')
        art=WORK/'art'
        art.mkdir(parents=True,exist_ok=True)
        OUT.mkdir(parents=True,exist_ok=True)
    render(reference)
