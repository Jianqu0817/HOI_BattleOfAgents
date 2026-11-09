from pathlib import Path
import os,json,csv,shutil,zipfile,hashlib,subprocess,sys,collections,html
from urllib.parse import quote
root=Path.cwd(); w=root/'runs/tyrol_official_20261109/fullcut';old=root/'exports/tyrol_two_flags_20261109';dest=old/'完整静音粗剪';pack=dest/'服务器配音包';roles=pack/'roles';roles.mkdir(exist_ok=True,parents=True)
read=lambda p:json.loads(p.read_text('utf-8-sig'))
t=read(w/'timeline.json');lines=t['lines'];cues=t['subtitle_cues'];chapters=t['chapters']
def writejsonl(p,rows):p.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),'utf8')
def csvfile(p,rows):
 with p.open('w',encoding='utf-8-sig',newline='') as f:
  wr=csv.writer(f);wr.writerows(rows)
def mm(f):
 ms=f*40;s,ms=divmod(ms,1000);m,s=divmod(s,60);h,m=divmod(m,60);return f'{h:02}:{m:02}:{s:02},{ms:03}'
def tc(f):
 s,fr=divmod(f,25);m,s=divmod(s,60);h,m=divmod(m,60);return f'{h:02}:{m:02}:{s:02}:{fr:02}'
def sha(p):
 with p.open('rb') as s:return hashlib.file_digest(s,'sha256').hexdigest()
def summary_tc(f):return f'{(f//25)//60:02}:{(f//25)%60:02}'
title='蒂罗尔的两面旗';video=dest/(title+'·完整静音粗剪.mp4');probe=json.loads(subprocess.check_output([os.environ.get('FFPROBE','ffprobe'),'-v','error','-show_streams','-show_chapters','-of','json',str(video)]))
vs=next(s for s in probe['streams'] if s['codec_type']=='video')
assert int(vs['nb_frames'])==t['frames'] and vs['r_frame_rate']=='25/1' and not any(s['codec_type']=='audio' for s in probe['streams'])
assert len(lines)==len({x['line_id'] for x in lines}) and {r['character_id'] for r in lines}=={'glm','gpt','claude','qwen','deepseek','gemini'}
assert lines[0]['start_frame']==0 and lines[-1]['end_frame']==t['frames'] and all(a['end_frame']==b['start_frame'] for a,b in zip(lines,lines[1:]))
assert len(probe['chapters'])==len(chapters)
for pc,cc in zip(probe['chapters'],chapters):assert float(pc['start_time'])==round(cc['start_frame']/25,3) and pc['tags']['title']==cc['name']
assert lines[0]['character_id']=='glm' and lines[0]['source_kind']=='recorded_intro'
first_reveal=next(i for i,r in enumerate(lines) if r['section']=='七天之后，翻开目标卡')
assert all(r['visibility']=='public' for r in lines[:first_reveal])
assert len([r for r in lines if r['source_kind']=='public_meeting'])==13 and lines[-1]['text'].endswith('下期见。')
for i,r in enumerate(lines):assert ''.join(c['text'] for c in cues if c['line_index']==i)==r['text']
writejsonl(pack/'voice_lines.jsonl',lines)
head=['序号','角色','声线ID','段落','入点 TC','出点 TC（不含）','入帧','出帧（不含）','入点 ms','可用 ms','画面台词','建议送读文本','WAV 文件名','来源']
def csvrow(r):return [r[k] for k in ['sequence','speaker','character_id','section','start_timecode','end_timecode','start_frame','end_frame','start_ms','duration_ms','text','tts_text','wav_name','event_id']]
csvfile(pack/'整期台词_按时间.csv',[head]+[csvrow(r) for r in lines])
role_names={'glm':'智谱娘·大狐狸','gpt':'白龙','claude':'克莱德','gemini':'基米','deepseek':'大肥鱼','qwen':'千问'}
for role,name in role_names.items():
 rr=[r for r in lines if r['character_id']==role]
 writejsonl(roles/(role+'.jsonl'),rr)
 csvfile(roles/(role+'_'+name+'.csv'),[head]+[csvrow(r) for r in rr])
