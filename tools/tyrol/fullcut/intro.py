from pathlib import Path
import json,sys
sys.path.insert(0,'tools')
from campaign import effective_resumed_config
from model_client import complete
from m0_probe import write_json
d=Path('runs/campaigns/tyrol_two_flags_20261109');read=lambda p:json.loads(p.read_text('utf-8-sig'))
manifest=read(d/'manifest.json');config=effective_resumed_config(manifest)
src=config['scenario'];cast=config['show']['public_cast']
prompt={'节目':'《蒂罗尔的两面旗》','开局公开播报':src['opening'],'公共演员表':[{k:i[k] for k in ('name','country_name','character_id')} for i in cast],'任务':'你是主持人智谱娘大狐狸，给第一次看的观众做片头介绍。用四到五口短句，总计95到150个汉字；第一口报上自己的名号和节目名；给地图新手介绍意大利的基米、奥地利的大肥鱼正在交战，671和6673两块相邻省份各是谁控制；带到德国白龙、法国克莱德、瑞士千问也坐在桌边；最后留下七个游戏日后回来对照旗子或承诺的悬念。不要预告实际结局、秘密目标、玩家回答或虚构采访。自称我，口语有性格，能一口念完。返回 JSON {"lines":["台词", "..."]}。'}
messages=[{'role':'system','content':'你是只用开场公开材料给观众带场的智谱娘大狐狸。输出 JSON。'},{'role':'user','content':json.dumps(prompt,ensure_ascii=False)}]
out=Path('runs/tyrol_official_20261109/fullcut/opening.json')
out.parent.mkdir(parents=True,exist_ok=True)
if not out.exists():
 value,meta=complete(config['commentary_model'],messages,attempts=2)
 write_json(out,{'purpose':'公开资料片头补录','messages':messages,'response':value,'metadata':meta})
print(read(out)['response'])
