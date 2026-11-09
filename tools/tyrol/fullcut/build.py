from plan import *
from pathlib import Path
from functools import lru_cache
import math,re,sys,hashlib,csv,subprocess,os
from PIL import Image,ImageDraw,ImageFont,ImageOps,ImageEnhance
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cut import province_panel,CAST,FF
fps=25
deliver=out/'完整静音粗剪';pack=deliver/'服务器配音包';build=work/'render';imgs=build/'layers'
for d in [deliver,pack,build,imgs]:d.mkdir(parents=True,exist_ok=True)
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n','utf8')
def tc(f):
 sec,frame=divmod(f,25);m,s=divmod(sec,60);h,m=divmod(m,60);return f'{h:02}:{m:02}:{s:02}:{frame:02}'
def srt_clock(f):
 ms=f*40;s,ms=divmod(ms,1000);m,s=divmod(s,60);h,m=divmod(m,60);return f'{h:02}:{m:02}:{s:02},{ms:03}'
def wav_text(t):return t.replace('6673','六六七三').replace('671','六七一').replace('119','一一九')
def clean_len(t):return len(re.sub(r'[^\w\u4e00-\u9fff]','',wav_text(t)))
@lru_cache(None)
def font(n,b=False):return ImageFont.truetype('C:/Windows/Fonts/msyh'+('bd' if b else '')+'.ttc',n)
def words(d,x,y,t,n,ink='#f8f2e5',bold=False,anchor=None):d.text((x,y),t,font=font(n,bold),fill=ink,anchor=anchor)
def split_text(t):
 parts=re.findall(r'.+?(?:[。！？?!，,；;：:、]+[”’」』]*|$)',t)
 assert ''.join(parts)==t,(parts,t)
 groups=[];cur=''
 for s in parts:
  if cur and (len(cur+s)>52 or (len(cur)>21 and cur.rstrip('”’」』').endswith(('。','？','！','?','!')))):groups.append(cur);cur=''
  cur+=s
 if cur:groups.append(cur)
 return groups
def balanced(t,width=1080):
 for pt in (40,38,36,34,32,30):
  f=font(pt,True)
  if f.getlength(t)<=width:return [t],pt
  options=[j for j in range(1,len(t)) if t[j] not in '，,.。！!？?；;：:、—”’」』' and not(t[j].isascii() and t[j].isalnum() and t[j-1].isascii() and t[j-1].isalnum()) and max(f.getlength(t[:j]),f.getlength(t[j:]))<=width]
  if options:
   cut=min(options,key=lambda j:abs(f.getlength(t[:j])-f.getlength(t[j:]))-(220 if t[j-1] in '，,。！!？?；;：:、' else 0))
   return [t[:cut],t[cut:]],pt
 raise ValueError(t)
R=Image.Resampling.LANCZOS
bg=Image.open(root/'assets/stage/academy/backdrops/map_focus.png').convert('RGBA').resize((1280,720),R)
council=Image.open(root/'assets/stage/backdrops/council.png').convert('RGBA').resize((1280,720),R)
mapim=province_panel()
clean=Image.open(out/'终局净地图.png').convert('RGBA')
faces={'GLM':['neutral','amused','deadpan'],'GER':['confident','skeptical','stern'],'FRA':['questioning','amused','concerned'],'ITA':['excited','pout','surprised'],'AUS':['questioning','happy','sleepy'],'SWI':['curious','confident','delighted']}
colors={k:v[4] for k,v in CAST.items()}
countries={'GLM':'主持','GER':'德国','FRA':'法国','ITA':'意大利','AUS':'奥地利','SWI':'瑞士'}
@lru_cache(256)
def sprite(tag,face,mouth,active,w,h):
 path=root/f'assets/characters/poses/{CAST[tag][2]}/{face}{"_talk" if mouth else ""}.png'
 im=Image.open(path).convert('RGBA'); im=im.crop((0,0,im.width,1060));im=ImageOps.contain(im,(w,h),R)
 if not active:
  a=im.getchannel('A');im=ImageEnhance.Brightness(im.convert('RGB')).enhance(.62).convert('RGBA');im.putalpha(a)
 return im
