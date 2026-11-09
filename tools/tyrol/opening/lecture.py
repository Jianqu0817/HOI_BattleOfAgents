from functools import lru_cache
import json,math,subprocess
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance, ImageFilter
import os

ROOT=Path(__file__).resolve().parents[3]
W=ROOT/'runs/tyrol_opening_preview_v2'
OUT=ROOT/'exports/蒂罗尔开场/上半段样片修订版'
MOVIE=OUT/'蒂罗尔的两面旗·大狐狸开课·入坑邀请版.mp4'
FF=os.environ.get('FFMPEG','ffmpeg')

M=W/'media'; R=Image.Resampling.LANCZOS
FONTS=Path('C:/Windows/Fonts')
ivory='#fff5d8';gold='#eaca80';mint='#a9dfcf';rust='#b44b37';navy='#1b3035'

@lru_cache(None)
def ft(n,style='sans'):
    return ImageFont.truetype(str(FONTS/{'sans':'msyh.ttc','bold':'msyhbd.ttc','ink':'simkai.ttf','serif':'simsun.ttc','roman':'georgiab.ttf','mono':'consola.ttf'}[style]),n)
def txt(draw,x,y,t,n,col=ivory,style='sans',anchor=None):
    draw.text((x,y),t,font=ft(n,style),fill=col,anchor=anchor)
@lru_cache(None)
def src(path):return Image.open(path).convert('RGBA')
def fit(im,size):return ImageOps.fit(im,size,method=R)
def place(im,art,x,y):
    im.paste(art,(round(x),round(y)),art if art.mode=='RGBA' else None)
def framed(im,p,box,contain=False):
    x,y,x2,y2=box; size=(x2-x,y2-y)
    pic=ImageOps.contain(p,size,R) if contain else fit(p,size)
    place(im,pic,x+(size[0]-pic.width)/2,y+(size[1]-pic.height)/2)
@lru_cache(None)
def sprite(role,pose,face,mouth,h,cut):
    p=ROOT/f"assets/characters/poses/{role}/{pose}/{face}{'_talk' if mouth else ''}.png"
    im=src(p)
    if cut:im=im.crop((0,0,im.width,cut))
    alpha=im.getchannel('A');bb=alpha.point(lambda x:255 if x>26 else 0).getbbox()
    im=im.crop(bb)
    return im.resize((round(im.width*h/im.height),h),R)
def fox(im,x,bottom,h,face,mouth,pose='book_explain',cut=1030):
    a=sprite('glm',pose,face,mouth,h,cut);place(im,a,x-a.width/2,bottom-a.height)
def bgr(im):return cv2.cvtColor(np.asarray(im.convert('RGB')),cv2.COLOR_RGB2BGR)
@lru_cache(None)
def backdrop(name):
    p=ROOT/('assets/stage/academy/backdrops/map_focus.png' if name=='room' else f'assets/stage/backdrops/{name}.png')
    return src(p).resize((1280,720),R).convert('RGB')
@lru_cache(None)
def lectern():
    p=src(ROOT/'assets/stage/academy/foregrounds/side_lectern.png').resize((1280,720),R)
    # Outside the lectern the original layer has a few near-transparent matte pixels.
    return p.crop((977,444,1262,705))
@lru_cache(None)
def papertex():
    yy,xx=np.mgrid[0:470,0:900]
    n=np.random.default_rng(67).normal(0,1.6,xx.shape)+np.sin(yy/38)*.65
    return Image.fromarray(np.clip(np.array([235,223,193])+n[:,:,None],0,255).astype('uint8'))
@lru_cache(None)
def chalktex():
    im=src(ROOT/'assets/stage/academy/backdrops/map_focus.png')
    im=im.crop((270,190,2690,1450)).resize((900,470),R).convert('RGB')
    return Image.blend(im,Image.new('RGB',im.size,(20,53,55)),.81)
