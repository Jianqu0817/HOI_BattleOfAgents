import os
import json,subprocess,re,sys
from pathlib import Path
root=Path(__file__).resolve().parents[3]
w=root/'runs/tyrol_class_photo_20261109'
out=root/'exports/蒂罗尔开场'
base=out/'合照局'
ff=os.environ.get('FFMPEG','ffmpeg');fp=os.environ.get('FFPROBE','ffprobe')
parts=[(out/'上半段样片修订版/蒂罗尔的两面旗·大狐狸开课·入坑邀请版.mp4',out/'上半段样片修订版/上半段字幕.srt','大狐狸开课'),(base/'合照是幌子·五人入场与选国.mp4',base/'合照是幌子·五人入场与选国.srt','合照与抽签')]

def ms(t):
 h,m,s,frac=map(int,re.split('[:,]',t));return ((h*60+m)*60+s)*1000+frac
def clock(t):
 s,ms=divmod(t,1000);m,s=divmod(s,60);h,m=divmod(m,60);return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'
def main(full=False):
    selected=list(parts)
    if full:
        selected.append((base/'合照局·七天桌边精选.mp4',base/'合照局·七天桌边精选.srt','七天桌边与总结大会'))
    t=0;subs=[];command=[ff,'-v','error','-y'];meta=';FFMETADATA1\n'
    for i,(p,sub,title) in enumerate(selected):
     data=json.loads(subprocess.check_output([fp,'-v','error','-show_format','-show_streams','-of','json',str(p)]));stream=next(s for s in data['streams'] if s['codec_type']=='video'); assert stream['r_frame_rate']=='25/1'
     duration=int(stream['nb_frames'])*40
     meta+=f'[CHAPTER]\nTIMEBASE=1/1000\nSTART={t}\nEND={t+duration}\ntitle={title}\n'
     for block in re.split(r'\r?\n\s*\r?\n',sub.read_text('utf-8-sig').strip()):
      q=block.splitlines();a,b=map(ms,q[1].split(' --> '));subs.append(f'{len(subs)+1}\n{clock(t+a)} --> {clock(t+b)}\n'+'\n'.join(q[2:]))
     command+=['-i',str(p)];t+=duration
    meta_path=w/'joined_chapters.txt';meta_path.write_text(meta,'utf8')
    command+=['-f','ffmetadata','-i',str(meta_path)]
    graph=';'.join(f'[{i}:0]setsar=1,setpts=PTS-STARTPTS[v{i}];[{i}:1]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]' for i in range(len(selected)))+';'+''.join(f'[v{i}][a{i}]' for i in range(len(selected)))+f'concat=n={len(selected)}:v=1:a=1[v][a]'
    name='蒂罗尔合照局·从开课到散场' if len(selected)==3 else '蒂罗尔合照局·完整开场'
    command+=['-filter_complex',graph,'-map','[v]','-map','[a]','-map_chapters',str(len(selected)),'-c:v','libx264','-preset','veryfast','-threads','6','-crf','19','-c:a','aac','-b:a','160k','-movflags','+faststart',str(w/'combined_ready.mp4')]
    subprocess.run(command,check=True);(w/'combined_ready.mp4').replace(base/(name+'.mp4'));(base/(name+'.srt')).write_text('\n\n'.join(subs)+'\n','utf-8-sig');print(name,t/1000,len(subs))


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser(description="合并讲课与入场段落，并按原帧率平移字幕。")
    parser.add_argument("--full",action="store_true",help="追加已剪好的七天精选与总结大会")
    main(parser.parse_args().full)
