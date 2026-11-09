from pathlib import Path
import os
import csv,json,math,re,sys,subprocess,wave
from functools import lru_cache
import numpy as np
from PIL import Image,ImageDraw,ImageFont,ImageOps,ImageEnhance
root=Path(__file__).resolve().parents[3]
work=root/'runs/tyrol_class_photo_20261109'
sys.path.insert(0,str(root/'tools/tyrol/qwen_day12'))
from edit import align,caption_parts,srt_time
sys.path.insert(0,str(root/'tools/tyrol'))
from cut import CAST
roles={v[2].split('/')[0]:v for v in CAST.values()}
colors={r:v[4] for r,v in roles.items()}
names={'glm':'智谱娘','qwen':'千问','deepseek':'大肥鱼','gpt':'白龙','gemini':'基米','claude':'克莱德'}
countries={'AUS':'奥地利','ITA':'意大利','GER':'德国','FRA':'法国','SWI':'瑞士'}
rd=lambda p:json.loads(p.read_text('utf-8-sig'))
ff=os.environ.get('FFMPEG','ffmpeg');R=Image.Resampling.LANCZOS; fps=25;spf=960
out=root/'exports/蒂罗尔开场/合照局';out.mkdir(exist_ok=True,parents=True)
@lru_cache(None)
def font(n,bold=False):return ImageFont.truetype('C:/Windows/Fonts/msyh'+('bd' if bold else '')+'.ttc',n)
@lru_cache(None)
def portrait(role,talk,h):
    cast=roles[role];prefix=root/'assets/characters/poses'/cast[2]; face=cast[3]
    if role=='glm':face='neutral'
    if role=='gemini':face='neutral'
    p=prefix/(face+('_talk' if talk else '')+'.png')
    im=Image.open(p).convert('RGBA');im=im.crop((0,0,im.width,min(1080,im.height)))
    box=im.getchannel('A').point(lambda a:255 if a>25 else 0).getbbox();im=im.crop(box)
    return im.resize((round(im.width*h/im.height),h),R)
@lru_cache(None)
def room(title):
    im=Image.open(root/'assets/stage/academy/backdrops/map_focus.png').convert('RGB')
    return ImageEnhance.Brightness(im.resize((1280,720),R)).enhance(.79)
@lru_cache(None)
def tyrol_map():
    game=Path(os.environ['HOI4_DIR'])/'map'
    im=np.array(Image.open(game/'provinces.bmp').convert('RGB').crop((2980,577,3033,606)))
    rgb=np.full_like(im,[71,93,101])
    for line in (game/'definition.csv').read_bytes().decode('latin1').splitlines():
        parts=line.split(';')
        if parts[0] in ('671','6673'):
            rgb[np.all(im==list(map(int,parts[1:4])),axis=2)]=[237,194,110] if parts[0]=='671' else [115,206,179]
    rgb[np.any(im!=np.roll(im,1,axis=0),axis=2)|np.any(im!=np.roll(im,1,axis=1),axis=2)]=[31,54,59]
    return Image.fromarray(rgb).resize((573,248),Image.Resampling.NEAREST)
@lru_cache(None)
def clean_background(day,date):
    directory=root/'runs'/(work.name+f'_t{day:03d}')/'clean_map'
    for directory in [directory,work/'final_clean']:
        if (directory/'camera.json').exists() and (directory/'map.png').exists():
            if not date or rd(directory/'camera.json')['game_date']==date:
                return Image.open(directory/'map.png').convert('RGBA').resize((1280,720),R)
    return None
def write(d,x,y,s,n,color,b=False,anchor=None):
    d.text((x,y),s,font=font(n,b),fill=color,anchor=anchor,stroke_width=1,stroke_fill='#26383a')