def ledger(stage):
    im=papertex().copy();d=ImageDraw.Draw(im)
    d.rectangle((19,17,880,452),outline='#93876d',width=2)
    txt(d,55,34,'本 指 挥 部 存 档',22,'#736645','ink')
    txt(d,55,77,'战后责任调查表',51,'#302d26','serif')
    d.line((54,158,845,158),fill='#797362',width=2)
    if stage==0:
        txt(d,60,201,'调查事项',24,'#746a50')
        txt(d,231,187,'部队为何原地不动？',36,navy,'ink')
        txt(d,58,300,'受询人',24,'#746a50');txt(d,231,289,'前线将军',37,navy,'ink')
    else:
        for j,t in enumerate(['步枪生产','卡车排产','进攻命令']):
            y=182+j*48;txt(d,63,y,t,24,'#554c3d')
            d.line((228,y+35,655,y+35),fill='#ac9e7d',width=1)
            if j<stage:txt(d,260,y-2,'本指挥',29,navy,'ink')
        if stage>=4:
            txt(d,68,354,'全处死每一个冤枉的',38,rust,'ink')
            txt(d,586,380,'最高指挥官签字',20,'#68614c')
            d.line((588,424,816,424),fill='#7b735e',width=2)
        if stage>=5:
            d.rounded_rectangle((650,194,844,261),7,outline=rust,width=4)
            txt(d,666,203,'将军放行',32,rust,'ink')
    return im

research=ROOT/'runs/m0_live/artifacts/20261108_072041/20261108_072046_research_slots.png'
production=ROOT/'runs/m0_live/artifacts/20261108_073134/20261108_073201_production_lines.png'
mapstill=ROOT/'exports/tyrol_two_flags_20261109/终局净地图.png'
def illustrative(im,word,label,x=42):
    d=ImageDraw.Draw(im);txt(d,x,30,label,23,gold,'bold');txt(d,x,97,word,52,ivory,'bold')
