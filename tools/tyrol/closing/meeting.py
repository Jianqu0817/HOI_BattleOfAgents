from pathlib import Path
import sys,json,re,hashlib,argparse
from concurrent.futures import ThreadPoolExecutor
ROOT=Path.cwd(); sys.path.insert(0,str(ROOT/'tools'))
from campaign import effective_resumed_config,load_model_environment,name_ok
from model_client import complete,ModelJSONError
from m0_probe import write_json
from showrunner import country_model
from host_skills import host_guidance
parser=argparse.ArgumentParser(description='从本桌公开原话和终局地图生成六人总结大会。默认保留旧蒂罗尔场。')
parser.add_argument('--name',default='tyrol_two_flags_20261109')
parser.add_argument('--brief',type=Path,help='新桌的公开选段与主持提问 JSON；不包含私信或密封决策')
args=parser.parse_args();name=name_ok(args.name);legacy=name=='tyrol_two_flags_20261109'
if not legacy and args.brief is None:parser.error('新桌须提供本桌的 --brief，不能沿用旧桌话题和人设分配')
work=Path('runs/tyrol_official_20261109/closing') if legacy else ROOT/'runs'/name/'closing'
work.mkdir(parents=True,exist_ok=True);run=ROOT/'runs/campaigns'/name
read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
m=read(run/'manifest.json');cfg=effective_resumed_config(m);show=cfg['show'];tags=cfg['countries']
assert m['status']=='completed' and m['turns'][-1]['final_date_raw']-m['initial_date_raw']==cfg['scenario']['hours']==168
load_model_environment(cfg)
cast={r['country'] or 'GLM':{k:r[k] for k in ('character_id','name','self_intro')} for r in show['public_cast']}
names={k:v['name'] for k,v in cast.items()}
sources={}
def add(key,speaker,text,source,original=None):
    sources[key]={'id':key,'speaker':speaker,'text':text,'source':source}
    if original:sources[key]['original_id']=original
for turn in (row['index'] for row in m['turns'] if row['status']=='completed'):
    tr=run/'trials'/f'turn_{turn:03d}';base=f'trials/turn_{turn:03d}'
    for idx,row in enumerate(read(tr/'public_diplomacy.json')):
        assert row['visibility']=='public' and row['source_kind']=='player_claim'
        add(f'{turn}-{row["country"]}-{row["round"]}', row['country'],row['message'],f'{base}/public_diplomacy.json#/{idx}/message',row['id'])
    for idx,row in enumerate(read(tr/'table_host.json')):
        assert row['visibility']=='public' and row['source_kind']=='host_commentary'
        add(f'{turn}-H{idx+1}','GLM',row['message'],f'{base}/table_host.json#/{idx}/message',row['id'])
last=m['turns'][-1]['index'];final=read(run/f'turns/turn_{last:03d}/result.json')['scenario_control']
if legacy:
    assert final=={'671':'AUS','6673':'ITA'}
    map_text='开场奥意已交战。七个游戏日结束，671号省仍由奥地利控制，6673号省仍由意大利控制。'
else:
    countries={'AUS':'奥地利','ITA':'意大利','GER':'德国','FRA':'法国','SWI':'瑞士'}
    map_text='开场奥意已交战。七个游戏日结束，'+ '，'.join(f'{p}号省由{countries.get(t,t)}控制' for p,t in final.items())+'。'
add('MAP','地图',map_text,f'turns/turn_{last:03d}/result.json#/scenario_control')
# Each excerpt is copied only from an explicitly public file above. No player profile, interview, order or mailbox enters the packet.
common=['MAP','1-H3','1-GER-2','1-AUS-2','2-H2','2-ITA-2','3-AUS-2','4-ITA-2','5-AUS-2','6-H1','6-FRA-2','6-SWI-2','7-H1','7-GER-2','7-ITA-2','7-AUS-2']
focus={'GER':['1-H3','1-GER-2','7-GER-2'],'ITA':['2-H2','2-ITA-2','4-ITA-2'],'AUS':['1-AUS-2','3-AUS-2','5-AUS-2'],'FRA':['1-FRA-2','4-FRA-2','6-FRA-2'],'SWI':['1-SWI-2','6-H1','6-SWI-2'],'GLM':['1-H3','2-H2','6-H1','7-H1']}
topics={'GER':'回想那天被追问听还是调，你公开讲过哪一句最容易让人误会，下回愿意怎么说？',
'ITA':'从自家旗子到租客，挑一句你现在听了都想笑的公开用词，说说它给别人什么印象。',
'AUS':'你把风景、房本、租约都搬上了桌。哪次比喻帮你把意思说清，或者反而给别人递了话柄？',
'FRA':'别人听你要巴黎同意，到底像证人还是房东？只复盘你在台面上的措辞，下回你会怎样把话说直？',
'SWI':'大家都来问你要签字，你只数茶盏。挑一次公开接话，说说你觉得自己哪句话把玩笑接住了。'}
voices={'GLM':'自称我。少说，具体，困归困，轮到自己挨笑也接得住。','GER':'白龙讲理简短，偶尔冷幽默。','FRA':'克莱德用本小姐，敢承认自己也有说过头的时候。','ITA':'基米自称本喵，开朗调皮，让喵落在有用的地方。','AUS':'大肥鱼会拿手边小事打比方，直爽。','SWI':'千问用我，拿得稳，说得轻，熟人间可以回敬。'}
if not legacy:
    brief=read(args.brief)
    assert brief['campaign']==name
    common,focus,topics,voices=(brief[k] for k in ('common','focus','topics','voices'))
    assert set(focus)==set(tags)|{'GLM'} and set(topics)==set(tags) and set(voices)==set(tags)|{'GLM'}
    assert set(common)|{i for ids in focus.values() for i in ids}<=set(sources)
    write_json(work/'public_brief.json',brief)
