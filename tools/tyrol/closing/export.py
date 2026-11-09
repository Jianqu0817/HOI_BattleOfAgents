from pathlib import Path
from functools import lru_cache
import sys,json,html,hashlib,math,re,subprocess,tempfile
from PIL import Image,ImageDraw,ImageFont,ImageOps,ImageEnhance
root=Path.cwd()
sys.path[:0]=[str(root/'tools'),str(Path(__file__).resolve().parents[1])]
from production import make_lines
from cut import CAST,province_panel,FF
work=Path('runs/tyrol_official_20261109/closing'); work.mkdir(parents=True,exist_ok=True); rd=lambda p:json.loads(p.read_text('utf-8-sig'))
rows=rd(work/'approved-for-review.json'); sources=rd(work/'public_sources.json')
out=root/'exports/tyrol_two_flags_20261109/公开总结大会';out.mkdir(parents=True,exist_ok=True)
lib=root/'assets/corpus/tyrol_two_flags_20261109/公开总结大会';lib.mkdir(parents=True,exist_ok=True)
assert len(rows)==13 and len({r['speaker'] for r in rows})==6 and rows[-1]['speaker']=='GLM' and '下期' in rows[-1]['text']
known=set(sources)
for r in rows:
 assert r['visibility']=='public' and set(r['refs'])<=known and (not r.get('reply_to') or r['reply_to'] in known)
 known.add(r['id'])
assert all(r.get('reply_to')==rows[i-1]['id'] for i,r in enumerate(rows) if i>=7)
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n','utf8')
assign={r['speaker'] if r['speaker']!='GLM' else 'host':r['character_id'] for r in rows}
emotions={'GLM':['neutral','deadpan','amused'],'AUS':['neutral','questioning','happy'],'GER':['neutral','skeptical','confident'],'FRA':['neutral','questioning','amused'],'ITA':['neutral','surprised','excited'],'SWI':['neutral','curious','delighted']}
events=[]
for r in rows:
 tag=r['speaker'];stage=r['stage'];emotion=emotions[tag][0 if stage=='开场' else 1 if stage=='桌面复盘' else 2]
 events.append({'id':'TYROL7-'+r['id'],'kind':'host' if tag=='GLM' else 'public','country':None if tag=='GLM' else tag,'text':r['text'],'visibility':'public','data':{'emotion':emotion}})
vo=make_lines({'events':events},assign)
assert len(vo)==len(rows)
for i,(line,r) in enumerate(zip(vo,rows),1):
 assert line['text']==r['text'] and line['character_id']==r['character_id']
 line.update(sequence=i,meeting_id=r['id'],speaker=r['name'],stage=r['stage'],refs=r['refs'],reply_to=r.get('reply_to'),addressed_to=r.get('addressed_to'),session='蒂罗尔的两面旗 · 公开总结大会')
(lib/'voice_lines.jsonl').write_text(''.join(json.dumps(l,ensure_ascii=False)+'\n' for l in vo),'utf8')
used={ref for r in rows for ref in r['refs'] if ref in sources}
save(lib/'public_sources.json',{k:v for k,v in sources.items() if k in used})
head=['# 《蒂罗尔的两面旗》公开总结大会','','奥意开场已交战。第七天公开地图上，奥地利控制 671 号省，意大利控制 6673 号省。以下为赛后由六位角色新录的公开台词。','']
prev=None
for r in rows:
 if r['stage']!=prev:head+=['## '+r['stage'],''];prev=r['stage']
 head+=['**'+r['name']+'**　'+r['text'],'']
(out/'大会逐字稿.md').write_text('\n'.join(head),'utf8')
W,H=1280,720;stage=root/'assets/stage';res=Image.Resampling.LANCZOS
@lru_cache(None)
def font(sz,b=True):return ImageFont.truetype('C:/Windows/Fonts/msyh'+('bd' if b else '')+'.ttc',sz)
canvas=Image.open(stage/'backdrops/council.png').convert('RGBA').resize((W,H),res)
x,y,w,h=[round(z/3) for z in rd(stage/'layout.json')['screen_bounds']['council']]
screen=Image.new('RGBA',(W,H));screen.paste(ImageOps.fit(province_panel(),(w,h),method=res),(x,y));mask=Image.open(stage/'screen_masks/council.png').convert('L').resize((W,H),res);canvas.paste(screen,(0,0),mask)
fore=Image.open(stage/'foregrounds/debate_podiums.png').convert('RGBA').resize((W,H),res)
@lru_cache(None)
def sprite(tag,face,talking,active):
 pose=CAST[tag][2];im=Image.open(root/f'assets/characters/poses/{pose}/{face}{"_talk" if talking else ""}.png').convert('RGBA')
 im=im.crop((0,0,im.width,1060));im=ImageOps.contain(im,(465,480),res)
 if not active:
  a=im.getchannel('A');im=ImageEnhance.Brightness(im.convert('RGB')).enhance(.56).convert('RGBA');im.putalpha(a)
 return im
