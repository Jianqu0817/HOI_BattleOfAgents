"""Record a fresh cast meeting and country draft using only public dialogue."""
import argparse
import copy
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
from campaign import load_config, load_model_environment, name_ok
from m0_probe import write_json
from model_trial import recorded_complete, read

def record(name):
    work = ROOT / "runs" / name_ok(name)
    frozen = work / "preshow_config.json"
    if not frozen.exists():
        config = load_config("configs/campaign.tyrol.json")
        template = read(ROOT / "configs/campaign.recording50.json")
        template["scenario"] = config["scenario"]
        write_json(frozen, {"config": config, "template": template})
    source = read(frozen)
    config = source["config"]
    load_model_environment(config)
    template = copy.deepcopy(source["template"])
    assignment = config["production"]["assignments"]
    roles = {role: tag for tag, role in assignment.items() if tag != "host"}
    cast = {r["character_id"]: r for r in config["show"]["public_cast"]}
    public_names = {role:r["name"] for role,r in cast.items()}
    guests = ["deepseek","gpt","gemini","claude","qwen"]
    order_path = work / "draft_order.json"
    if not order_path.exists():
        write_json(order_path,random.SystemRandom().sample(guests,len(guests)))
    history = []
    def say(role, kind, cue, available=None):
        tag = roles.get(role)
        profile = config["show"]["profiles"][tag] if tag else config["show"]["host_profile"]
        model = config["show"]["models"][tag] if tag else config["model"]
        key = f"P{len(history)+1:02d}"
        payload = {"桌面":"邀请函上写着来教室拍张合照，顺便看一眼地图。刚到时还没有选国。","演员":public_names,
                   "已说出口的原话":history,"此刻":cue}
        if available is not None:
            payload["可选国家"] = available
        instruction = ("只说自己在现场能说出口的话，别代人做决定。"+config["show"].get("spoken_style",""))
        if role=="glm":
            instruction += config["show"].get("host_table_brief","")
        fmt = '{"text":"口播","country":"可选国家代码"}' if available is not None else '{"text":"口播"}'
        payload["地图背景"]="本场为1936年9月4日晚的架空存档。671由奥地利控制、6673由意大利控制，奥意已交战；桌上的国家牌尚待抽选。"
        messages = [{"role":"system","content":profile["persona"]+"\n"+instruction},
                    {"role":"user","content":json.dumps(payload,ensure_ascii=False)+"\n只输出一个 JSON 对象，键名和结构严格使用："+fmt}]
        value = recorded_complete(work,key,model,messages,"preshow",tag,True)["response"]
        row = {"id":key,"kind":kind,"role":role,"speaker":public_names[role],"text":value["text"].strip()}
        if not row["text"]:raise ValueError("Empty speech: "+key)
        if available is not None:
            if value.get("country") not in available:raise ValueError("Unavailable country: "+key)
            row["country"] = value["country"]
        history.append(row)
        write_json(work/"开桌公开原话.json",history)
        print(key,row["speaker"],flush=True)
        return row
    say("glm","host","门开了，五个朋友第一次聚到桌边。接上合照邀请，自然招呼进门的人，别重新讲游戏。")
    for role in guests:
        say(role,"intro","进门报个名字，让第一次见面的人知道你是哪一位模型、有哪件事值得认识。也可以顺着桌边刚发生的事说下去。")
    say("glm","host","人齐了，要拍第一张合照。若刚才有趣事可以吐槽，拍照时到底站哪儿由她们自己选。")
    for role in guests:
        say(role,"social","合照前的空当，可以随意说句话或留一句很短的回应。")
    say("glm","history","地图摊开了。讲清1919年条约后的南蒂罗尔、布伦纳山口，和本场架空存档的区别。1936年9月4日晚奥意已交战，671奥控、6673意控；停在当前线与承认归属是两件事。把德、法、瑞各自可能介入或旁观的理由说得好懂。")
    available = {r["country"]:r["country_name"] for r in cast.values() if r["country"]}
    seats = {}
    for role in read(order_path):
        row=say(role,"draft","轮到你选国。自己挑一张还在桌上的牌，说你看中了什么。",available)
        seats[row["country"]]=role
        del available[row["country"]]
    say("glm","host","人和国家对上了。就桌上刚刚说过的一桩趣事或分歧接到开局，把镜头交给地图。")
    template["production"]["assignments"]={**seats,"host":"glm"}
    template["player_model_configs"]={t:source["template"]["player_model_configs"][roles[r]] for t,r in seats.items()}
    template["scenario"]["opening"]+="\n入场与选国的公开原话：\n"+"\n".join(r["speaker"]+"："+r["text"] for r in history)
    template.update(turn_limit=7,step_hours=24)
    template["clock_policy"].update(war_hours=24,critical_hours=24,peace_hours=24,maximum_hours=24)
    write_json(work/"campaign.json",template)
    write_json(work/"seats.json",seats)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name",required=True)
    record(parser.parse_args().name)