def speak(no,tag,phase,question,earlier,reply_to=None,target=None):
    ids=list(dict.fromkeys(common+focus[tag])); history=[{k:r[k] for k in ('id','speaker','text')} for r in earlier]
    pool=set(ids)|{r['id'] for r in history}; lines=[{k:sources[i][k] for k in ('id','speaker','text')} for i in ids]
    payload={'公开演员':names,'本人公开简介':cast[tag]['self_intro'],'公开记录':lines,'刚才实际公开说的话':history,'本次发言':question,
      '格式':'返回 JSON {"text":"一段可直接朗读的简体中文台词","refs":["公开材料或刚才发言的真实id"]}。refs一至四项。正文不要读编号；直接开口，不写舞台指令、角色标签。'}
    sysmsg=f'你是{names[tag]}。{voices[tag]} 现在是本期公开收尾大会，大家和观众都听得见。全场只谈公开地图和台词。不得透露、猜测或暗示未公开的决策、任务、私信、执行记录或他人动机；只说对公开言语的理解。说自己今天这句如何改、接眼前的梗都可以。不要虚构别人说过的话。只给JSON。'
    if tag=='GLM':
        sysmsg+=host_guidance(show)
        if show.get('host_table_brief') and show.get('host_profile'):
            payload['主持人设']=show['host_profile']['persona']
    messages=[{'role':'system','content':sysmsg},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
    config=cfg['model'] if tag=='GLM' else country_model(cfg['model'],show,tag)
    path=work/f'{no:02d}_{tag}.json'
    if path.is_file():
        rec=read(path);assert rec['messages']==messages
    else:
        try:
            value,meta=complete(config,messages,attempts=1)
        except ModelJSONError as error:
            write_json(work/f'{no:02d}_{tag}.failed.json',{'messages':messages,'raw_content':error.raw_content,'metadata':error.metadata,'error':str(error)})
            raise
        rec={'messages':messages,'raw_response':value,'metadata':meta};write_json(path,rec)
    v=rec['raw_response']; refs=v['refs']; t=v['text'].strip()
    assert isinstance(t,str) and 1<=len(t)<=360 and isinstance(refs,list) and 1<=len(refs)<=4 and all(i in pool for i in refs),(no,v)
    row={'id':f'C{no:02d}','speaker':tag,'character_id':cast[tag]['character_id'],'name':names[tag],'stage':phase,'text':t,'refs':refs,'visibility':'public'}
    if reply_to:row['reply_to']=reply_to
    if target:row['addressed_to']=target
    print(row['id'],tag,t,flush=True);return row
opening='用两三句话开场：把两面旗最终位置的事实讲得听得懂，邀请大家挑一句当时公开说过、现在愿意重说的话。最后还有个你也躲不过的环节，暂时卖关子。约55字。' if legacy else brief['opening_question']
rows=[speak(1,'GLM','开场',opening,[])]
order=['AUS','GER','ITA','FRA','SWI']
with ThreadPoolExecutor(max_workers=5) as pool:
    pending=[pool.submit(speak,n,tag,'桌面复盘',topics[tag]+' 40至85字。',rows[:],reply_to='C01') for n,tag in enumerate(order,2)]
    rows.extend([p.result() for p in pending])
target='ITA' if legacy else brief['host_roast_target'];assert target in tags
host_question='现在轮到互损。可以直接说是用户大人强烈要求我也得下场。先拿你自己在这桌上的一个毛病开刀，再挑基米刚才说的一句当面问她。像朋友，给她一个能漂亮反击的把柄，40至90字。' if legacy else brief['host_question']
target_line=next(row['id'] for row in rows[1:] if row['speaker']==target)
rows.append(speak(7,'GLM','互相吐槽',host_question,rows[:],reply_to=target_line,target=target))
chain=['ITA','AUS','GER','FRA','SWI'] if legacy else [target]+[t for t in order if t!=target]
for n,tag in enumerate(chain,8):
    prev=rows[-1];next_tag=(chain+['GLM'])[n-7]
    q=f'{names[prev["speaker"]]}刚刚点了你，先接住这句新话里具体的一个词，替自己辩一句或认一句；再点名把一条有根据的玩笑递给{names[next_tag]}，让对方下一句能接。你也可以顺便认领自己的一处毛病。只谈桌面。约45至85字。'
    rows.append(speak(n,tag,'互相吐槽',q,rows[:],reply_to=prev['id'],target=next_tag))
ending='千问刚刚把最后一棒递给你。具体接她一句，坦率说下回你自己主持想改的一点，然后向桌上五位和观众道别，明确说期待下期或下期见。不预告未经确定的剧情。50至95字。' if legacy else f'{names[chain[-1]]}刚把话递回来。接她一句，说下回自己主持愿意改的一件具体小事，和桌上朋友散场道别，可以说下回见。不喊屏幕外的人，不预告未经确定的剧情。50至95字。回复必须直接从JSON左花括号开始，到右花括号结束；台词只放text字段，不能在JSON前后另说一遍。'
rows.append(speak(13,'GLM','下期见',ending,rows[:],reply_to=rows[-1]['id'],target='全体与观众' if legacy else '全体'))
write_json(work/'approved-for-review.json',rows)
write_json(work/'public_sources.json',{i:sources[i] for i in sorted(set(common)|{i for l in focus.values() for i in l})})