def faded(p):return ImageEnhance.Brightness(p.convert('RGB')).enhance(.53)
def board(sc,state):
    im=chalktex().copy();d=ImageDraw.Draw(im)
    if sc=='case':return ledger(0)
    if sc in ('type','draft','invite'):
        im=papertex().copy(); d=ImageDraw.Draw(im)
        txt(d,54,32,'社团活动邀请',45,navy,'serif')
        txt(d,58,111,'收件人　五位朋友',25,'#72674f')
        d.line((56,160,837,160),fill='#a59a7f',width=2)
        if sc=='type':
            txt(d,65,214,'来打钢四吗？',46,navy,'ink')
            if state:d.line((62,251,349,226),fill=rust,width=5)
        else:
            txt(d,65,198,'晚上来教室拍张合照，',43,navy,'ink')
            if state or sc=='invite':txt(d,65,274,'顺便帮我看一眼地图。',43,navy,'ink')
        if sc=='invite' and state:
            d.rounded_rectangle((622,344,838,419),7,outline=rust,width=4)
            txt(d,643,353,'已经寄出',37,rust,'ink')
        return im
    if sc=='paper':
        im=papertex().copy();d=ImageDraw.Draw(im)
        txt(d,65,45,'宣传片口播稿',34,navy,'ink')
        d.line((65,111,833,111),fill='#a19475',width=2)
        txt(d,68,142,'……邂逅国策各异的色块们。',38,navy,'serif')
        if state:txt(d,441,280,'下一页：抽卡',52,rust,'ink')
    elif sc=='lecture':
        base=fit(src(M/'load_6.png'),im.size);im=ImageEnhance.Brightness(base.convert('RGB')).enhance(.82)
    elif sc=='research':
        im=ImageOps.fit(src(research).crop((0,2,617,553)).convert('RGB'),(900,470),method=R,centering=(0,.05))
        d=ImageDraw.Draw(im); d.rounded_rectangle((581,360,877,444),8,fill=navy)
        txt(d,601,377,'科研 · 图纸',33,gold,'bold')
    elif sc in ('stock','zero','host'):
        if sc=='host':
            im=ledger(5)
            d=ImageDraw.Draw(im);d.rectangle((55,349,842,438),fill='#eaddc1')
            txt(d,62,359,'本场当班指挥：',33,navy,'ink')
            d.line((345,409,831,409), fill='#90846e',width=2)
            return im
        item='步兵装备' if sc=='stock' else '运输卡车'
        icon=src(M/('GER_infantry_equipment_1.png' if sc=='stock' else 'motorized_equipment_1.png'))
        im=papertex().copy();d=ImageDraw.Draw(im)
        txt(d,50,36,'装备库存   /   课堂推演',23,'#655b48')
        d.line((49,85,850,85),fill='#8b8065',width=2)
        txt(d,51,120,item,48,navy,'bold')
        icon=ImageOps.contain(icon,(350,180),R)
        # Game inventory icons are small: enlarge the illustration without distorting its proportions.
        scale=min(320/icon.width,135/icon.height);icon=icon.resize((int(icon.width*scale),int(icon.height*scale)),R)
        place(im,icon,91,241)
        txt(d,653,256,'0',128,rust,'roman',anchor='mm');txt(d,666,365,'库存',27,'#655b48','ink',anchor='mt')
    elif sc=='factory':
        im=fit(src(production).crop((0,60,617,548)),(900,470)).convert('RGB')
        if state:
            ui=src(M/'production_line.png'); overlay=Image.new('RGBA',im.size,(0,0,0,90));im=Image.alpha_composite(im.convert('RGBA'),overlay).convert('RGB')
            framed(im,ui,(65,133,836,322),True)
    elif sc in ('supply','truck'):
        im=faded(fit(src(mapstill),im.size));d=ImageDraw.Draw(im)
        d.rectangle((22,20,360,77),fill='#18303a');txt(d,39,29,'补给线路 · 教学示意',28,gold,'bold')
        a=(118,269);b=(462,199);c=(792,279)
        d.line((a,(254,266),(335,202),b),fill='#e4ce8b',width=11)
        for x,y in (a,b,c):d.ellipse((x-12,y-12,x+12,y+12),fill=ivory,outline='#1a3b42',width=3)
        for x,y,t in [(90,310,'首都'),(399,132,'补给中心'),(753,319,'前线')]:
            d.rounded_rectangle((x-8,y-2,x+len(t)*29+8,y+43),5,fill='#1c343c');txt(d,x,y,t,28,ivory,'bold')
        for n in range(13):
            x=487+22*n;y=206+5.3*n;d.line((x,y,x+12,y+3),fill='#dccdba' if state==0 else '#eb9482',width=5)
        if sc=='truck':
            truck=src(M/'motorized_equipment_1.png');framed(im,truck,(510,225,740,309),True)
    elif sc in ('inquiry','sign'):return ledger(5 if sc=='sign' else min(4,state+1))
    elif sc=='more':
        im=fit(src(M/('load_8.png' if state==0 else 'load_3.png')),(900,470)).convert('RGB')
    elif sc=='mods':
        txt(d,44,28,'Steam 创意工坊',39,ivory,'bold')
        for j,(t,detail) in enumerate([('国家实力排名','统计'),('人名和部队名称汉化补充','汉化'),('傀儡使用宗主颜色','地图')]):
            y=113+107*j;d.line((45,y-12,855,y-12),fill='#6d9290',width=1)
            txt(d,50,y+5,t,32,ivory);txt(d,788,y+11,detail,23,gold,'ink')
    elif sc=='kaiser':
        im=faded(fit(src(M/'load_6.png'),im.size));d=ImageDraw.Draw(im)
        txt(d,51,70,'Kaiserreich',69,ivory,'roman');txt(d,56,191,'如果德国赢了一战？',48,gold,'serif')
    elif sc in ('boast','invite'):
        txt(d,454,75,'《蒂罗尔的两面旗》',58,ivory,'ink',anchor='mt')
        for i,n in enumerate(['开始游戏','邀请玩家']):
            x=98+i*369;on=(i==(1 if sc=='invite' else 0))
            d.rounded_rectangle((x,263,x+328,353),11,fill='#d3c184' if on else '#253f43',outline='#acbfad',width=2)
            txt(d,x+164,282,n,35,navy if on else ivory,'bold',anchor='mt')
    elif sc in ('five','cut'):
        return im
    return im.convert('RGB')

