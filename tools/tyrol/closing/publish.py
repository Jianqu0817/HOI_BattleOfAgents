from pathlib import Path
import json,html,shutil,hashlib
from html.parser import HTMLParser
from urllib.parse import quote,unquote
from collections import Counter
root=Path.cwd();work=root/'runs/tyrol_official_20261109/closing';lib=root/'assets/corpus/tyrol_two_flags_20261109/公开总结大会';out=root/'exports/tyrol_two_flags_20261109/公开总结大会'
rows=[json.loads(l) for l in (lib/'voice_lines.jsonl').read_text('utf8').splitlines()];ref=json.loads((lib/'public_sources.json').read_text('utf8'));timeline=json.loads((out/'剪辑时间轴.json').read_text('utf8'))
for fn in ['voice_lines.jsonl','public_sources.json']:shutil.copy2(lib/fn,out/fn)
def esc(s):return html.escape(s,quote=True)
url=quote
video=next(out.glob('*.mp4'))
parts=['''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>蒂罗尔的两面旗 · 公开总结大会</title><style>
body{margin:0;background:#141e27;color:#f5f4ed;font:17px/1.7 "Microsoft YaHei",sans-serif}main{max-width:920px;margin:auto;padding:30px 22px 65px}h1{font-size:clamp(29px,5vw,44px);line-height:1.25;margin:8px 0 10px}h2{margin-top:32px;color:#f2dba7;font-size:22px}a{color:#a8dfd2;margin-right:14px}video{display:block;width:100%;border-radius:9px;margin-top:18px}small,summary{color:#b3c5c7}summary{cursor:pointer}article{padding:10px 0;border-bottom:1px solid #324650}article p{margin:0}details{margin-top:12px}nav{margin:13px 0}button{background:transparent;color:#a7cfc7;border:0;padding:0;font:inherit;cursor:pointer}b{color:#f2debb}</style>
<main><small>IRON · 赛后公开录制</small><h1>蒂罗尔的两面旗 · 总结大会</h1><p>六位角色回看七天里的公开交锋。视频为静音配音底片，台词按出场顺序收录。</p>''',f'<video id="film" src="{url(video.name)}" controls preload="metadata"></video>','<nav>']
for fn,name in [('大会逐字稿.md','逐字稿'),('voice_lines.jsonl','配音语料'),('公开总结大会.srt','字幕'),('剪辑时间轴.json','时间轴'),('public_sources.json','公开引用')]:parts.append(f'<a href="{url(fn)}">{name}</a>')
parts+=['</nav>'];stage=''
for row,chapter in zip(rows,timeline['chapters']):
 assert row['meeting_id']==chapter['id']
 if stage!=row['stage']:stage=row['stage'];parts.append('<h2>'+esc(stage)+'</h2>')
 sec=chapter['start_seconds'];parts.append(f'<article id="{row["meeting_id"]}"><button onclick="let v=document.getElementById(\'film\');v.currentTime={sec:.2f};v.play()">{int(sec)//60:02}:{int(sec)%60:02}</button>　<b>{esc(row["speaker"])}</b><p>{esc(row["text"])}</p></article>')
parts+=['</main></html>'];(out/'放映与台词.html').write_text(''.join(parts),'utf8')
# Privacy QA: compare publication and actual model inputs to the public bibliography and the earlier meeting turns.
public_all=json.loads((work/'public_sources.json').read_text('utf8')); originals={}; no_secrets=['scenario_goal','private_decisions','private_intents','private_diplomacy','locked_decision','audience_commentary']
for row in rows:
 mid=row['meeting_id']; n=int(mid[1:]); p=next(work.glob(f'{n:02d}_*.json'));rec=json.loads(p.read_text('utf8'))
 data=json.loads(rec['messages'][1]['content'])
 assert row['text']==rec['raw_response']['text'].strip()
 assert set(data)=={'公开演员','本人公开简介','公开记录','刚才实际公开说的话','本次发言','格式'}
 for s in data['公开记录']:assert s['text']==public_all[s['id']]['text']
 for s in data['刚才实际公开说的话']:assert originals[s['id']]==s['text']
 assert not (set(row['refs']) - (set(ref)|set(originals)));originals[mid]=row['text']
 assert all(word not in json.dumps(data,ensure_ascii=False) for word in no_secrets)
assert len(set(r['line_id'] for r in rows))==len(rows)
class Links(HTMLParser):
 def handle_starttag(self,tag,attrs):
  for k,v in attrs:
   if k in ('src','href'):
    target=(out/unquote(v)).resolve();assert target.parent==out and target.is_file(),target
Links().feed(''.join(parts))
print(len(rows),dict(Counter(r['speaker'] for r in rows)),len(ref),'public refs')