srt='\n'.join(f"{i}\n{mm(c['start_frame'])} --> {mm(c['end_frame'])}\n{c['speaker']}　{c['text']}\n" for i,c in enumerate(cues,1))
(pack/'整期字幕.srt').write_text(srt,encoding='utf-8-sig')
csvfile(pack/'章节.csv',[['章节','入点 TC','出点 TC（不含）']]+[[c['name'],tc(c['start_frame']),tc(c['end_frame'])] for c in chapters])
result={'title':title,'master':'reference.mp4','fps':25,'timecode':'HH:MM:SS:FF / 25 fps, non-drop, end exclusive','frames':t['frames'],'duration_ms':t['frames']*40,'video_sha256':sha(video),'line_count':len(lines),'role_counts':dict(collections.Counter(r['character_id'] for r in lines)),'chapters':chapters}
(pack/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
shutil.copyfile(root/'tools/gpt_sovits.py',pack/'gpt_sovits.py');shutil.copyfile(Path(__file__).resolve().parents[1]/'dub/batch_dub.py',pack/'batch_dub.py');shutil.copyfile(root/'configs/tts.cast.json',pack/'tts.cast.example.json')
instructions=f"""# 《{title}》服务器配音单

成片 1280 × 720，25 fps，起点 00:00:00:00，长度 {tc(t['frames'])}，共 {len(lines)} 条配音。参考片 reference.mp4 已压入 ZIP，带画面字幕、章节和完整总结大会，音轨留给服务器生成。

1. 整场批量读取 voice_lines.jsonl，按角色批量可读取 roles/ 中相应 JSONL。人工对词可打开同目录 CSV。配音对象以 character_id 为准，大狐狸、智谱娘和公开主持共用 glm；白龙及白色龙娘酱共用 gpt。
2. text 保存片中台词。tts_text 是送读文本，仅把省份代码 671、6673 及联队编号 119 改为逐位读。栏目名、人物名牌、证据编号和时间码属于画面与索引，均无需朗读。
3. 所有时间码都以整期参考片首帧为零。start_frame 和 start_ms 用于放置音频，end_frame 为下一段起始帧。duration_ms 是此版粗剪给该条预留的窗口。按 wav_name 导出，一个条目一个 WAV。整期字幕.srt 按画面换字幕，一条配音可对应多条字幕。
4. 若服务器使用 GPT-SoVITS API v2，在现有服务器声线配置中设置 base_url、voice 的模型权重和参考音频路径。可从 tts.cast.example.json 开始改，脚本将相对路径按配置文件所在目录解析。配套助手与本项目同用 Python 标准库。服务器音色资产和权重另行准备。

```sh
python batch_dub.py --check-only
python batch_dub.py --config /path/to/server-cast.json --out wav
python batch_dub.py --lines roles/glm.jsonl --config /path/to/server-cast.json --out wav
```

后两条分别用于整场和单角色，按需要选一条。WAV 和时长核对 CSV 在输出目录，over_by_ms 大于零表示该句超过粗剪窗口。音频保留完整，再决定调语速或延长镜头。所有 WAV 说话时长须以服务器实际生成结果为准。

素材说明：片头是智谱娘另为本片录的公开介绍。场内为实局公开原话节选，按问答剪接；地图画面使用终局补录的净地图，开局和终局控制色取各自的存档。五国目标与已核对的结局只出现在观众解说段；公开总结大会完整沿用六位角色的公开原话。
"""
(pack/'服务器使用说明.md').write_text(instructions,'utf8')
# The portable bundle uses only relative paths. No server credentials or model weights are packaged.
bundle=dest/'蒂罗尔的两面旗·服务器配音包.zip'
files=[p for p in sorted(pack.rglob('*')) if p.is_file() and '__pycache__' not in p.parts]
with zipfile.ZipFile(bundle,'w') as z:
 for p in files:z.write(p,p.relative_to(pack).as_posix(),compress_type=zipfile.ZIP_DEFLATED)
 z.write(video,'reference.mp4',compress_type=zipfile.ZIP_STORED)
with zipfile.ZipFile(bundle) as z:
 assert z.testzip() is None and json.loads(z.read('manifest.json'))==result
 assert hashlib.sha256(z.read('reference.mp4')).hexdigest()==result['video_sha256']
 assert sum(len(z.read('roles/'+name+'.jsonl').splitlines()) for name in role_names)==len(lines)
# Link the new full episode at the top of the existing viewing room.
index=old/'选段与解说.html';old_html=index.read_text('utf8')
block='<p class="links"><a href="'+quote(os.path.relpath(video,old).replace(chr(92),'/'))+'">播放整期静音粗剪 · '+summary_tc(t['frames'])+'</a><a href="'+quote(os.path.relpath(bundle,old).replace(chr(92),'/'))+'">服务器配音包</a></p>'
import re
old_html=re.sub(r'<p class="links"><a href="[^"]*">播放整期静音粗剪.*?</p>','',old_html,count=1)
assert old_html.count('<h2>剪辑选段</h2>')==1
index.write_text(old_html.replace('<h2>剪辑选段</h2>',block+'<h2>剪辑选段</h2>'),'utf8')
print(result['frames'],result['role_counts'],bundle.stat().st_size)