@lru_cache(64)
def rack(count):
    im=backdrop('council').copy()
    shadow=Image.new('RGBA',im.size,(5,17,29,130))
    im=Image.alpha_composite(im.convert('RGBA'),shadow).convert('RGB')
    for i,(role,pose) in enumerate([('deepseek','maid_greeting'),('gpt','cloud_greeting'),('gemini','cat_paws'),('claude','book_hold'),('qwen','fan_greeting')]):
        a=sprite(role,pose,'neutral',False,455,1120)
        # Give each guest a distinct outline across the row. Hide the cut edges behind the desk.
        if a.width>248:
            a=a.resize((248,round(a.height*248/a.width)),R)
        mask=a.getchannel('A');solid=Image.new('RGBA',a.size,'#101c29' if i<count else '#273340');solid.putalpha(mask)
        edge=Image.new('RGBA',a.size,'#c8aaa0' if i<count else '#5f6a70');edge.putalpha(mask)
        x=132+253*i-a.width//2;place(im,edge,x-2,577-a.height-2);place(im,solid,x,577-a.height)
        if i<count:
            d=ImageDraw.Draw(im);d.ellipse((123+253*i,601,131+253*i,609),fill=gold)
    d=ImageDraw.Draw(im)
    d.rectangle((0,574,1280,588),fill='#503d34')
    d.line((0,574,1280,574),fill='#b69a6e',width=3)
    d.rectangle((0,588,1280,640),fill='#302b28')
    for i in range(count):
        x=132+253*i;d.ellipse((x-4,608,x+4,616),fill=gold)
    return im

def overlay_for_map(sc,mouth,state):
    im=Image.new('RGBA',(1280,720));d=ImageDraw.Draw(im)
    if sc in ('map','italy'):
        fox(im,224,735,554,'neutral' if sc=='map' else 'amused',mouth,cut=1000)
        if state and sc=='map':
            d=ImageDraw.Draw(im);d.rounded_rectangle((757,104,1185,178),14,fill=(20,31,45,192))
            txt(d,782,119,'OTL  /  原本的时间线',30,ivory,'bold')
        if sc=='italy' and state:
            d=ImageDraw.Draw(im);txt(d,902,186,'♥',66,'#ffd2d2','sans')
    elif sc=='arrow':
        fox(im,1060,735,471,'amused',mouth,cut=1050)
    return im

@lru_cache(32)
def layer(sc,mouth,state):
    im=overlay_for_map(sc,mouth,state)
    return np.array(im)[:,:,:3][:,:,::-1].copy(),np.array(im.getchannel('A'))[:,:,None]

@lru_cache(70)
def still(sc,mouth,state):
    if sc=='cover':
        p=src(M/'cover.jpg');im=fit(p,(1280,720)).filter(ImageFilter.GaussianBlur(16)).convert('RGB')
        im=ImageEnhance.Brightness(im).enhance(.36);framed(im,p,(0,0,1280,720),True)
    elif sc=='meme':
        im=Image.new('RGB',(1280,720),'#101015');framed(im,src(M/'meme.jpg'),(0,0,1280,648),True)
    elif sc=='cute' and state>=2:
        im=fit(src(ROOT/'assets/characters/cutaways/glm/lectern_nap.png'),(1280,720)).convert('RGB')
    elif sc in ('five','cut'):
        im=rack(5 if sc=='cut' else state).copy()
    elif sc in ('cute','confess','no_move','boast') and (sc!='boast' or state==0):
        im=backdrop('speaker').copy()
        if sc=='cute':face=['shy','deadpan'][min(state,1)];pose='book_thought'
        elif sc=='confess':face='amused' if state==0 else 'deadpan';pose='book_thought'
        elif sc=='no_move':face='deadpan';pose='book_thought'
        else:face='amused';pose='book_explain'
        fox(im,656,849,773,face,mouth,pose,950)
    else:
        im=backdrop('room').copy()
        slide=board(sc,state);im.paste(slide.resize((907,475),R),(43,41))
        face='annoyed' if sc in ('zero','inquiry') else 'deadpan' if sc in ('paper','sign','stock') else 'amused' if sc in ('boast','invite','order','case') else 'neutral'
        pose='book_thought' if sc in ('paper','zero','sign','case','inquiry') else 'book_explain'
        fox(im,1092,729,605,face,mouth,pose,1040)
        place(im,lectern(),977,444)
    return bgr(im)