def still(row,prev,txt,talk,mode):
    # All cropped sprites extend behind the same opaque, full-width foreground.
    rail_y=520
    bottom=rail_y+20
    role=row['role'];scene=row.get('chapter') or {'intro':'五人入场','draft':'抽签选国','social':'合照留位','history':'两面旗的来历','host':'钢四教室'}[row['kind']]
    key=re.match(r'[DN](\d+)',row['id'])
    actual=None
    if key:
        day=int(key[1]);state=row.get('world')
        actual=clean_background(day+1,state['date']) if state else clean_background(day,'')
    im=actual.copy() if actual is not None else room('room').copy().convert('RGBA')
    d=ImageDraw.Draw(im)
    d.rounded_rectangle((46,30,1218,87),10,fill='#263c41',outline='#c9b87d',width=2)
    write(d,70,41,'蒂罗尔的两面旗',26,'#f6e9be',True);write(d,1194,58,scene,25,'#fff3d4',True,'rm')
    ensemble=row['id'] in ('P01','P07','P19')
    if ensemble:
        for i,r in enumerate(['deepseek','claude','gemini','qwen','gpt']):
            a=portrait(r,False,238);x=315+i*187
            im.alpha_composite(a,(x-a.width//2,bottom-a.height))
        a=portrait('glm',talk,322);im.alpha_composite(a,(1,bottom-a.height))
    elif row.get('world'):
        area=tyrol_map()
        im.paste(area,(564,158))
        a=portrait(role,talk,395);im.alpha_composite(a,(260-a.width//2,bottom-a.height))
        d=ImageDraw.Draw(im);state=row['world']
        write(d,568,111,state['date'].replace('.',' / '),26,'#fff3ce',True)
        for x,p,c in [(570,'671','#efd091'),(848,'6673','#96e6c9')]:
            write(d,x,420,p+' · '+countries.get(state['control'][p],state['control'][p]),26,c,True)
        write(d,570,468,'存档外交状态　'+('奥意交战中' if state['at_war'] else '奥意未交战'),22,'#fff3ce')
    else:
        listener=row.get('listener') or (prev if prev!=role else 'glm')
        if listener==role:listener='gpt'
        b=portrait(listener,False,296);im.alpha_composite(b,(958-b.width//2,bottom-b.height))
        a=portrait(role,talk,403);im.alpha_composite(a,(312-a.width//2,bottom-a.height))
        d=ImageDraw.Draw(im)
        if row.get('country'):
            write(d,675,176,'我选',28,'#f3e3ba');write(d,672,204,countries[row['country']],52,'#ffdf91',True)
        elif row.get('id')=='P13':
            write(d,627,171,'1919 年　布伦纳山口',30,'#ffdf91',True)
    d=ImageDraw.Draw(im)
    d.rectangle((0,rail_y,1279,719),fill='#4a3931')
    d.rectangle((0,rail_y,1279,rail_y+3),fill='#b69a6e')
    d.rounded_rectangle((40,530,1240,694),15,fill='#152d33',outline='#8da99c',width=2)
    write(d,73,545,names[role],24,colors[role],True)
    if txt:
        lines=[];line=''
        for ch in txt:
            if line and d.textlength(line+ch,font=font(34,True))>1125:lines.append(line);line=''
            line+=ch
        if line:lines.append(line)
        assert len(lines)<=2,txt
        for j,line in enumerate(lines):write(d,73,585+j*44,line,34,'#fff7e4',True)
    return im.convert('RGB').tobytes()
def render(mode):
    opening=mode=='opening'
    rows=rd(work/('开桌公开原话.json' if opening else 'highlights.json'))
    takes=work/('opening_voices' if opening else 'game_voices')
    name='合照是幌子·五人入场与选国' if opening else '合照局·七天桌边精选'
    target=out/(name+'.mp4');tmp=work/(mode+'_video.mp4');sound=work/(mode+'_audio.wav')
    proc=subprocess.Popen([ff,'-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1280x720','-r','25','-i','pipe:0','-an','-c:v','libx264','-threads','4','-preset','ultrafast','-crf','19','-pix_fmt','yuv420p',str(tmp)],stdin=subprocess.PIPE)
    timeline=[];srt=[];audio=[];start=0;prev='glm'
    for row in rows:
        tag=row['role']
        wav=takes/(row['id']+'.wav')
        if opening and row['id']=='P13':
            row={**row,'text':row['text'].replace('……喘口气。','').split('两口气留完了。')[0]}
            wav=work/'P13.broadcast.wav'
        with wave.open(str(wav)) as w:
            pcm=np.frombuffer(w.readframes(w.getnframes()),'<i2')
        nz=np.flatnonzero(abs(pcm.astype('i4'))>70);assert len(nz)>500
        pcm=pcm[max(0,nz[0]-960):nz[-1]+1600]
        # The presenter has the same tempo as the accepted classroom preview. Other guests keep their own cadences.
        factor=1.055 if tag=='glm' else 1.035
        pcm=np.frombuffer(subprocess.check_output([ff,'-v','error','-f','s16le','-ar','24000','-ac','1','-i','pipe:0','-af','atempo='+str(factor),'-f','s16le','pipe:1'],input=pcm.tobytes()),'<i2')
        n=math.ceil(len(pcm)/spf);pad=np.pad(pcm,(0,n*spf-len(pcm)))
        e=np.sqrt((pad.reshape(-1,spf).astype(float)**2).mean(axis=1))
        text=row['text']; parts=caption_parts(text);bounds=align(parts,pcm,n)
        gap=34 if row.get('kind') in ('history','draft') or timeline and row.get('chapter')!=timeline[-1].get('chapter') else 16
        if row['id'] in ('P07','P12','P13','P18','P19') or row==rows[-1]:gap=40
        for k,part in enumerate(parts):
            a,b=bounds[k:k+2];srt.append((start+a,start+b,names[tag],part))
            frames=[still(row,prev,part,talk,mode) for talk in (False,True)]
            for j in range(a,b):
                proc.stdin.write(frames[int(e[j]>max(260,np.percentile(e,65)*.28) and j%10<7)])
        idle=still(row,prev,'',False,mode)
        for _ in range(gap):proc.stdin.write(idle)
        audio.extend([pad,np.zeros(gap*spf,dtype='<i2')])
        timeline.append({**row,'start':start,'end':start+n,'tail':gap})
        start+=n+gap;prev=tag
    proc.stdin.close();assert proc.wait()==0
    with wave.open(str(sound),'wb') as w:
        w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(np.concatenate(audio).tobytes())
    publish=work/(mode+'_ready.mp4')
    subprocess.run([ff,'-v','error','-y','-i',str(tmp),'-i',str(sound),'-c:v','copy','-c:a','aac','-b:a','128k','-movflags','+faststart',str(publish)],check=True)
    publish.replace(target)
    (out/(name+'.srt')).write_text('\n\n'.join(f'{i}\n{srt_time(a)} --> {srt_time(b)}\n{speaker}　{text}' for i,(a,b,speaker,text) in enumerate(srt,1))+'\n','utf-8-sig')
    with (out/(name+'·配音表.csv')).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['角色','开始','台词结束','原话编号','台词'])
        for row in timeline:w.writerow([names[row['role']],srt_time(row['start']),srt_time(row['end']),row['id'],row['text']])
    print(name,start/25,flush=True)
if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description="使用已有台词和 WAV 渲染桌边立绘。")
    parser.add_argument("mode",choices=("opening","highlights"))
    render(parser.parse_args().mode)