def actor(im,tag,x,bottom,w,h,face='neutral',mouth=False,active=True):
 art=sprite(tag,face,mouth,active,w,h);im.alpha_composite(art,(int(x-art.width/2),bottom-art.height))
def tablet(d,coords,fill=(14,30,39,245)): d.rounded_rectangle(coords,15,fill=fill,outline=(97,132,133,230),width=2)
def heading(d,topic):
 w=font(27,True).getlength(topic);tablet(d,(37,26,67+int(w),76));words(d,52,30,topic,27,'#ffdf9f',True)
def phase_face(tag,sec):
 return faces[tag][0 if sec<2 else 1 if sec in (2,3) else 2]
def stage_image(row,idx,mouth):
 tag=row['tag'];st=row['style'];sec=row['section'];other=row['other']
 if st in ('intro','roster'):
  im=bg.copy();im.paste(ImageOps.fit(clean.convert('RGB'),(918,515),method=R),(42,40))
  d=ImageDraw.Draw(im)
  if st=='intro':
   tablet(d,(70,103,774,436));words(d,103,130,'IRON   /   五国实局',25,'#9ed8c8',True)
   words(d,96,182,'蒂罗尔的两面旗',67,'#fff0cd',True);words(d,104,315,'1936 年 9 月 4 日',30,'#e2eee6');words(d,104,365,'七个游戏日后，回到同一张地图。',28,'#c1dada')
  else:
   tablet(d,(62,77,911,465));words(d,90,100,'今天谁在桌边',34,'#ffe0a3',True)
   for j,t in enumerate(['AUS','ITA','GER','FRA','SWI']):
    cx=158+165*j;actor(im,t,cx,367,140,186,faces[t][0]);d=ImageDraw.Draw(im)
    words(d,cx,380,names[t],26,colors[t],True,'mt');words(d,cx,421,countries[t],20,'#dae7e3',False,'mt')
  actor(im,'GLM',1040,550,465,480,faces['GLM'][0],mouth)
 elif st in ('map','pairmap','mapend','movement'):
  im=Image.new('RGBA',(1280,720));d=ImageDraw.Draw(im)
  if st=='movement':
   tablet(d,(62,106,820,451));words(d,88,129,'七日存档  /  师位置核对',30,'#ffdf9f',True)
   words(d,89,185,'换过省份的师',36,'#f1f5ee',True)
   for k,(t,v) in enumerate(changed.items()):
    cx=149+141*k;words(d,cx,257,str(v),52,colors[t],True,'mt');words(d,cx,340,countries[t],22,'#e9ebe7',False,'mt')
   words(d,89,403,'相邻两日存档对照，同一师只计一次。',22,'#bbcdca')
  else:
   tablet(d,(53,89,821,479));im.alpha_composite(mapim.resize((675,351),R),(94,113))
  if st=='pairmap':
   actor(im,'AUS',944,509,300,315,faces['AUS'][0]);actor(im,'ITA',1145,501,263,284,faces['ITA'][0])
  else:actor(im,'GLM',1061,542,397,430,'amused' if st=='mapend' else 'neutral',mouth)
 elif st=='goals':
  im=council.copy();d=ImageDraw.Draw(im);tablet(d,(240,87,1040,371))
  words(d,640,117,'赛后揭卡',34,'#e9cf93',True,'mt')
  ts=other.split('/')
  concepts={'AUS':'收回控制区，自己定边界','ITA':'保住现有控制区','FRA':'保留介入的选择','GER':'给奥地利留余地','SWI':'平安，也不欠人情'}
  for j,t in enumerate(ts):
   cy=196+j*70;words(d,640,cy,countries[t]+'  /  '+concepts[t],33,colors[t],True,'mt')
  actor(im,ts[0],201,548,392,420,faces[ts[0]][1]);actor(im,ts[-1] if len(ts)>1 else 'GLM',1090,548,392,420,'neutral')
 else:
  im=Image.new('RGBA',(1280,720));d=ImageDraw.Draw(im);d.rectangle((0,0,1280,720),fill=(4,17,27,55))
  # Return cuts keep a participant on the same side whenever the next speaker answers them.
  x=213 if row['left'] else 1067;ox=1280-x
  actor(im,other,ox,550,397,407,'neutral',False,False)
  actor(im,tag,x,552,465,476,phase_face(tag,sec),mouth,True)
 d=ImageDraw.Draw(im)
 if st not in ('intro','roster'):heading(d,chap[sec])
 if st in ('map','mapend','movement','pairmap','table'):
  marker=(f'第 {row["turn"]} 天  ·  ' if row['turn'] else '')+'终局净地图补录'
  w=int(font(20).getlength(marker));d.rounded_rectangle((1249-w-22,28,1254,65),8,fill=(15,34,42,212));words(d,1240-w,31,marker,20,'#dae5df')
 return im