def blend(base,ov,a):
    return cv2.blendLinear(base,ov,(255-a[:,:,0]).astype('f4')/255.,a[:,:,0].astype('f4')/255.)

def arrow(frame,frac):
    points=np.array([[366,405],[451,365],[544,353],[649,314],[728,287]],'f4')
    n=min(3,int(frac*4));t=frac*4-n
    now=np.vstack([points[:n+1],points[n]+(points[n+1]-points[n])*t]).astype('i4')
    cv2.polylines(frame,[now],False,(28,53,50),11,cv2.LINE_AA)
    cv2.polylines(frame,[now],False,(124,217,241),6,cv2.LINE_AA)
    if frac>=.995:cv2.arrowedLine(frame,(672,305),(727,287),(124,217,241),7,cv2.LINE_AA,tipLength=.42)

def grade(frame,sc):
    # A soft bottom shadow keeps white movie subtitles legible without masking the map.
    if sc in ('cover','meme'):return frame
    result=frame.copy()
    result[586:]=cv2.multiply(frame[586:],GRAD,scale=1/255.)
    return result
GRAD=np.broadcast_to(np.linspace(255,120,134,dtype='uint8')[:,None,None],(134,1280,3)).copy()
@lru_cache(80)
def graded(sc,mouth,state):return grade(still(sc,mouth,state),sc)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    t=json.loads((W/'timeline.json').read_text('utf8'))
    cap=cv2.VideoCapture(str(M/'maproll.mp4'))
    assert cap.isOpened()
    bgmap=bgr(fit(src(mapstill),(1280,720)))
    rng=np.random.default_rng(18)
    cmd=[FF,'-v','error','-nostdin','-y','-f','rawvideo','-pixel_format','bgr24','-video_size','1280x720','-framerate','25','-i','pipe:0',
         '-an','-c:v','libx264','-threads','5','-preset','veryfast','-crf','16','-pix_fmt','yuv420p',str(W/'picture.mp4')]
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
    for shot in t['shots']:
        sc=shot['shot']; n=shot['frames'];a=shot['audio_frames']; lead=shot['lead']
        for f in range(n):
            k=f-lead; activity=0<=k<a and shot['talking'][k]
            mouth=bool(activity and (k//3)%3!=0)
            u=max(0,min(1,k/max(1,a-1)))
            state=0
            if sc=='map':state=int(shot['i']==3 and u>.19)
            if sc=='italy':state=int(u>.48)
            if sc=='paper':state=int(u>.32)
            if sc=='cute':state=0 if u<.40 else 1 if u<.67 else 2
            if sc=='confess':state=int(u>.72)
            if sc in ('factory','supply','more','boast','draft'):state=int(u>.54)
            if sc=='type':state=int(u>.70)
            if sc=='invite':state=int(u>.84)
            if sc=='inquiry':state=min(3,int(u*3.6))
            if sc=='five':state=min(5,1+int(f/6.75))
            if sc in ('map','italy','arrow'):
                ok,frame=cap.read()
                if not ok:cap.set(cv2.CAP_PROP_POS_FRAMES,0);ok,frame=cap.read()
                if not ok:raise ValueError('Unreadable source map')
                if sc=='arrow':arrow(frame,min(1,max(0,(u-.28)/.40)))
                ov,alpha=layer(sc,mouth,state)
                frame=grade(blend(frame,ov,alpha),sc)
                if sc=='italy' and f>=n-16:
                    if f<n-5:
                        bands=np.array([[168,171,174],[62,190,194],[193,179,86],[80,156,81],[143,71,167],[103,69,185],[155,83,66]],dtype='i2')
                        base=np.repeat(bands[None,:,:],180,axis=0);base=np.repeat(base,46,axis=1)
                        noise=rng.normal(0,21,(180,322,1));frame=cv2.resize(np.clip(base+noise,0,255).astype('u1'),(1280,720),interpolation=cv2.INTER_NEAREST);frame[::4]//=2
                    else:frame=np.zeros((720,1280,3),np.uint8)
            elif sc=='order':
                frame=bgmap.copy();arrow(frame,min(1,max(0,(u-.36)/.40)));frame=grade(frame,'order')
            else:
                frame=graded(sc,mouth,state)
                if sc in ('cover','cute','meme','zero') and (sc!='cute' or state==2):
                    scale=1+(.023 if sc!='zero' else .045)*f/max(1,n-1)
                    frame=cv2.warpAffine(frame,cv2.getRotationMatrix2D((640,310),0,scale),(1280,720),borderMode=cv2.BORDER_REPLICATE)
                if sc=='sign' and u>.62:
                    # Cut to the allocation being corrected just after the general is excused.
                    im=backdrop('room').copy()
                    slide=papertex().copy();d=ImageDraw.Draw(slide)
                    txt(d,48,34,'生产调拨单',47,navy,'serif');d.line((48,121,851,121),fill='#9c906e',width=2)
                    txt(d,49,155,'新增生产线',28,'#746848');txt(d,55,218,'运输卡车',54,navy,'bold')
                    framed(slide,src(M/'motorized_equipment_1.png'),(481,136,823,326),True)
                    d.rounded_rectangle((579,328,823,422),8,outline=rust,width=4);txt(d,604,337,'批准排产',43,rust,'ink')
                    im.paste(slide.resize((907,475),R),(43,41));fox(im,1092,729,605,'neutral',mouth,'book_explain',1040);place(im,lectern(),977,444)
                    frame=grade(bgr(im),sc)
            proc.stdin.write(frame.tobytes())
        print(sc,round((shot['start']+n)/25,2),flush=True)
    proc.stdin.close()
    if proc.wait():raise RuntimeError('image encode failed')
    cap.release()
    chapters=[('封面与假宣传片','cover'),('大狐狸的自白','paper'),('指挥部的后勤课','lecture'),('创意工坊','more'),('一封入坑邀请','type')]
    bounds=[next(p['start'] for p in t['shots'] if p['shot']==sc) for _,sc in chapters]+[t['frames']]
    metadata=[';FFMETADATA1']
    for j,(name,_) in enumerate(chapters):metadata+=['[CHAPTER]','TIMEBASE=1/25',f'START={bounds[j]}',f'END={bounds[j+1]}','title='+name]
    (W/'chapters.txt').write_text('\n'.join(metadata)+'\n','utf8')
    finish()

def finish():
    stage=OUT/'opening.partial.mp4'
    sub=subprocess.Popen([os.environ.get('FFMPEG_ASS',FF),'-v','error','-nostdin','-i','picture.mp4',
                          '-an','-vf','ass=sub.ass','-pix_fmt','yuv420p','-f','rawvideo','pipe:1'],cwd=W,stdout=subprocess.PIPE)
    try:
        subprocess.run([FF,'-hide_banner','-v','error','-nostdin','-y','-f','rawvideo','-pixel_format','yuv420p','-video_size','1280x720',
                        '-framerate','25','-i','pipe:0','-i','mix.wav','-f','ffmetadata','-i','chapters.txt',
                        '-map','0:v:0','-map','1:a:0','-map_metadata','2','-map_chapters','2',
                        '-c:v','libx264','-threads','5','-preset','veryfast','-crf','18','-c:a','aac','-b:a','192k','-metadata:s:a:0','language=zho',
                        '-movflags','+faststart',str(stage)],cwd=W,stdin=sub.stdout,check=True)
    finally:
        sub.stdout.close()
    if sub.wait():raise RuntimeError('subtitle render failed')
    stage.replace(MOVIE)
    print(MOVIE)
if __name__=='__main__':
    import argparse
    argparse.ArgumentParser(description="使用已录配音和分镜渲染大狐狸开课片段。").parse_args()
    main()
