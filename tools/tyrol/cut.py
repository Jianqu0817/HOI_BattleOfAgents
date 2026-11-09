"""Cut sourced table exchanges from the live recording with the audience reveal last."""
from pathlib import Path
from datetime import datetime
from functools import lru_cache
import json, math, os, subprocess
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageOps

ROOT = Path.cwd()
RUN = ROOT/'runs/campaigns/tyrol_two_flags_20261109'
WORK = ROOT/'runs/tyrol_official_20261109'
OUT = ROOT/'exports/tyrol_two_flags_20261109'
FF = os.environ.get('FFMPEG', 'ffmpeg')
PROBE = os.environ.get('FFPROBE', 'ffprobe')
FONT = Path('C:/Windows/Fonts')
CAST = {
 'GER':('白龙','德国','gpt/cloud_present','skeptical','#bcd8ff'),
 'FRA':('克莱德','法国','claude/book_hold','questioning','#f2d3f8'),
 'ITA':('基米','意大利','gemini/cat_paws','excited','#febf9f'),
 'AUS':('大肥鱼','奥地利','deepseek/notebook','questioning','#a9deff'),
 'SWI':('千问','瑞士','qwen/fan_thought','curious','#d5edbd'),
 'GLM':('大狐狸','智谱','glm/book_explain','amused','#ffdf9d')}