def overlay(row,idx,frag,mouth):
 im=stage_image(row,idx,mouth);d=ImageDraw.Draw(im);tag=row['tag']
 tablet(d,(34,514,1246,696),(12,28,38,249));d.rounded_rectangle((55,542,62,666),3,fill=colors[tag])
 role=('开场介绍' if row['kind']=='recorded_intro' else '观众解说' if row['kind']=='audience_narration' else '桌边主持' if row['kind']=='host' else countries[tag]+'公开发言')
 words(d,82,527,names[tag]+'  /  '+role,24,colors[tag],True)
 lines,pt=balanced(frag);y=588 if len(lines)==1 else 569
 for k,line in enumerate(lines):words(d,82,y+k*47,line,pt,'#fffaf0',True)
 return im
movement=read(out/'七日师位置核对.json')['distinct_divisions_with_observed_province_change']
changed={t:movement[t] for t in ('AUS','ITA','SWI','FRA','GER')}
# Keep the full record/answer adjacency and the ordering of first-round and host remarks.
rows.insert(7,rows.pop(8)) if rows[7]['source']=='T1-E012' and rows[8]['source']=='T1-E007' else None
chunks=[];lines=[];cues=[];frame=0;previous_tag=None;previous_left=True;chapters=[]
def add_chapter(n):
 if not chapters or chapters[-1]['name']!=chap[n]:chapters.append({'name':chap[n],'start_frame':frame})
def main_text(row,idx,start,end):
 txt=row['text'];k=row['tag'];src=row['source'];lid=hashlib.sha256((src+'\0'+casts[k]+'\0'+txt).encode()).hexdigest()[:20]
 return {'line_id':lid,'character_id':casts[k],'text':txt,'event_id':src,'visibility':'audience_only' if row['kind']=='audience_narration' else 'public','emotion':phase_face(k,row['section']),'speaker':names[k],'section':chap[row['section']],'source_kind':row['kind'],'refs':row['refs'],'start_frame':start,'end_frame':end}
for n,row in enumerate(rows):
 add_chapter(row['section']);start=frame
 row['left']=previous_left if row['tag']==previous_tag else not previous_left;previous_tag=row['tag'];previous_left=row['left']
 for j,frag in enumerate(split_text(row['text'])):
  count=max(52,round((clean_len(frag)/(5.8 if row['kind'] in ('audience_narration','recorded_intro') else 6.0)+.52)*fps))
  for opened in (0,1):
   art=overlay(row,n,frag,bool(opened));art.save(imgs/f'{n:03}-{j}-{opened}.png',compress_level=2)
  part={'start_frame':frame,'end_frame':frame+count,'text':frag,'speaker':names[row['tag']],'line_index':len(lines)}
  cues.append(part);frame+=count
  for offset in range(0,count,12):
   remaining=min(12,count-offset);on=min(5,remaining) if offset<count-13 else 0
   for opened,frames in ((1,on),(0,remaining-on)):
    if frames:chunks.extend([f"file 'layers/{n:03}-{j}-{opened}.png'",f'duration {frames/fps:.2f}'])
 lines.append(main_text(row,n,start,frame))
