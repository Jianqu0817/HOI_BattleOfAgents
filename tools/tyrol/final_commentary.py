from pathlib import Path
from datetime import datetime,timezone
import sys,json
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'tools'))
from model_client import complete
from episode import validate_commentary,normalize_commentary_quotes,digest
from campaign import effective_resumed_config
from m0_probe import write_json
d=ROOT/'runs/campaigns/tyrol_two_flags_20261109';work=ROOT/'runs/tyrol_official_20261109';out=ROOT/'exports/tyrol_two_flags_20261109'
work.mkdir(parents=True,exist_ok=True)
out.mkdir(parents=True,exist_ok=True)
def rd(p):return json.loads(p.read_text('utf-8-sig'))
m=rd(d/'manifest.json');assert m['status']=='completed' and all(t.get('audience_completed_at') for t in m['turns'])
cfg=effective_resumed_config(m)
evs=[]; days=[]
for entry in m['turns']:
 i=entry['index'];ep=rd(d/'episodes_postmatch'/f'turn_{i:03d}'/'episode.json');days.append((i,entry['final_date'],ep))
 for e in ep['events']:
  k=e['kind']; speaker=e.get('country')
  if (k in {'host','scenario_map','opening','execution','clock','interview'}
      or (k=='scenario_goal' and i==1)
      or (k=='public' and e['data'].get('round')==2)
      or (k=='decision' and (speaker in ('ITA','AUS') or 'actions' in e['data'] and e['data']['actions']))):
   evs.append({key:e.get(key) for key in ('id','kind','country','text','title','game_date')})
pkt={'role':'智谱娘大狐狸，面向观众做七日赛后解说','任务':'写四小段可配音的中文解说，先还原一组桌上激将与回应，再揭角色目标，最后用地图和动作读回说清结果。每段analysis约65至120字。避免编造因果与实际部队行动。区分原有战争、玩家口头停火与游戏条约；没有控制权变化不等于没有战斗。due_turn是回合号。没有公开逐字记录的内容别写成引语。每段用evidence_ids列真实id。返回{title,facts:[],analysis:[{text,evidence_ids}],jokes:[],highlights:[]}。','evidence':evs}
msgs=[{'role':'system','content':'你是IRON只面向观众的智谱娘大狐狸。用自然的口语讲得清楚，引用由游戏记录核实。输出JSON。'},{'role':'user','content':json.dumps(pkt,ensure_ascii=False)}]
path=work/'glm_final_call.json'
if path.is_file():
 rec=rd(path);assert rec['evidence_digest']==digest(evs)
else:
 val,meta=complete(cfg['commentary_model'],msgs,attempts=2)
 rec={'audience':'audience_full','evidence_digest':digest(evs),'messages':msgs,'response':val,'metadata':meta}
 write_json(path,rec)
raw=rec['response']; requested={**raw,'facts':[],'jokes':[],'highlights':[]}
comment,changes=normalize_commentary_quotes(requested,{'events':evs},explain_only=True)
changes.extend({'kind':'unrequested_section_omitted','section':k,'count':len(raw[k])} for k in ('facts','jokes','highlights') if raw.get(k))
assert comment['analysis']
# Read every saved daily snapshot. The absence of newly issued commands does not establish stationary units.
from collections import defaultdict
history = defaultdict(list)
previous = None
for number, directory in enumerate([d/'initial_capture']+sorted((d/'captures').glob('after_*'))):
    snapshots = rd(directory/'snapshots.json')
    current = {tag: {u['unit_id']:u['province'] for u in snapshots[tag]['divisions']['units']} for tag in cfg['countries']}
    if previous is not None:
        for tag, units in current.items():
            for uid, province in units.items():
                if uid in previous[tag] and previous[tag][uid]!=province:
                    history[tag].append({'day':number,'division':uid,'from':previous[tag][uid],'to':province})
    previous=current
counts={tag:len({u['division'] for u in history[tag]}) for tag in cfg['countries']}
movement_id='七日逐日师位置核对'
write_json(out/'七日师位置核对.json',{'id':movement_id,'distinct_divisions_with_observed_province_change':counts,'changes':dict(history),'source':'initial_capture/snapshots.json and captures/after_001 through after_007/snapshots.json','note':'同一师在相邻两个暂停点的省份不同；仅据此不能判断原因或战果。'})
fix_path=work/'glm_final_map_correction.json'
fix_input={'task':'你是智谱娘大狐狸。重写这一段七日结语（100至150字），保留两面旗开箱的口吻，使用下面的检查结果纠正关于部队位置的句子。七天没有新下地面命令，但不能说没有行军或没有战斗。不要猜移动原因。返回JSON {text:段落,evidence_ids:下列真实引用ID列表}。','original':comment['analysis'][-1],
           'observations':{'distinct_divisions_that_changed_province':counts,'new_ground_orders':0,'new_action':'第一回合瑞士启用空军119联队制空任务','final_controllers':{'671':'AUS','6673':'ITA'},'final_war_relation':'AUS与ITA仍处战争状态'},
           'sources':[movement_id,'T7-E063','T1-E047','T1-E001']}
if fix_path.exists():
    corrected=rd(fix_path)
    assert corrected['input']==fix_input
else:
    val,meta=complete(cfg['commentary_model'],[{'role':'system','content':'只为观众写赛后解说，返回 JSON。'},{'role':'user','content':json.dumps(fix_input,ensure_ascii=False)}],attempts=2)
    corrected={'input':fix_input,'response':val,'metadata':meta};write_json(fix_path,corrected)
fix=corrected['response']
assert set(fix)=={'text','evidence_ids'} and set(fix['evidence_ids'])<=set(fix_input['sources']+fix_input['original']['evidence_ids'])
assert '没有下达新的地面命令' in fix['text'] and '都有师换了省份' in fix['text']
comment['analysis'][-1]=fix
validate_commentary(comment,{'events':evs+[{'id':movement_id,'text':json.dumps(counts)}]})
lines=['# '+comment['title'],'','智谱娘 · 七日赛后解说','']
for row in comment['analysis']:
 lines.extend([row['text'],'','依据：'+ '、'.join(row['evidence_ids']),''])
lines+=['[七日师位置逐日核对](七日师位置核对.json)','','## 逐回合解说','','以下保留每回合模型原文。凡提到“一个兵都没动”，执行记录仅能证实当轮有没有新下调兵命令，位置变化以上方逐日核对为准。台词中“第五天”的约期台账是第 5 回合，本场每回合推进 24 小时。','']
for i,date,ep in days:
 data=rd(d/'trials'/f'turn_{i:03d}'/'audience_commentary/commentary.json')['content']
 lines+=['### 第'+str(i)+'回合 · '+data['title'], '', '回合终点 '+date,'']
 for row in data['analysis']:lines.extend([row['text'],'','依据：'+'、'.join(row['evidence_ids']),''])
 lines.extend(['[查看这一回合的完整回看](../../runs/campaigns/tyrol_two_flags_20261109/episodes_postmatch/turn_'+f'{i:03d}'+'/index.html)',''])
(out/'智谱娘的七日解说.md').write_text('\n'.join(lines),'utf8')
write_json(out/'智谱娘的七日解说.json',{'audience':'audience_full','content':comment,'normalizations':changes,'evidence_digest':digest(evs),'provider_metadata':rec['metadata'],'map_correction_metadata':corrected['metadata'],'editor_verified_source':movement_id})
print(comment['title'])
for r in comment['analysis']: print(r['text'],r['evidence_ids'])
