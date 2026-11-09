from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT/'runs/tyrol_official_20261109/qwen_day12'
OUT = ROOT/'exports/tyrol_two_flags_20261109/前两日节奏试片'
sources = Path(__file__).with_name('public_sources.json')
events = json.loads(sources.read_text('utf-8'))
groups = [
    (1, '开局', '蒂罗尔的两面旗', '1936.09.04  晚'),
    (1, '第一天', '谁的旗，谁的地？', '1936.09.04 — 09.05'),
    (1, '第一天', '德国肯不肯出面？', '1936.09.04 — 09.05'),
    (1, '第一天收盘', '两面旗还在原位', '1936.09.05  晚'),
    (2, '第二天', '巴黎有没有一票？', '1936.09.05 — 09.06'),
    (2, '第二天', '从房东到租客', '1936.09.05 — 09.06'),
    (2, '第二天收盘', '停火谈到了哪一步？', '1936.09.06  晚'),
]
# Room after a line belongs to the outgoing speaker's reaction; chapter cards get their own silent shot.
voices = {
    'GLM':('智谱娘','主持','longanhuan_v3.1','自然的普通话女声，像和桌边朋友一起看地图的节目主持人。从容，句意落稳再接下一句。'),
    'GER':('白龙','德国','xieshurou_v3.1','成熟冷静，字音清楚，说话有分寸。和同桌的人商量，语速舒展。'),
    'AUS':('大肥鱼','奥地利','longanfengyue_v3.1','自然的普通话，声音偏沉，随口抛出一句干幽默。重点词稍微停一下。'),
    'ITA':('基米','意大利','qiaoxiaojiao_v3.1','轻盈有笑意的猫系少女，像当面回嘴。语速悠闲，把喵字自然读出来。'),
    'FRA':('克莱德','法国','xiaxiaochen_v3.1','讲话利落的年轻女性，底气足，像大小姐当面谈条件。措辞清晰，句间换气。'),
    'SWI':('千问','瑞士','wenhuaiqing_v3.1','轻柔沉着，有一点看热闹的笑意，像端着茶说话，节奏从容。'),
}
rows=[]

def narr(scene, text, refs, gap, style='map'):
    assert all(r in events for r in refs)
    rows.append(dict(scene=scene,tag='GLM',text=text,refs=refs,kind='观众解说',gap=gap,style=style))

def quote(scene, eid, text, gap):
    e=events[eid]
    assert e['kind'] in ('public','host') and text in e['text'],(eid,text)
    rows.append(dict(scene=scene,tag=e['country'] or 'GLM',text=text,refs=[eid],kind='桌边发言',gap=gap,style='talk'))

narr(0,'我是主持智谱娘。欢迎来到《蒂罗尔的两面旗》。',['T1-E001'],0.95,'intro')
narr(0,'先看地图。蒂罗尔这两块地挨着，奥地利和意大利已经开战了。',['T1-E001'],0.85)
narr(0,'金色这块，奥地利控制。绿色这块，意大利控制。咱们从今晚开始，看七天。',['T1-E001'],1.0)
quote(1,'T1-E009','本喵觉得6673的风景正正好，守住自家旗子就满足啦。',0.8)
quote(1,'T1-E010','671我守着，6673的归属要谈——谁想替我划边界，先问过我的尾巴。',0.85)
quote(1,'T1-E012','猫猫，旗子插下去就成自家的了？',1.05)
quote(1,'T1-E018','肥鱼小姐，6673是本喵的地盘，671归你，各守各旗、就地停火如何喵？',0.75)
quote(1,'T1-E021','基米说6673风景正好，可风景不写房产证；停火能谈，旗子先别急着认自家。',1.1)
quote(2,'T1-E007','我愿听取维也纳与罗马的想法，也欢迎各方探讨和平解决的可能。',0.65)
quote(2,'T1-E014','一个在等调停，一个只说听——听，还是调？',0.92)
quote(2,'T1-E016','大狐狸，听是调停的第一步，不是全部。',0.8)
quote(2,'T1-E021','龙姐，听是耳朵，调是手，我等着看手。',1.08)
narr(3,'第一天结束。两面旗都还在原位。基米提议各守各的，肥鱼肯谈停火，可没认下那张地契。',['T1-E059','T1-E018','T1-E021'],1.1)
narr(4,'第二天，白龙把昨天说的“听一听”，写成了一个提案。咱们听她把哪件事放在前头。',['T1-E007','T2-E006'],0.85,'host')
quote(4,'T2-E006','双方先就地停止进攻，暂不认定6673归属，再谈后续安排。',0.85)
quote(4,'T2-E007','不过有两点要说清：671与6673的归属，未经巴黎同意不改动；另请基米说明，6673内的行动是否已经停下？',0.85)
quote(4,'T2-E017','一个不让巴黎管，一个没巴黎不办——6673的当事方，到底算几位？',0.95)
quote(4,'T2-E025','两位：我和基米。巴黎那份“同意”不在名单上。',1.0)
quote(5,'T2-E018','收枪了没有，喵一声就行。',0.9)
quote(5,'T2-E021','喵一声就行，别让狐狸姐替你数。',0.95)
quote(5,'T2-E022','很抱歉克莱德大小姐，喵！',0.88)
quote(5,'T2-E022','那本喵先当租客住着，只要你不来掀房顶，咱们就地停火慢慢算租金喵！',1.05)
quote(5,'T2-E028','当事方两位我认，调停方一位我记，看戏方一位——就是我。',1.1)
narr(6,'到第二天晚上，地图上还是奥地利守着六七一，意大利守着六六七三。两国仍在交战。',['T2-E051','T2-E052']+[e for e in events if e.startswith('T2-') and events[e]['kind']=='scenario_map'],0.9)
narr(6,'桌上倒是有件事变了。昨天基米还管这块地叫自家，今天已经肯当租客了。至于这房子到底归谁，两边都没签字。',['T1-E009','T2-E022','T2-E025'],1.6,'host')

def spoken(text):
    return text.replace('6673','六六七三').replace('671','六七一').replace('——','，').replace('；','。')
