"""Export an audience-only, offline episode from frozen IRON turn records."""
import argparse
import csv
import hashlib
import html
import json
from pathlib import Path
import shutil
import sys
import textwrap
import uuid

from model_client import complete


NAMES = {"GER": "德国", "ITA": "意大利", "JAP": "日本", "ENG": "英国", "SOV": "苏联"}
COLORS = {"GER": "#89a5ba", "ITA": "#53c7a5", "JAP": "#ef8f94", "ENG": "#8aafff", "SOV": "#eec064"}
STATUS = {"applied": "已生效（存档核验）", "not_applied": "未生效", "rejected": "已拒绝", "unknown": "结果未确认"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def many(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def action_text(action):
    args, kind = action.get("args", {}), action.get("type")
    if kind == "research.start":
        return f"第 {args.get('slot')} 科研槽开始研究 {args.get('technology_id')}"
    if kind == "focus.select":
        return f"选择国策 {args.get('focus_id')}"
    if kind == "production.assign":
        return f"生产线 {args.get('line_id')} 请求分配 {args.get('factories')} 间工厂"
    if kind == "construction.queue":
        return f"地区 {args.get('state_id')} 排队建造 {args.get('count')} 个 {args.get('building_id')}"
    if kind == "diplomacy.declare_war":
        target = args.get("target_country", args.get("recipient"))
        goal = args.get("wargoal_id", args.get("goal_handle"))
        return f"请求向{NAMES.get(target, target)}（{target}）宣战，战争目标 {goal}"
    if kind == "division.move":
        return f"师 {args.get('unit_id')} 请求从 {args.get('source')} 移动至 {args.get('target')}"
    if kind == "air.mission":
        mission = {1: "制空", 2: "密接支援", 4: "拦截"}.get(args.get("mission_bit"), f"任务位 {args.get('mission_bit')}")
        return f"空军联队 {args.get('wing_id')} 的{mission}任务设为{'启用' if args.get('enabled') else '停用'}（预期掩码 {args.get('expected_mask')}）"
    if kind == "air.region":
        return f"空军联队 {args.get('wing_id')} 从空域 {args.get('expected_region')} 调往空域 {args.get('target_region')}"
    if kind == "navy.mission":
        mission = args.get("mission_type")
        name = {0: "无任务", 1: "巡逻", 2: "打击舰队", 3: "袭击运输船", 4: "护航", 5: "布雷", 6: "扫雷", 7: "训练", 9: "海军入侵支援"}.get(mission, f"任务类型 {mission}")
        return f"特遣舰队 {args.get('task_force_id')} 任务由 {args.get('expected_type')} 改为{name}"
    if kind == "navy.regions":
        return f"特遣舰队 {args.get('task_force_id')} 的活动海域由 {', '.join(map(str, args.get('expected_regions', [])))} 改为 {', '.join(map(str, args.get('target_regions', [])))}"
    if kind == "air.assign":
        mission = {"air_superiority": "夺取制空", "cas": "密接支援"}.get(args.get("mission"), args.get("mission"))
        return f"空军联队 {args.get('wing_id')} 在空域 {args.get('region_id')} 执行{mission}"
    if kind == "air.stop":
        return f"取消空军联队 {args.get('wing_id')} 的当前任务"
    if kind == "navy.assign":
        mission = {"patrol": "巡逻", "strike_force": "打击舰队任务"}.get(args.get("mission"), args.get("mission"))
        return f"舰队 {args.get('fleet_id')} 在海域 {args.get('region_id')} 执行{mission}"
    if kind == "navy.move":
        return f"舰队 {args.get('fleet_id')} 驶向海洋省份 {args.get('province_id')}"
    if kind == "navy.stop":
        return f"舰队 {args.get('fleet_id')} 驻留并停止移动"
    return f"{kind}：{json.dumps(args, ensure_ascii=False, sort_keys=True)}"


def _source(path, root, label, pointer=""):
    return {"file": label + "/" + path.relative_to(root).as_posix(), "json_pointer": pointer,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _image_paths(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _image_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from _image_paths(item)
    elif isinstance(value, str) and Path(value).suffix.lower() in {".png", ".jpg", ".jpeg"}:
        path = Path(value)
        if path.is_file():
            yield path


def collect_episode(trial, turn):
    """Build a compact evidence ledger. It is omniscient audience material, never player input."""
    trial, turn = Path(trial), Path(turn)
    result_path = turn / "result.json"
    result = read(result_path)
    tags = result["countries"]
    events, images, observations, markers = [], {}, {}, []
    war_story = any(entry.get("action", {}).get("type") == "diplomacy.declare_war" for entry in result.get("actions", []))

    def call_time(label):
        path = trial / "model_calls" / (label + ".json")
        if not path.exists():
            return None
        record = read(path)
        return record.get("recorded_at") or record.get("metadata", {}).get("ended_at")

    def add(kind, title, text, source, country=None, to=None, visibility="public", data=None, recorded_at=None):
        event = {"id": f"T{result['turn_id']}-E{len(events) + 1:03d}", "kind": kind,
                 "title": title, "text": text, "country": country, "to": to,
                 "participants": list(dict.fromkeys(x for x in (country, to) if x)),
                 "visibility": visibility, "game_date": result.get("before_save", {}).get("parsed_date"),
                 "source": source, "data": data or {}, "recorded_at": recorded_at}
        events.append(event)
        return event

    for tag in tags:
        observation_path = trial / "observations" / (tag + ".json")
        if observation_path.exists():
            observations[tag] = read(observation_path)
            war_story = war_story or "diplomacy" in observations[tag]
            for image in _image_paths(observations[tag].get("research", {})):
                images[str(image)] = {"path": str(image), "country": tag, "role": "观察截图"}
        path = trial / "private_intents" / (tag + ".json")
        if path.exists():
            value = read(path)
            if value.get("country") != tag:
                raise ValueError("Private intent belongs to another country")
            text = "\n".join(f"{label}：{value.get(key, '')}" for key, label in
                             (("goal", "目标"), ("plan", "计划"), ("concealment", "准备隐瞒")))
            for prediction in value.get("predictions", []):
                text += "\n预测：" + json.dumps(prediction, ensure_ascii=False)
            add("intent", f"{NAMES.get(tag, tag)}的事前私密意图摘要", text,
                _source(path, trial, "trial"), tag, visibility="private", data=value, recorded_at=call_time("intent_" + tag))

    path = trial / "public_diplomacy.json"
    public = read(path) if path.exists() else []
    for index, value in enumerate(public):
        tag = value["country"]
        add("public", f"公开讨论 · 第 {value.get('round', 1)} 轮 · {NAMES.get(tag, tag)}", value["message"],
            _source(path, trial, "trial", f"/{index}/message"), tag,
            data={"round": value.get("round", 1), "commitments": value.get("commitments", [])},
            recorded_at=call_time("diplomacy" + ("_" + str(value["round"]) if value.get("round", 1) > 1 else "") + "_" + tag))
        for aside in value.get("public_asides", []):
            add("public", f"{tag}回应{aside['about']} · {aside['stance']}", aside["text"],
                _source(path, trial, "trial", f"/{index}/public_asides"), tag, to=aside["about"],
                data={"public_ref": aside["ref"]})

    path = trial / "performance.json"
    if path.exists():
        performance = read(path)
        for index, value in enumerate(performance.get("host", [])):
            add("host", "主持人 · 公开点评", value["message"], _source(path, trial, "trial", f"/host/{index}"),
                data={"public_refs": value["refs"]}, recorded_at=call_time("public_host"))
        for index, value in enumerate(performance.get("interviews", [])):
            add("interview", "观众专属采访 · " + value["country"], value["answer"],
                _source(path, trial, "trial", f"/interviews/{index}"), value["country"], visibility="private",
                data={"question": value["question"], "writeback": False},
                recorded_at=call_time("audience_interview_" + value["country"]))
    for index, value in enumerate(result.get("public_events", [])):
        if value.get("visibility") != "public":
            raise ValueError("Private event in public event board")
        add("public_event", "公开可观察结果", json.dumps(value["facts"], ensure_ascii=False),
            _source(result_path, turn, "turn", f"/public_events/{index}"), data=value)

    # Inbox files contain both directions; collect each actual message once.
    messages, seen = [], set()
    for tag in tags:
        path = trial / "private_diplomacy" / (tag + ".json")
        if path.exists():
            for index, value in enumerate(read(path).get("sent", [])):
                if value.get("country") != tag:
                    raise ValueError("Outbox contains another country's private message")
                if value.get("to") is None:
                    continue
                identity = digest(value)
                if identity not in seen:
                    seen.add(identity)
                    messages.append((value, _source(path, trial, "trial", f"/sent/{index}")))
    if not messages:
        for path in sorted((trial / "model_calls").glob("private_*.json")):
            value = read(path).get("response", {})
            if value.get("to"):
                messages.append((value, _source(path, trial, "trial", "/response")))
    messages.sort(key=lambda row: row[0].get("kind") == "reply")
    for value, source in messages:
        tag, to = value["country"], value["to"]
        if tag not in tags or to not in tags or tag == to:
            raise ValueError("Private message has an invalid route")
        reply = value.get("kind") == "reply"
        add("private_reply" if reply else "private", f"私信{'回复' if reply else ''} · {tag} → {to}",
            value["message"], source, tag, to, "private", {"commitments": value.get("commitments", [])},
            recorded_at=call_time(("reply_" if reply else "private_") + tag))

    for tag in tags:
        path = trial / "private_decisions" / (tag + ".json")
        value = read(path) if path.exists() else result.get("requests", {}).get(tag)
        if value is None:
            continue
        if value.get("country") != tag or result.get("requests", {}).get(tag, value) != value:
            raise ValueError("Sealed decision differs from executed request")
        commands = "\n".join(action_text(action) for action in value.get("actions", [])) or "保持现有命令"
        text = commands + "\n提交理由：" + value.get("reason", "未记录")
        source = _source(path, trial, "trial") if path.exists() else _source(result_path, turn, "turn", "/requests/" + tag)
        add("decision", f"{NAMES.get(tag, tag)}的密封决策", text, source, tag, visibility="private",
            data={"actions": value.get("actions", []), "reason": value.get("reason", "")}, recorded_at=call_time("decision_" + tag))

    path = trial / "decision_result.json"
    for call_path in sorted((trial / "model_calls").glob("reply_*.json")):
        call = read(call_path)
        for index, rejected in enumerate(call.get("delivery_projection", {}).get("rejected", [])):
            add("undelivered_private", "未送达的私信草稿", rejected["reply"].get("message", ""),
                _source(call_path, trial, "trial", f"/delivery_projection/rejected/{index}/reply/message"),
                rejected["country"], rejected["to"], visibility="private",
                data={"status": "undelivered", "reason": rejected["reason"]})
    if path.exists():
        for index, review in enumerate(read(path).get("referee", {}).get("reviews", [])):
            tag = review["country"]
            text = ("批准" if review.get("approved") else "未批准") + "：" + review.get("reason", "")
            if review.get("concerns"):
                text += "\n事实疑点：" + "；".join(review["concerns"])
            add("referee", f"裁判审查 · {NAMES.get(tag, tag)}", text,
                _source(path, trial, "trial", f"/referee/reviews/{index}"), tag, visibility="private", recorded_at=call_time("referee"))

    for index, entry in enumerate(result.get("actions", [])):
        tag = entry["country"]
        proof = entry.get("verification", {})
        dispatch = proof.get("observations", {})
        native = result.get("execution_backend") == "native"
        declaration = entry["action"]["type"] == "diplomacy.declare_war"
        target = (entry["action"]["args"].get("target_country", entry["action"]["args"].get("recipient"))
                  if declaration else None)
        effect = entry.get("effect_status") or proof.get("effect_status") or ("full" if entry["status"] == "applied" else "none" if entry["status"] == "not_applied" else "unknown")
        if native:
            label = {"applied": "已执行（原生状态核验）", "not_applied": "未执行",
                     "rejected": "已拒绝", "unknown": "结果未确认"}.get(entry["status"], entry["status"])
            if entry["status"] == "applied" and proof.get("verified") is not True:
                label = "已提交，原生状态未确认"
            if effect == "ordered":
                label = "移动命令已下达（原生状态核验；尚未确认抵达）"
        else:
            label = "部分响应（存档核验）" if effect == "partial" else STATUS.get(entry["status"], entry["status"])
        new_declaration = declaration and entry["status"] == "applied" and proof.get("verified") is True and dispatch.get("new_war") is True and dispatch.get("declaration_sent") is True
        if not native and declaration and effect == "already_present":
            label = "已处于交战状态，本次未重复宣战（存档核验）"
        elif not native and new_declaration:
            label = "新宣战已生效（存档核验）"
        text = action_text(entry["action"]) + "\n" + label
        if native and entry["action"]["type"] == "division.move":
            native_effect = next((row for row in result.get("native_result", {}).get("effects", [])
                                  if row.get("country") == tag and row.get("action_id") == entry["action"]["id"]), {})
            if native_effect.get("arrival_observed_during_clock"):
                arrival = native_effect["first_arrival"]
                text += f"\n中途已观察抵达：游戏原生小时 {arrival['date_raw']}；终点位置 {native_effect['observed']['province']}。"
            elif "arrival_observed_during_clock" in native_effect:
                text += "\n本轮采样尚未观察到抵达；分块间的短暂经过可能未被采样。"
        if entry.get("effect_status") == "partial":
            text += f"\n部分响应：{len(dispatch.get('responded_units', []))} 个单位取得目标命令，{len(dispatch.get('blocked_units', []))} 个单位正在防守而阻塞。"
        if entry.get("ui_error") and not native:
            text += "\n界面执行出现异常；应以存档核验为准，不能直接认定为玩家反悔。"
        if entry.get("verification", {}).get("checks"):
            check_label = "原生状态核验" if native else "核验"
            text += "\n" + check_label + "：" + "；".join(key + (" ✓" if value else " ✗") for key, value in entry["verification"]["checks"].items())
        title = f"宣战执行 · {tag} → {target}" if declaration else f"实际执行 · {NAMES.get(tag, tag)}"
        event = add("execution", title, text,
            _source(result_path, turn, "turn", f"/actions/{index}"), tag, to=target,
            visibility="private",
            data={"status": entry["status"], "action": entry["action"], "verified": entry.get("verification", {}).get("verified"),
                  "effect_status": effect, "dispatch": entry.get("verification", {}).get("observations", {})},
            recorded_at=entry.get("ended_at") or entry.get("started_at"))
        for key in ("started_at", "ended_at"):
            if entry.get(key):
                markers.append({"evidence_id": event["id"], "kind": "action_" + key, "recorded_at": entry[key],
                                "game_date": event["game_date"], "label": event["title"]})
        for image in _image_paths(entry.get("ui_result", {})):
            images[str(image)] = {"path": str(image), "country": tag, "role": "动作界面证据"}
        if new_declaration:
            pairs = dispatch.get("war_pairs_added", [])
            text = f"宣战方：{NAMES.get(tag, tag)}（{tag}）\n被宣战方：{NAMES.get(target, target)}（{target}）\n执行后存档已确认新的战争状态。"
            if pairs:
                text += "\n新增交战关系：" + "；".join(" ↔ ".join(pair) for pair in pairs)
            add("war", f"宣战生效 · {tag} → {target}", text,
                _source(result_path, turn, "turn", f"/actions/{index}/verification/observations"), tag, target,
                data={key: dispatch.get(key) for key in ("actor", "target_country", "wargoal_id", "wargoal_type", "new_war", "declaration_sent", "war_pairs_added")},
                recorded_at=entry.get("ended_at"))

    for index, value in enumerate(result.get("public_notices", [])):
        if value.get("proof", {}).get("major") != "yes":
            continue
        event = add("news", value.get("title", "世界新闻"), value.get("text", ""),
                    _source(result_path, turn, "turn", f"/public_notices/{index}"))
        event["game_date"] = value.get("observed_date")
        for image in _image_paths(value):
            images[str(image)] = {"path": str(image), "country": None, "role": "公共新闻证据"}

    for key, role in (("before_map", "回合开始地图"), ("after_map", "回合结束地图")):
        for image in _image_paths(result.get(key)):
            images[str(image)] = {"path": str(image), "country": None, "role": role}
    final = result.get("final_save", {})
    final_path = turn / (final.get("name", "").removeprefix("hoi4live_") + ".json")
    if result.get("execution_backend") == "native":
        for tag in tags:
            state = result.get("native_after", {}).get(tag)
            if not isinstance(state, dict):
                continue
            army = state.get("army", {})
            divisions = state.get("divisions", [])
            naval_air = state.get("naval_air", {})
            wars = army.get("wars", [])
            wings = naval_air.get("air_wings", [])
            task_forces = naval_air.get("task_forces", [])
            text = f"原生状态回读 · {state.get('date', '日期未记录')}"
            text += f"\n陆军师：{army.get('division_count', len(divisions))}"
            if divisions:
                text += "\n师位置：" + "；".join(
                    f"{row.get('id')} 位于 {row.get('location', '未知')}" +
                    (f"（路径：{' → '.join(map(str, row['path']))}）" if row.get("path") else "")
                    for row in divisions)
            text += "\n交战关系：" + ("；".join(
                f"{row.get('enemy', row.get('country', row))}" if isinstance(row, dict) else str(row)
                for row in wars) or "无已记录交战关系")
            text += f"\n空军联队：{len(wings)}；特遣舰队：{len(task_forces)}"
            for wing in wings:
                text += f"\n空军 {wing.get('id')}：空域 {wing.get('region', '未知')}，任务掩码 {wing.get('current', '未知')}"
            for force in task_forces:
                text += f"\n特遣舰队 {force.get('id')}：所属舰队 {force.get('fleet_id', '未记录')}，任务类型 {force.get('current', '未知')}，海域 {', '.join(map(str, force.get('regions', [])))}"
            snapshot = {"date": state.get("date"), "date_raw": state.get("date_raw"),
                        "division_count": army.get("division_count", len(divisions)), "divisions": divisions,
                        "wars": wars, "air_wings": wings, "task_forces": task_forces}
            add("outcome", f"原生回读状态 · {NAMES.get(tag, tag)}", text,
                _source(result_path, turn, "turn", f"/native_after/{tag}"), tag, visibility="private", data=snapshot)
            events[-1]["game_date"] = state.get("date") or result.get("before_save", {}).get("parsed_date")
    elif final.get("name") and final_path.is_file():
        state = read(final_path)
        for tag in tags:
            own, old = state["countries"][tag], observations.get(tag, {})
            technologies = own.get("technology", {})
            snapshot = {"political_power": own.get("politics", {}).get("political_power"),
                        "focus": own.get("focus", {}), "research_slots": technologies.get("slots", {}),
                        "division_count": len(many(own.get("units", {}).get("division")))}
            text = f"日期：{state['date']}\n政治点数：{old.get('political_power', '未记录')} → {snapshot['political_power']}"
            text += f"\n当前国策：{snapshot['focus'].get('current') or '无'}；进度：{snapshot['focus'].get('progress', '未知')}"
            text += f"\n师数量：{len(old['divisions']) if 'divisions' in old else '未记录'} → {snapshot['division_count']}"
            text += "\n在研项目：" + "、".join(key for key in snapshot["research_slots"] if key != "empty")
            if war_story:
                from army_actions import own_wars, own_active_battles
                wars, battles = own_wars(own, tag), own_active_battles(state, tag)
                snapshot.update(own_wars=wars, active_battles=battles, combat_observation_available="combat" in state)
                text += "\n当前交战国家：" + ("、".join(f"{NAMES.get(row['enemy'], row['enemy'])}（{row['enemy']}）" for row in wars) or "无已记录交战关系")
                text += f"\n已记录本国陆战：{len(battles)} 处" if "combat" in state else "\n陆战数据：本次存档未采集"
            if "naval_air" in old:
                from naval_air_actions import naval_air_observation
                forces = naval_air_observation(state, tag)
                snapshot["naval_air"] = {key: forces[key] for key in ("air_wings", "fleets", "air_data_available")}
                text += f"\n本国海军：{len(forces['fleets'])} 支舰队"
                text += f"\n本国空军：{len(forces['air_wings'])} 个联队" if forces["air_data_available"] else "\n空军数据：本次存档未采集"
            event = add("outcome", f"回合后状态 · {NAMES.get(tag, tag)}", text,
                        _source(final_path, turn, "turn", "/countries/" + tag), tag, data=snapshot)
            event["game_date"] = state["date"]
    if result.get("execution_backend") == "native":
        ending = (f"回合状态：{result['status']}\n共同推进：{result.get('elapsed_game_hours', '未完成')} 游戏小时"
                  f"\n终点：{final.get('parsed_date', '未记录')}\n存档文件核验：{'已确认' if final.get('path') else '未记录'}")
    else:
        ending = (f"回合状态：{result['status']}\n共同推进：{result.get('elapsed_game_hours', '未完成')} 游戏小时"
                  f"\n终点：{final.get('parsed_date', result.get('after_clock', {}).get('date', '未核验'))}")
    add("clock", "本轮结果", ending, _source(result_path, turn, "turn"),
        data={"status": result["status"], "hours": result.get("elapsed_game_hours"), "final_save_name": final.get("name")})
    for step in result.get("clock_steps", []):
        if step.get("observed_at"):
            markers.append({"evidence_id": events[-1]["id"], "kind": "clock_check", "recorded_at": step["observed_at"],
                            "game_date": step.get("receipt", {}).get("date"), "label": "已推进 " + str(step.get("advanced_hours")) + " 游戏小时"})
    for event in events:
        if event.get("recorded_at") and event["kind"] != "execution":
            markers.append({"evidence_id": event["id"], "kind": event["kind"], "recorded_at": event["recorded_at"],
                            "game_date": event["game_date"], "label": event["title"]})

    path = trial / "commitments.json"
    commitments = read(path) if path.exists() else {"entries": []}
    profile_path = trial / "show_profiles.json"
    profiles = read(profile_path).get("profiles", {}) if profile_path.exists() else {}
    players = {}
    for tag in tags:
        model = None
        for phase in ("intent", "decision", "diplomacy"):
            path = trial / "model_calls" / (phase + "_" + tag + ".json")
            if path.exists():
                model = read(path).get("metadata", {}).get("requested_model")
                break
        players[tag] = {"name": profiles.get(tag, {}).get("name") or NAMES.get(tag, tag),
                        "persona": profiles.get(tag, {}).get("persona", ""), "model": model}
    return {"schema_version": 1, "audience": "audience_full", "turn_id": result["turn_id"],
            "match_id": result["match_id"], "countries": tags, "events": events,
            "players": players,
            "commitments": commitments, "image_sources": list(images.values()),
            "recording_markers": markers,
            "source_runs": {"trial": trial.name, "turn": turn.name},
            "notice": "全知观众素材，包含各国私信与密封决策。私密意图是事前提交的决策摘要，不是模型隐藏思维链。禁止作为玩家观察输入。"}


def validate_commentary(value, episode):
    if not isinstance(value, dict) or set(value) != {"title", "facts", "analysis", "jokes", "highlights"}:
        raise ValueError("Commentary schema differs from the audience contract")
    evidence = {event["id"]: event for event in episode["events"]}
    if not isinstance(value["title"], str) or not value["title"] or len(value["title"]) > 120:
        raise ValueError("Invalid commentary title")
    for key in ("facts", "analysis", "jokes", "highlights"):
        if not isinstance(value[key], list) or len(value[key]) > 8:
            raise ValueError("Invalid commentary section")
        for row in value[key]:
            if not isinstance(row, dict):
                raise ValueError("Commentary entries must be objects")
            if key == "facts":
                if set(row) != {"evidence_id", "quote"} or not isinstance(row["evidence_id"], str) or row["evidence_id"] not in evidence or not isinstance(row["quote"], str) or not row["quote"] or row["quote"] not in evidence[row["evidence_id"]]["text"]:
                    raise ValueError("A commentary fact must quote existing evidence verbatim")
            else:
                text_key = "title" if key == "highlights" else "text"
                if set(row) != {text_key, "evidence_ids"} or not isinstance(row[text_key], str) or not row[text_key] or len(row[text_key]) > 800:
                    raise ValueError("Invalid commentary text")
                ids = row["evidence_ids"]
                if not isinstance(ids, list) or not ids or any(not isinstance(identity, str) or identity not in evidence for identity in ids):
                    raise ValueError("Commentary cites missing evidence")
    return value


def generate_commentary(trial, turn, config):
    """Generate commentary for viewers only. Never writes player memory or observations."""
    trial, turn = Path(trial), Path(turn)
    episode = collect_episode(trial, turn)
    evidence = [{key: event.get(key) for key in ("id", "kind", "country", "to", "title", "text", "game_date")}
                for event in episode["events"]]
    payload = {"turn_id": episode["turn_id"], "events": evidence,
               "task": "根据证据为中文策略对战视频写简短解说。facts只能逐字摘录证据text；analysis明确是判断；jokes幽默但不能编造行动。区分故意欺骗、临时改计划、策略失败、执行器故障，证据不足不能称背叛。承诺尚无强制力，生产请求不等于实际获得工厂。旧记录没有事前意图时不得把事后理由伪装成事前谋划。每类至多3条，不必凑数；每条事实摘录至多60字、判断至多100字、吐槽至多60字，标题至多30字。总输出务必简短以适配1800 token上限。只输出{title,facts:[{evidence_id,quote}],analysis:[{text,evidence_ids}],jokes:[{text,evidence_ids}],highlights:[{title,evidence_ids}]}。"}
    messages = [{"role": "system", "content": "你是只面向观众的独立赛后解说。玩家不会收到你的全知信息。只输出JSON。"},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    settings = {key: config[key] for key in ("base_url", "model", "api_key_env", "temperature", "max_tokens", "enable_thinking",
                                            "include_thinking_parameter", "json_mode", "api_format", "omit_temperature", "request_parameters") if key in config}
    directory = trial / "audience_commentary"
    path = directory / "model_call.json"
    if path.exists():
        record = read(path)
        if record.get("messages") != messages or record.get("settings") != settings:
            raise ValueError("Existing commentary call has different evidence or model settings")
    else:
        response, metadata = complete(config, messages)
        record = {"audience": "audience_full", "messages": messages, "settings": settings,
                  "response": response, "metadata": metadata}
        write(path, record)
    value, normalizations = normalize_commentary_quotes(record["response"], episode)
    if normalizations:
        record["normalized_response"] = value
        record["normalizations"] = normalizations
        write(path, record)
    output = {"audience": "audience_full", "kind": "model_commentary", "content": value,
              "evidence_sha256": digest(episode["events"]), "metadata": record["metadata"]}
    if normalizations:
        output["normalizations"] = normalizations
    write(directory / "commentary.json", output)
    return output


def normalize_commentary_quotes(value, episode):
    """Retain raw model output; never publish a paraphrase as a verbatim quote."""
    import copy
    candidate = copy.deepcopy(value)
    evidence = {event["id"]: event for event in episode["events"]}
    changes, facts = [], []
    for key in ("facts", "analysis", "jokes", "highlights"):
        rows = candidate.get(key)
        if isinstance(rows, list) and len(rows) > 8:
            changes.append({"kind": "cap_section", "section": key, "kept": 8, "dropped": len(rows) - 8})
            candidate[key] = rows[:8]
    for index, fact in enumerate(candidate.get("facts", [])):
        if (not isinstance(fact, dict) or set(fact) != {"evidence_id", "quote"}
                or fact.get("evidence_id") not in evidence or not isinstance(fact.get("quote"), str)
                or not fact["quote"]):
            facts.append(fact)  # Leave malformed sources to the strict validator.
            continue
        quote = fact["quote"]
        source = evidence[fact["evidence_id"]]["text"]
        if quote in source:
            facts.append(fact)
        elif quote.endswith(("。", ".")) and len(quote) > 1 and quote[:-1] in source:
            fact["quote"] = quote[:-1]
            facts.append(fact)
            changes.append({"index": index, "evidence_id": fact["evidence_id"], "kind": "trim_added_terminal_period",
                            "original_quote": quote, "quote": fact["quote"]})
        else:
            changes.append({"index": index, "evidence_id": fact["evidence_id"], "kind": "omit_nonverbatim_fact",
                            "original_quote": quote})
    candidate["facts"] = facts
    return validate_commentary(candidate, episode), changes


def _srt_time(seconds):
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    sec, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{sec:02d},{millis:03d}"


def subtitle_timeline(events):
    """Editorial timing starts at zero; it is not a claim about OBS capture timecodes."""
    cues, chapters, cursor = [], [], 0.0
    for event in events:
        start = cursor
        label = ("私密摘要" if event["kind"] == "intent" else "提交理由" if event["kind"] == "decision" else "")
        text = event["title"] + ("（" + label + "）" if label else "") + "。" + event["text"]
        lines = [line for part in text.splitlines() for line in textwrap.wrap(part, width=24, break_long_words=True, break_on_hyphens=False)]
        for index in range(0, len(lines), 2):
            content = "\n".join(lines[index:index + 2])
            duration = min(8.0, max(2.0, len(content.replace("\n", "")) / 6))
            cues.append({"start": cursor, "end": cursor + duration, "text": content, "evidence_id": event["id"]})
            cursor += duration + .2
        chapters.append({"evidence_id": event["id"], "start": start, "end": max(start, cursor - .2)})
    return cues, chapters


def _commentary_events(episode, commentary):
    if commentary is None:
        return []
    if commentary.get("audience") != "audience_full" or commentary.get("evidence_sha256") != digest(episode["events"]):
        raise ValueError("Commentary belongs to different turn evidence")
    content = validate_commentary(commentary["content"], episode)
    result = []
    for key, title in (("facts", "事实摘录"), ("analysis", "解说判断"), ("jokes", "解说吐槽")):
        for index, row in enumerate(content[key]):
            ids = [row["evidence_id"]] if key == "facts" else row["evidence_ids"]
            result.append({"id": f"T{episode['turn_id']}-C-{key}-{index + 1}", "kind": "commentary", "title": title,
                           "text": row["quote"] if key == "facts" else row["text"], "country": None,
                           "to": None, "participants": [], "visibility": "audience_only", "game_date": None,
                           "source": {"file": "trial/audience_commentary/commentary.json", "evidence_ids": ids}, "data": {"evidence_ids": ids}})
    return result


def _render_html(episode):
    esc = lambda value: html.escape(str(value), quote=True)
    players = episode.get("players", {})
    countries = "".join(f'<button class="country" data-tag="{esc(tag)}" title="{esc(players.get(tag, {}).get("model") or "模型未记录")}" style="--accent:{COLORS.get(tag, "#a5a5d5")}">{esc(players.get(tag, {}).get("name") or NAMES.get(tag, tag))} · {esc(tag)}</button>' for tag in episode["countries"])
    cards = []
    for index, event in enumerate(episode["events"]):
        secret = event["visibility"] != "public"
        source = event["source"]
        refs = " ".join(f'<a href="#{esc(identity)}">{esc(identity)}</a>' for identity in source.get("evidence_ids", []))
        body, technical = event["text"], ""
        if event["kind"] == "intent" and all(key in event.get("data", {}) for key in ("goal", "plan", "concealment")):
            intent = event["data"]
            lines = [f"{label}：{intent[key]}" for key, label in (("goal", "目标"), ("plan", "计划"), ("concealment", "准备隐瞒"))]
            for prediction in intent.get("predictions", []):
                confidence = prediction.get("confidence")
                certainty = f"{confidence:.0%}" if type(confidence) in (int, float) else "未注明"
                lines.append(f"预测 · {prediction.get('subject', '')}：{prediction.get('prediction', '')}（信心 {certainty}）")
            body = "\n".join(lines)
        elif event["kind"] == "execution" and "\n核验：" in body:
            body, technical = body.split("\n核验：", 1)
            technical = '<p class="technical">核验明细：' + esc(technical) + '</p>'
        cards.append(f'''<article id="{esc(event['id'])}" data-index="{index}" data-tags="{esc(' '.join(event['participants']))}" data-secret="{str(secret).lower()}" style="--accent:{COLORS.get(event['country'], '#b69bff')}"><div class="meta">{esc(event['id'])} · {esc(event['kind'])} · {esc(event.get('game_date') or '同回合')} {'· 私密／观众专属' if secret else '· 公开'}</div><h2>{esc(event['title'])}</h2><p>{esc(body)}</p><details><summary>记录来源</summary><code>{esc(source.get('file', ''))}{esc(source.get('json_pointer', ''))}</code><br>{refs}{technical}</details></article>''')
    gallery = "".join(f'<figure><a href="{esc(item["file"])}"><img loading="lazy" src="{esc(item["file"])}" alt="{esc(item["role"])}"></a><figcaption>{esc(item.get("country") or "公共")} · {esc(item["role"])}</figcaption></figure>' for item in episode.get("assets", []) if item.get("kind") == "image")
    pledge_cards = "".join(f'<li><b>{esc(row.get("country"))} → {esc(row.get("to") or "公开")}</b>：{esc(row.get("text"))}<br><small>截至回合 {esc(row.get("due_turn"))}；条件：{esc(row.get("condition"))}；状态：未核实，不能据此认定兑现或背叛。</small></li>' for row in episode.get("commitments", {}).get("entries", []))
    reveal_handler = "document.getElementById('all').click();document.getElementById('secrets').checked=true;document.getElementById('reveal').click();"
    highlights = []
    for item in episode.get("commentary", {}).get("content", {}).get("highlights", []):
        links = " ".join(f'<a href="#{esc(identity)}" onclick="{reveal_handler}">{esc(identity)}</a>' for identity in item["evidence_ids"])
        highlights.append(f'<li><strong>{esc(item["title"])}</strong> · {links}</li>')
    highlight_panel = '<section><h2>本轮看点 · 解说建议</h2><ol>' + ''.join(highlights) + '</ol><p><small>点击证据会揭示对应的观众私密记录；看点是模型建议，按原话和结果核对。</small></p></section>' if highlights else ''
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>IRON 回合 {esc(episode['turn_id'])} · 观众回看</title><style>
    :root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:#0e1420;color:#e6edf6;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}}header,main,footer{{max-width:1100px;margin:auto;padding:24px}}header{{padding-bottom:8px}}h1{{font-size:32px;margin:8px 0}}.notice{{background:#29223b;border-left:4px solid #c7a2ff;padding:12px 18px}}.bar{{position:sticky;top:0;background:#121b2aee;backdrop-filter:blur(8px);z-index:2;padding:14px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}button{{background:#1d2b3e;color:#eef4ff;border:1px solid #425772;border-radius:7px;padding:8px 12px;cursor:pointer}}button.country{{border-color:var(--accent)}}button.active{{background:#385574}}input[type=range]{{flex:1;min-width:120px}}article{{scroll-margin-top:140px;border:1px solid #334154;border-left:5px solid var(--accent);border-radius:10px;padding:20px;margin:18px 0;background:#151f2e}}article h2{{font-size:20px;margin:6px 0}}article p{{white-space:pre-wrap;overflow-wrap:anywhere}}.meta,small,summary,footer{{color:#a6b8ce;font-size:13px}}code{{overflow-wrap:anywhere}}a{{color:#9cc6ff}}.gallery{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}}figure{{margin:0}}img{{width:100%;border-radius:8px}}[hidden]{{display:none!important}}.empty{{padding:20px;color:#a6b8ce}}ol{{padding-left:24px}}
    </style><header><small>IRON · {esc(episode['match_id'])} · 第 {esc(episode['turn_id'])} 回合</small><h1>说了什么，打算什么，实际做了什么</h1><p class="notice">{esc(episode['notice'])}</p><p>字幕和剪辑表使用建议剪辑时间，须在剪辑软件中对齐实际录屏。历史缺少意图摘要时，仅展示已有提交理由。</p></header><main>{highlight_panel}<nav class="bar"><button id="all" class="active">全部国家</button>{countries}<label><input id="secrets" type="checkbox" checked> 揭示私密记录</label><button id="prev">上一步</button><input id="progress" type="range" min="1" max="{len(episode['events'])}" value="1"><button id="next">揭示下一条</button><button id="reveal">全部揭示</button><output id="count"></output></nav><div id="cards">{''.join(cards)}</div><p id="empty" class="empty" hidden>当前筛选没有已揭示记录。继续揭示，或切换国家／私密记录。</p><details><summary>承诺账本（原话摘录，履约状态另行核实）</summary><ol>{pledge_cards or '<li>这一回合没有结构化承诺记录。</li>'}</ol></details><h2>界面与截图证据</h2><div class="gallery">{gallery or '<p>本素材包没有可复制的截图。</p>'}</div></main><footer>全知观众包 · 所有判断和吐槽均应对照原始证据。无需联网，可直接打开本文件。</footer><script>
    const cards=[...document.querySelectorAll('article')],p=document.getElementById('progress'),s=document.getElementById('secrets');let tag='';function update(){{let n=0;for(const card of cards){{const ok=Number(card.dataset.index)<Number(p.value)&&(!tag||card.dataset.tags.split(' ').includes(tag))&&(s.checked||card.dataset.secret==='false');card.hidden=!ok;if(ok)n++}}document.getElementById('count').textContent=p.value+' / '+cards.length;document.getElementById('empty').hidden=n>0;document.querySelectorAll('.country').forEach(b=>b.classList.toggle('active',b.dataset.tag===tag));document.getElementById('all').classList.toggle('active',!tag)}}p.oninput=update;s.onchange=update;document.getElementById('prev').onclick=()=>{{p.value=Math.max(1,Number(p.value)-1);update()}};document.getElementById('next').onclick=()=>{{p.value=Math.min(cards.length,Number(p.value)+1);update()}};document.getElementById('reveal').onclick=()=>{{p.value=cards.length;update()}};document.getElementById('all').onclick=()=>{{tag='';update()}};document.querySelectorAll('.country').forEach(b=>b.onclick=()=>{{tag=b.dataset.tag;update()}});update();
    </script></html>'''


def export_episode(trial, turn, output, commentary=None, load_cached_commentary=True, production_markers=None):
    trial, turn, output = Path(trial), Path(turn), Path(output)
    episode = collect_episode(trial, turn)
    owner = {key: episode[key] for key in ("source_runs", "match_id", "turn_id")}
    owner["evidence_sha256"] = digest(episode["events"])
    owner_path = output / "export_owner.json"
    for marker in production_markers or []:
        episode["recording_markers"].append({"evidence_id": None, "kind": "campaign_phase",
            "recorded_at": marker["recorded_at"], "epoch": marker.get("epoch"), "campaign_turn": marker["turn"],
            "phase": marker["phase"], "game_date": None, "label": marker["phase"]})
    if output.exists() and any(output.iterdir()):
        manifest = output / "episode.json"
        if owner_path.is_file():
            same_source = read(owner_path) == owner
        else:
            existing = read(manifest) if manifest.is_file() else {}
            same_source = all(existing.get(key) == episode[key] for key in ("source_runs", "match_id", "turn_id"))
        if not same_source:
            raise ValueError("Output is not an existing package for this trial and turn")
    output.mkdir(parents=True, exist_ok=True)
    write(owner_path, owner)
    if commentary is None and load_cached_commentary:
        path = trial / "audience_commentary/commentary.json"
        if path.exists():
            commentary = read(path)
    episode["events"].extend(_commentary_events(episode, commentary))
    episode["commentary"] = commentary or {"kind": "deterministic_overview", "notice": "未调用解说模型；仅展示真实记录，没有生成模型吐槽。"}
    assets = []
    for source in episode.pop("image_sources"):
        path = Path(source["path"])
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        target = Path("assets") / (sha[:16] + path.suffix.lower())
        (output / "assets").mkdir(exist_ok=True)
        shutil.copy2(path, output / target)
        assets.append({"kind": "image", "file": target.as_posix(), "country": source["country"], "role": source["role"], "sha256": sha})
    episode["assets"] = assets
    cues, chapters = subtitle_timeline(episode["events"])
    timing = {row["evidence_id"]: row for row in chapters}
    write(output / "episode.json", episode)
    write(output / "assets.json", assets)
    (output / "timeline.jsonl").write_text("".join(json.dumps({**event, "editorial_timing": timing[event["id"]]}, ensure_ascii=False) + "\n" for event in episode["events"]), encoding="utf-8")
    with (output / "recording_markers.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["recorded_at_utc", "epoch", "campaign_turn", "phase", "evidence_id", "kind", "game_date", "label", "suggested_editorial_start", "suggested_editorial_end"])
        writer.writeheader()
        for marker in sorted(episode["recording_markers"], key=lambda row: row["recorded_at"]):
            window = timing.get(marker["evidence_id"])
            writer.writerow({"recorded_at_utc": marker["recorded_at"], **{key: marker[key] for key in ("evidence_id", "kind", "game_date", "label")},
                             **{key: marker.get(key) for key in ("epoch", "campaign_turn", "phase")},
                             "suggested_editorial_start": round(window["start"], 3) if window else "",
                             "suggested_editorial_end": round(window["end"], 3) if window else ""})
    with (output / "timeline.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["evidence_id", "kind", "country", "to", "visibility", "game_date", "recorded_at", "editorial_start", "editorial_end", "title", "text", "source"])
        writer.writeheader()
        for event in episode["events"]:
            writer.writerow({"evidence_id": event["id"], **{key: event.get(key) for key in ("kind", "country", "to", "visibility", "game_date", "recorded_at", "title", "text")},
                             "editorial_start": round(timing[event["id"]]["start"], 3), "editorial_end": round(timing[event["id"]]["end"], 3),
                             "source": event["source"]["file"] + event["source"].get("json_pointer", "")})
    (output / "subtitles.srt").write_text("\n".join(f"{index}\n{_srt_time(cue['start'])} --> {_srt_time(cue['end'])}\n{cue['text']}\n" for index, cue in enumerate(cues, 1)), encoding="utf-8")
    (output / "index.html").write_text(_render_html(episode), encoding="utf-8")
    draft = ["# 旁白与原话清单", "", "以下时间为建议剪辑时间，不是录屏时间码。引号内原话引用须对照证据；改写配音请标为旁白。私密意图是事前提交摘要；旧记录中的提交理由不当作事前意图。", ""]
    shots = ["# 剪辑镜头表", "", "先挑选有因果联系的发言、秘密命令和结果；本表时间是完整记录的建议时间，可按故事线重新剪辑。", "", "| 建议起点 | 证据 | 内容 | 建议画面 |", "| --- | --- | --- | --- |"]
    for event in episode["events"]:
        stamp = _srt_time(timing[event["id"]]["start"])
        draft.extend([f"## {stamp} · {event['id']} · {event['title']}", "", event["text"], "", f"来源：{event['source']['file']}{event['source'].get('json_pointer', '')}", ""])
        visual = {"intent": "角色头像＋私密意图卡", "public": "公开聊天气泡", "private": "两国头像＋私信气泡", "private_reply": "私信回复", "decision": "密封命令揭示", "execution": "操作截图／地图，注明执行状态", "outcome": "回合前后数据／地图对比", "news": "游戏新闻截图", "commentary": "解说字幕＋引用证据"}.get(event["kind"], "地图／回合总结")
        shots.append(f"| {stamp} | {event['id']} | {event['title'].replace('|', '／')} | {visual} |")
    (output / "narration.md").write_text("\n".join(draft), encoding="utf-8")
    (output / "shotlist.md").write_text("\n".join(shots) + "\n", encoding="utf-8")
    (output / "README.txt").write_text("双击 index.html 离线回看。该包为 audience_full，包含全部私信与密封决策，不能交给参赛 Agent。\nsubtitles.srt、timeline.csv、shotlist.md 使用建议剪辑时间；recording_markers.csv 中真实UTC需与OBS录像锚点对齐。\nassets/为已复制截图；原始游戏存档不复制，其名称见本轮结果。已引用JSON记录的文件名与哈希保留在episode.json。\n没有模型解说时，只展示确定性事实记录，不冒充模型点评。\n", encoding="utf-8")
    return {"output": str(output.resolve()), "audience": "audience_full", "events": len(episode["events"]), "assets": len(assets), "subtitle_cues": len(cues), "editorial_seconds": round(cues[-1]["end"], 3) if cues else 0,
            "commentary": episode["commentary"]["kind"]}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("export", "commentary"):
        arg = sub.add_parser(command)
        arg.add_argument("--trial", type=Path, required=True)
        arg.add_argument("--turn", type=Path, required=True)
        arg.add_argument("--model-config", type=Path)
        if command == "export":
            arg.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "commentary" and not args.model_config:
        parser.error("commentary requires --model-config")
    commentary = generate_commentary(args.trial, args.turn, read(args.model_config)) if args.model_config else None
    value = export_episode(args.trial, args.turn, args.output, commentary) if args.command == "export" else commentary
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
