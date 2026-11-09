"""Locked editorial lines: all match speech is a contiguous excerpt of its recorded public turn."""
from pathlib import Path
import json
root=Path.cwd(); out=root/'exports/tyrol_two_flags_20261109'; run=root/'runs/campaigns/tyrol_two_flags_20261109'
work=root/'runs/tyrol_official_20261109/fullcut'
read=lambda p:json.loads(p.read_text('utf-8-sig'))
casts={'GLM':'glm','GER':'gpt','FRA':'claude','ITA':'gemini','AUS':'deepseek','SWI':'qwen'}
names={'GLM':'智谱娘','GER':'白龙','FRA':'克莱德','ITA':'基米','AUS':'大肥鱼','SWI':'千问'}
events={e['id']:e for i in range(1,8) for e in read(run/'episodes_postmatch'/f'turn_{i:03d}'/'episode.json')['events']}
narr=read(out/'智谱娘的七日解说.json')['content']['analysis']
chap=['智谱娘开场','听，还是调？','风景不写房产证','第五天到期，谁续？','作证还是作保','七天之后，翻开目标卡','两面旗最后在哪','公开总结大会']
rows=[]
def row(section,tag,text,src,kind,style,other=None,turn=None,refs=(),image_source=None):
 rows.append(dict(section=section,tag=tag,text=text,source=src,kind=kind,style=style,other=other,turn=turn,refs=list(refs),image_source=image_source))
def quote(section,eid,text,other,style='table'):
 e=events[eid];assert e['kind'] in {'host','public'} and text in e['text'],(eid,text)
 row(section,e['country'] or 'GLM',text,eid,e['kind'],style,other,int(eid.split('-')[0][1:]),[eid])
def voice(section,para,text,style,other=None,refs=None):
 assert text in narr[para]['text'],text
 row(section,'GLM',text,f'GLM-FINAL-P{para+1}','audience_narration',style,other,None,refs if refs is not None else narr[para]['evidence_ids'])
intro=read(work/'opening.json')['response']['lines'];assert len(intro)==5
for i,(line,style,other) in enumerate(zip(intro,['intro','pairmap','map','roster','intro'],[None,'AUS',None,None,None])):
 row(0,'GLM',line,f'片头补录-{i+1}','recorded_intro',style,other)
quote(1,'T1-E010','671我守着，6673的归属要谈——谁想替我划边界，先问过我的尾巴。','ITA')
quote(1,'T1-E009','本喵觉得6673的风景正正好，守住自家旗子就满足啦。','AUS')
quote(1,'T1-E012','猫猫，旗子插下去就成自家的了？','ITA')
quote(1,'T1-E007','我愿听取维也纳与罗马的想法，也欢迎各方探讨和平解决的可能。','AUS')
quote(1,'T1-E014','一个在等调停，一个只说听——听，还是调？','GER')
quote(1,'T1-E016','大狐狸，听是调停的第一步，不是全部。','GLM')
quote(1,'T1-E021','龙姐，听是耳朵，调是手，我等着看手。','GER')
quote(1,'T1-E021','基米说6673风景正好，可风景不写房产证；停火能谈，旗子先别急着认自家。','ITA')
quote(2,'T1-E017','法国的条件很简单:671与6673的归属,未经巴黎同意不改动。','ITA')
quote(2,'T2-E017','一个不让巴黎管，一个没巴黎不办——6673的当事方，到底算几位？','FRA')
quote(2,'T2-E025','两位：我和基米。巴黎那份“同意”不在名单上。','FRA')
quote(2,'T2-E018','收枪了没有，喵一声就行。','ITA')
quote(2,'T2-E021','喵一声就行，别让狐狸姐替你数。','ITA')
quote(2,'T2-E022','很抱歉克莱德大小姐，喵！','FRA')
quote(2,'T2-E022','那本喵先当租客住着，只要你不来掀房顶，咱们就地停火慢慢算租金喵！','AUS')
quote(2,'T3-E026','房本我揣着，租约能签，但租约上不写“现状即归属”。','ITA')
quote(3,'T4-E019','肥鱼催了，我也催：基米，敢不敢把后半句补上？','ITA')
quote(3,'T4-E025','本喵正式确认：停火不等于认定6673归属，房本以后慢慢扯！','AUS')
quote(3,'T5-E019','租约明明第五天到期，你倒先替房东续到了第七天。肥鱼，她替你续的这两天，你认不认？','AUS')
quote(3,'T5-E029','茶钱猫猫请，房本我揣着。基米，第五到第七天我不先拔腿——但这是续茶，不是续约；第六天谁先动，公开记录说话。','ITA')
quote(4,'T6-E009','大小姐和千千作证，安心等第七天开箱喵！','SWI')
quote(4,'T6-E017','两位都只数盏，没答应替你按门。作证和作保，猫猫选哪个？','ITA')
quote(4,'T6-E021','狐狸姐，作证不作保，这条本小姐认。','GLM')
quote(4,'T6-E024','大狐狸，别调侃本喵啦，作证是友谊，作保是契约，本喵分得清喵！','GLM')
quote(4,'T6-E030','狐姐问作证还是作保——门把在当事方手里，盏在我手里。','GLM')
quote(4,'T7-E017','干净战报和无进攻，是同一把锁吗？明早开箱见。','GER')
quote(4,'T7-E021','没战报不等于没进攻。基米的表态我记下了，执行仍待核实','GLM')
quote(4,'T7-E028','基米把“6673不进攻”补上了，口径齐，我认。','ITA')
quote(4,'T7-E025','那本喵带根香蕉去蹭饭总行吧？','AUS')
voice(5,1,'翻开各自的底牌：猫猫想保住蒂罗尔现有的意方控制区，肥鱼守着671还惦记收回对面那块；','goals','ITA/AUS',['T1-E056','T1-E057'])
voice(5,1,'大小姐要意大利开打前掂量巴黎的态度，龙姐盼着奥地利有独立周旋的余地；','goals','FRA/GER',['T1-E055','T1-E054'])
voice(5,1,'千千只求第七天瑞士依然和平。五个人围着两面旗子，各有各的算盘，谁也没把牌全亮出来。','goals','SWI',['T1-E058'])
voice(6,3,'开箱看结果：终局地图上671仍归奥地利、6673仍归意大利，两面旗没换主。','mapend',refs=['T7-E063','T1-E001'])
voice(6,3,'奥意开场就在交战，至今仍是战争状态。','mapend',refs=['T7-C-analysis-3','T1-E001'])
voice(6,3,'这七天没有下达新的地面命令，可部队并非原地钉死：逐日核对显示奥、意、瑞、法都有师换了省份，缘由按下不表。','movement',refs=['七日师位置核对.json'])
voice(6,3,'瑞士还在首回合启用了空军119联队的制空任务。旗子原样，账各归各。','movement',refs=['T1-E047','七日师位置核对.json'])
assert len(rows)>35 and set(r['turn'] for r in rows if r['turn'])==set(range(1,8))