# Prefix and existing complete closing are joined on the same frame grid.
prefix_frames=frame
closing=out/'公开总结大会'
cr=[json.loads(s) for s in (closing/'voice_lines.jsonl').read_text('utf8').splitlines()]
ct=read(closing/'剪辑时间轴.json');assert ct['fps']==fps and len(cr)==len(ct['chapters'])==13
add_chapter(7)
for r,t in zip(cr,ct['chapters']):
 a=prefix_frames+round(t['start_seconds']*fps);b=prefix_frames+round(t['end_seconds']*fps)
 assert r['meeting_id']==t['id'] and a==frame
 lines.append({**{k:r[k] for k in ('line_id','character_id','text','event_id','visibility','emotion','speaker','refs')},'section':chap[7],'source_kind':'public_meeting','start_frame':a,'end_frame':b})
 frame=b
def fromclock(s):
 h,m,rest=s.split(':');sec,ms=rest.split(',');f=(int(h)*3600+int(m)*60+int(sec))*25+int(ms)/40;assert f==round(f);return round(f)
for block in re.split(r'\n\s*\n',(closing/'公开总结大会.srt').read_text('utf-8-sig').strip()):
 no,interval,body=block.split('\n',2);sp,frag=body.split('　',1);a,b=[prefix_frames+fromclock(s) for s in interval.split(' --> ')]
 ix=next(i for i,r in enumerate(lines) if r['start_frame']<=a<b<=r['end_frame']);assert sp==lines[ix]['speaker']
 cues.append({'start_frame':a,'end_frame':b,'text':frag,'speaker':sp,'line_index':ix})
for i,line in enumerate(lines,1):
 a,b=line['start_frame'],line['end_frame'];line.update(sequence=i,start_timecode=tc(a),end_timecode=tc(b),start_ms=a*40,end_ms=b*40,duration_ms=(b-a)*40,wav_name=f'{i:03d}_{line["character_id"]}_{line["line_id"]}.wav',tts_text=wav_text(line['text']))
 assert ''.join(c['text'] for c in cues if c['line_index']==i-1)==line['text']
for i,c in enumerate(chapters):c['end_frame']=chapters[i+1]['start_frame'] if i+1<len(chapters) else frame
save(work/'timeline.json',{'fps':25,'frames':frame,'prefix_frames':prefix_frames,'chapters':chapters,'lines':lines,'subtitle_cues':cues})
(build/'prefix.ffconcat').write_text('ffconcat version 1.0\n'+'\n'.join(chunks+[chunks[-2]])+'\n','ascii')
def ff(args):subprocess.run([FF,'-hide_banner','-loglevel','error','-nostdin','-y',*map(str,args)],check=True)
print('Compositing',prefix_frames,'frames;',len(lines),'voice cues in total',flush=True)
ff(['-stream_loop','-1','-i',out/'终局净地图.mp4','-f','concat','-safe','0','-i',build/'prefix.ffconcat','-filter_complex','[0:v]fps=25,setsar=1[game];[1:v]fps=25,format=rgba[layer];[game][layer]overlay=shortest=0:repeatlast=1,format=yuv420p[v]','-map','[v]','-frames:v',prefix_frames,'-r',25,'-an','-c:v','libx264','-threads','5','-preset','veryfast','-crf','18',build/'main.mp4'])
print('Joining full closing',flush=True)
meta=[';FFMETADATA1']
for c in chapters:meta+=['[CHAPTER]','TIMEBASE=1/25','START='+str(c['start_frame']),'END='+str(c['end_frame']),'title='+c['name']]
(build/'chapter.txt').write_text('\n'.join(meta)+'\n','utf8')
video=deliver/'蒂罗尔的两面旗·完整静音粗剪.mp4'
# Re-encode across the single junction to guarantee identical H.264 parameters and frame timestamps.
ff(['-i',build/'main.mp4','-i',closing/'蒂罗尔的两面旗·公开总结大会.mp4','-f','ffmetadata','-i',build/'chapter.txt','-filter_complex','[0:v]setsar=1,setpts=PTS-STARTPTS[a];[1:v]setsar=1,setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1:a=0[v]','-map','[v]','-map_metadata','2','-map_chapters','2','-an','-r',25,'-c:v','libx264','-threads',6,'-preset','veryfast','-crf',18,'-movflags','+faststart',video])
print('Ready',video,tc(frame),flush=True)
