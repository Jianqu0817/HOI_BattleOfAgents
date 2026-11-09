import sys,json,html,os,subprocess
from pathlib import Path
from urllib.parse import quote
root=Path.cwd();out=root/'exports/tyrol_two_flags_20261109'; work=root/'runs/tyrol_official_20261109'; run=root/'runs/campaigns/tyrol_two_flags_20261109';rd=lambda p:json.loads(p.read_text('utf-8-sig')); esc=html.escape
def url(p):return quote(os.path.relpath(p,out).replace('\\','/'),safe='/._-')
def tc(seconds):
 t=round(seconds,1);return f'{int(t//60):02}:{t%60:04.1f}'
clips=rd(out/'选段来源与时间码.json');glm=rd(out/'智谱娘的七日解说.json')['content']
segments=sorted((root/'recordings/tyrol_two_flags_20261109').glob('*.mp4')); source={str(p):i+1 for i,p in enumerate(segments)}
head='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>蒂罗尔的两面旗 · 七日放映室</title><style>
*{box-sizing:border-box}body{margin:0;background:#101b23;color:#eef3ef;font:16px/1.7 "Microsoft YaHei",sans-serif}main{margin:auto;max-width:1050px;padding:32px 22px 70px}h1{font-size:clamp(30px,5vw,54px);letter-spacing:.03em;margin:4px 0 8px}h2{font-size:26px;margin:30px 0 10px;color:#ecd9af}h3{font-size:21px;margin:0 0 8px}small,.meta{color:#a3bbb9}p{margin:7px 0 13px}a{color:#a4e0d2}video{width:100%;display:block;border-radius:8px;background:#0a1218}article{border-top:1px solid #37505b;padding:22px 0;display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:20px}summary{cursor:pointer;color:#acdbce}ol{margin:8px 0;padding-left:20px}details{margin:8px 0}blockquote{border-left:3px solid #789989;margin:12px 0;padding-left:17px}.links a{display:inline-block;margin:3px 14px 3px 0}@media(max-width:680px){article{grid-template-columns:1fr}main{padding:18px 14px 38px}}
</style><main><small>IRON · 五国模型实局</small><h1>蒂罗尔的两面旗</h1><p class="meta">1936.9.4 22时 — 9.11 22时　·　168 游戏小时　·　七个回合</p><h2>剪辑选段</h2><p>先看片中交锋，再揭角色目标或查看终局。字幕取自本局记录，地图背景为终局暂停后补录。四段均为静音底片。</p>'''
parts=[head];md=['# 《蒂罗尔的两面旗》选段与原片时间码','','七回合、168 游戏小时。所有选段均静音，以终局净地图作背景。原片时间码根据模型完成记录与 OBS 分段起点换算，指向台词落盘时的游戏画面。同期公开发言并行，剪辑排列是观看顺序。','']
for i,c in enumerate(clips,1):
 p=out/c['video'];marks=[];md+=['## '+c['title'],'',c['why'],'',f'静音预剪时长 {c["duration"]:.1f} 秒。','']
 for shot in c['shots']:
  code=shot['call_timecode'];seg=source[shot['raw']]
  t=f'原片 {seg} · {tc(code)}' if code is not None else '赛后揭示'
  marks.append(f'<li>{esc(t)} · {esc(shot["quote"])}</li>')
  md.append(f'- {t}　{shot["event"]}　{shot["quote"]}')
 md+=['']
 parts.append(f'<article><video controls preload="metadata" src="{url(p)}"></video><div><h3>{esc(c["title"])}</h3><small>{c["duration"]:.1f} 秒</small><p>{esc(c["why"])}</p><a href="{url(p.with_suffix(".srt"))}">配套字幕</a><details><summary>台词与原片时间码</summary><ol>{"".join(marks)}</ol></details></div></article>')
parts+=['<h2>智谱娘的七日解说</h2>','<p class="meta">'+esc(glm['title'])+'</p>']
for r in glm['analysis']:parts.append('<blockquote>'+esc(r['text'])+'</blockquote>')
parts+=['<p class="links"><a href="'+url(out/'智谱娘的七日解说.md')+'">全场与逐回合解说稿</a><a href="'+url(out/'七日师位置核对.json')+'">师位置事实核对</a></p>','<h2>逐日回看</h2><p class="links">']
for i in range(1,8):
 parts.append(f'<a href="{url(run/"episodes_postmatch"/f"turn_{i:03d}"/"index.html")}">第 {i} 天</a>')
parts+=['</p>']
closing=out/'公开总结大会/放映与台词.html'
if closing.is_file():
 parts.append('<h2>公开总结大会</h2><p><a href="'+url(closing)+'">六人互评、静音片尾与配音语料</a></p>')
parts+=['<h2>原始素材</h2><p class="links">']
for i,p in enumerate(segments,1):parts.append(f'<a href="{url(p)}">原片 {i}</a>')
parts+=['<a href="'+url(out/'终局净地图.mp4')+'">终局净地图视频</a><a href="'+url(out/'终局净地图.png')+'">净地图静帧</a><a href="'+url(out/'选段来源与时间码.json')+'">逐镜来源</a><a href="'+url(run/'verification.json')+'">最终审计</a></p><p class="meta">原片共 20 分 54 秒。第 4 回合意图阶段曾中断，第二段从同一回合接续。七日终点及存档核验通过，23 项供应商身份元数据未能独立确认。</p></main></html>']
(out/'选段与解说.html').write_text(''.join(parts),'utf8');(out/'剪辑时间码.md').write_text('\n'.join(md),'utf8')
from html.parser import HTMLParser
from urllib.parse import unquote
class Verify(HTMLParser):
 def handle_starttag(self,tag,attrs):
  attrs=dict(attrs)
  for key in ['href','src']:
   if key in attrs:assert (out/unquote(attrs[key])).is_file(),attrs[key]
Verify().feed(''.join(parts))