def rd(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def stamp(t):
    m,s=divmod(max(0,t),60)
    h,m=divmod(int(m),60)
    return f'{h:02}:{m:02}:{s:06.3f}'
def probe(p):
    v=subprocess.check_output([PROBE,'-v','error','-show_entries','format=duration:stream=codec_type','-of','json',str(p)])
    return float(json.loads(v)['format']['duration'])
@lru_cache(None)
def font(size,bold=False): return ImageFont.truetype(str(FONT/('msyhbd.ttc' if bold else 'msyh.ttc')),size)
def draw_text(d,pos,text,size,color,bold=False):d.text(pos,text,font=font(size,bold),fill=color)
def wrapped(d,text,max_width,size):
    lines=[];current=''
    for ch in text:
        if ch=='\n' or (current and d.textlength(current+ch,font=font(size,True))>max_width):
            lines.append(current);current='' if ch=='\n' else ch
        else:current+=ch
    if current:lines.append(current)
    return lines
@lru_cache(None)
def province_panel():
    import numpy as np
    sysdir=Path(os.environ['HOI4_DIR']) / 'map'
    raw=np.asarray(Image.open(sysdir/'provinces.bmp').convert('RGB').crop((2980,577,3033,606)))
    definitions=[row.split(';') for row in (sysdir/'definition.csv').read_bytes().decode('latin1').splitlines()]
    land=np.zeros((*raw.shape[:2],3),dtype=np.uint8);land[:]=[52,77,86]
    edges=np.any(raw!=np.roll(raw,1,0),2)|np.any(raw!=np.roll(raw,1,1),2)
    for row in definitions:
        if row[0] in ('671','6673'):
            color=list(map(int,row[1:4]));mask=(raw==color).all(2)
            if not mask.any():raise ValueError('Focus province is outside the cutout')
            land[mask]=[232,186,95] if row[0]=='671' else [113,202,173]
    land[edges]=[32,51,60]
    pane=Image.new('RGBA',(730,382),(16,33,43,250))
    pane.alpha_composite(Image.fromarray(land).resize((698,280),Image.Resampling.NEAREST).convert('RGBA'),(16,14))
    dd=ImageDraw.Draw(pane);draw_text(dd,(28,302),'671   奥地利',28,'#ffd88f',True);draw_text(dd,(350,302),'6673   意大利',28,'#99ebcf',True)
    return pane
def overlay(path,clip,item,quote,tag,talk):
    image=Image.new('RGBA',(1280,720));d=ImageDraw.Draw(image)
    name,country,pose,face,color=CAST[tag]
    is_reveal=item['kind']=='scenario_goal'; is_analysis=item['kind']=='commentary'; is_map=item['kind']=='scenario_map'
    box=(38,26,78+int(d.textlength(clip['title'],font=font(29,True))),80)
    d.rounded_rectangle(box,13,fill=(10,27,37,238),outline=(99,126,122,210),width=1)
    draw_text(d,(58,32),clip['title'],29,'#fff3d9',True)
    if is_map:
        if item['text']!='671号省由AUS控制。6673号省由ITA控制': raise ValueError('Map labels differ from final evidence')
        image.alpha_composite(province_panel(),(64,99))
    if not is_reveal and not is_analysis and not is_map:
        portrait=Image.open(ROOT/f'assets/characters/poses/{pose}/{face}{"_talk" if talk else ""}.png').convert('RGBA')
        portrait=ImageOps.contain(portrait.crop((0,0,portrait.width,1070)),(355,365),Image.Resampling.LANCZOS)
        image.alpha_composite(portrait,(1265-portrait.width,550-portrait.height))
    d.rounded_rectangle((33,506,1247,695),16,fill=(12,29,40,245),outline=(115,149,148,235),width=2)
    d.rounded_rectangle((52,536,60,668),3,fill=color)
    label=(f'目标揭晓   /   {country} · {name}' if is_reveal else '存档读回   /   蒂罗尔两块焦点省份' if is_map else '赛后解说   /   智谱娘' if is_analysis else f'{country} · {name}   /   '+('桌边主持' if tag=='GLM' else '第二轮回应' if item.get('data',{}).get('round')==2 else '公开发言'))
    draw_text(d,(81,522),label,25,color,True)
    draw_text(d,(877,529),f'第 {clip["turn"]} 回合  ·  终局地图补录',21,'#c3c5c5')
    for size in [39,36,33,30,27,24]:
        lines=wrapped(d,('第七天收场，两面旗仍在原位。' if is_map else quote),1120,size)
        if len(lines)<=2:break
    if len(lines)>2:raise ValueError('quote too long for two caption lines: '+quote)
    for i,line in enumerate(lines):draw_text(d,(81,570+i*49),line,size,'#fffcf6',True)
    image.save(path)
def cut(clips):
    OUT.mkdir(parents=True,exist_ok=True);build=WORK/'cuts';build.mkdir(exist_ok=True)
    receipts=[]; report=[]
    for p in sorted(WORK.glob('recording.segment*.json'))+[RUN/'recording.json']:
        r=rd(p)
        if not r.get('output_path'):continue
        src=Path(r['output_path']);start=datetime.fromisoformat(r['started_at']).timestamp()
        if any(seg[0]==src for seg in receipts):continue
        receipts.append((src,start,probe(src)))
    receipts.sort(key=lambda x:x[1])
    if not receipts:raise RuntimeError('No finalized recording')
    for n,clip in enumerate(clips,1):
        turn=clip['turn']
        shots=[];current=0;last=None
        for j,cue in enumerate(clip['cues'],1):
            tag,kind,quote=cue[:3]; shot_turn=cue[3] if len(cue)>3 else turn
            shot_clip={**clip,'turn':shot_turn}
            events=rd(RUN/'episodes_postmatch'/f'turn_{shot_turn:03d}'/'episode.json')['events']
            hits=[e for e in events if e['kind']==kind and (tag=='GLM' or e['country']==tag) and quote in e['text']]
            if len(hits)!=1:raise ValueError((turn,kind,tag,quote,len(hits)))
            e=hits[0]
            if e.get('recorded_at'):
                moment=datetime.fromisoformat(e['recorded_at']).timestamp()
                available=[x for x in receipts if x[1]-.5<=moment<=x[1]+x[2]+1]
                if not available:raise ValueError('No recording covers '+str(e))
                src,epoch,total=available[-1];offset=min(total-.1,max(0,moment-epoch))
                last=(src,epoch,total,offset)
            elif last:
                src,epoch,total,offset=last
            else:raise ValueError('The first cue needs a live timestamp')
            duration=min(10,max(3.75,math.ceil(len(quote)/8*4)/4))
            pos=max(0,min(offset-1.0,total-duration))
            start=build/f'{n}-{j}-normal.png';opened=build/f'{n}-{j}-talk.png'
            overlay(start,shot_clip,e,quote,tag,False);overlay(opened,shot_clip,e,quote,tag,True)
            part=build/f'{n}-{j}.mp4'
            filter_graph="[1:v][2:v]overlay=format=auto:enable='lt(mod(t,0.43),0.16)'[cap];[0:v]fps=30[game];[game][cap]overlay=0:0:format=auto,format=yuv420p[v]"
            args=[FF,'-nostdin','-loglevel','error','-y','-stream_loop','-1','-i',str(OUT/'终局净地图.mp4'),'-loop','1','-framerate','30','-i',str(start),'-loop','1','-framerate','30','-i',str(opened),'-t',str(duration),'-filter_complex',filter_graph,'-map','[v]','-an','-c:v','libx264','-preset','veryfast','-crf','19','-threads','3',str(part)]
            subprocess.run(args,check=True)
            shots.append({'turn':shot_turn,'event':e['id'],'kind':kind,'speaker':tag,'quote':quote,'raw':str(src),'raw_in':round(pos,3),'raw_out':round(pos+duration,3),'call_timecode':round(offset,3) if e.get('recorded_at') else None,'edit_in':current,'edit_out':current+duration,'source':e['source'],'visual_source':'终局净地图.mp4（暂停后补录）','on_screen_text':('第七天收场，两面旗仍在原位。' if kind=='scenario_map' else quote)})
            current+=duration
        parts=build/f'parts{n}.txt';parts.write_text(''.join(f"file '{n}-{j}.mp4'\n" for j in range(1,len(shots)+1)),'ascii')
        video=OUT/f'{n:02d}-{clip["title"]}.mp4'
        subprocess.run([FF,'-nostdin','-loglevel','error','-y','-f','concat','-safe','0','-i',str(parts),'-c','copy','-movflags','+faststart',str(video)],check=True)
        actual=probe(video)
        if abs(current-actual)>.4:raise ValueError((video,current,actual))
        srt=[]
        for i,s in enumerate(shots,1):
            srt.append(f"{i}\n{stamp(s['edit_in']).replace('.',',')} --> {stamp(s['edit_out']).replace('.',',')}\n{s['on_screen_text']}\n")
        video.with_suffix('.srt').write_text('\n'.join(srt),encoding='utf-8-sig')
        report.append({**{k:v for k,v in clip.items() if k!='cues'},'video':video.name,'duration':actual,'shots':shots})
    (OUT/'选段来源与时间码.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
if __name__=='__main__':cut(rd(Path(__file__).with_name('selections.json')))