def draw_scene(line,other,speaker_left,frag,talking):
 im=canvas.copy()
 for tag,left,on in [(other,not speaker_left,False),(line['country'] or 'GLM',speaker_left,True)]:
  pose=sprite(tag,line['emotion'] if on else 'neutral',talking if on else False,on)
  center=263 if left else 1020;im.alpha_composite(pose,(center-pose.width//2,540-pose.height))
 im.alpha_composite(fore,(0,100));d=ImageDraw.Draw(im)
 label='公开总结大会   /   '+line['stage'];d.rounded_rectangle((34,24,49+int(font(26).getlength(label))+20,76),radius=12,fill=(18,34,44,245));d.text((51,32),label,font=font(26),fill='#fce7b6')
 d.rounded_rectangle((34,509,1246,698),16,fill=(12,26,37,250),outline='#819393',width=2)
 accent=CAST[line['country'] or 'GLM'][4];d.rounded_rectangle((55,542,63,666),3,fill=accent);d.text((84,523),line['speaker'],font=font(26),fill=accent)
 lines=[frag]
 if font(36).getlength(frag)>1110:
  candidates=[i for i in range(1,len(frag)) if frag[i] not in '，。！？；：、”’」』' and
              max(font(36).getlength(frag[:i]),font(36).getlength(frag[i:]))<=1110]
  split=min(candidates,key=lambda i:abs(i-len(frag)/2)-(4 if frag[i-1] in '，。！？；：、' else 0))
  lines=[frag[:split],frag[split:]]
 assert len(lines)<=2
 for i,l in enumerate(lines):d.text((84,570+48*i),l,font=font(36),fill='#fff8e9')
 return im.convert('RGB')
def chunks(text):
 parts=re.findall(r'.+?(?:[，。！？；：、—]+[”“’」』]*|$)',text)
 assert ''.join(parts)==text
 groups=[];cur=''
 for part in parts:
  if cur and (len(cur+part)>43 or (len(cur)>=24 and cur.rstrip('”」').endswith(('。','！','？')))):
   groups.append(cur);cur=''
  cur+=part
 if cur:groups.append(cur)
 assert ''.join(groups)==text
 return groups
def clock(fr):
 ms=fr*40;s,ms=divmod(ms,1000);m,s=divmod(s,60);h,m=divmod(m,60);return f'{h:02}:{m:02}:{s:02},{ms:03}'
clips=[];sub=[];pos=0;chapter=[]
with tempfile.TemporaryDirectory(prefix='iron-tyrol-close-') as tmp:
 build=Path(tmp)
 for i,(line,r) in enumerate(zip(vo,rows)):
  other=(rows[i-1]['speaker'] if i>=7 else 'GLM' if r['speaker']!='GLM' else 'ITA')
  start=pos
  for j,frag in enumerate(chunks(line['text'])):
   frames=max(57,round(len(frag)/6.2*25+8))
   for active in (False,True):
    img=draw_scene(line,other,i%2==0,frag,active);img.save(build/f'{i}-{j}-{int(active)}.jpg',quality=93,subsampling=0)
   for offset in range(0,frames,10):
    rem=min(frames-offset,10);opened=min(4,rem) if offset<frames-12 else 0
    for flag,n in [(1,opened),(0,rem-opened)]:
     if n:clips+=["file '"+f'{i}-{j}-{flag}.jpg'+"'",f'duration {n*.04:.2f}']
   sub+=[f'{len(sub)+1}\n{clock(pos)} --> {clock(pos+frames)}\n{line["speaker"]}　{frag}\n'];pos+=frames
  chapter.append({'id':r['id'],'speaker':r['name'],'stage':r['stage'],'start_seconds':start*.04,'end_seconds':pos*.04})
 last=clips[-2];(build/'closing.ffconcat').write_text('ffconcat version 1.0\n'+'\n'.join(clips+[last])+'\n','ascii')
 dest=out/'蒂罗尔的两面旗·公开总结大会.mp4'
 subprocess.run([FF,'-v','error','-nostdin','-y','-f','concat','-safe','0','-i',str(build/'closing.ffconcat'),'-vf','fps=25','-frames:v',str(pos),'-an','-c:v','libx264','-threads','4','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(dest)],check=True)
(out/'公开总结大会.srt').write_text('\n'.join(sub),'utf-8-sig')
save(out/'剪辑时间轴.json',{'fps':25,'duration_seconds':pos*.04,'chapters':chapter})
print('exported',len(rows),'lines',pos*.04,'seconds',flush=True)
