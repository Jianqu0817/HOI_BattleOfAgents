"""Per-country, evidence-bound post-turn lessons without omniscient commentary."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import sys
import uuid

from episode import action_text, digest, many, read
from agent_contract import object_id
from model_client import complete


CATEGORIES = {"observation", "execution", "strategy", "diplomacy"}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def country_evidence(trial, turn, tag):
    """Read the country's own files and public material only into its learning context."""
    trial, turn = Path(trial), Path(turn)
    result = read(turn / "result.json")
    if tag not in result["countries"]:
        raise ValueError("Lesson country is outside this turn")
    observed = read(trial / "observations" / (tag + ".json"))
    request = read(trial / "private_decisions" / (tag + ".json"))
    if observed.get("country") != tag or request.get("country") != tag or result["requests"].get(tag) != request:
        raise ValueError("Own lesson request differs from executed country request")
    if any(request.get(key) != observed.get(key) for key in ("match_id", "turn_id", "date", "observation_id")) or observed["turn_id"] != result["turn_id"]:
        raise ValueError("Own lesson observation does not match the frozen turn")
    evidence = []

    def add(kind, value, text=None):
        evidence.append({"id": f"T{result['turn_id']}-{tag}-{kind}-{len(evidence) + 1}", "kind": kind,
                         "text": text if text is not None else json.dumps(value, ensure_ascii=False, sort_keys=True), "data": value})

    own = {key: observed.get(key) for key in ("country", "date", "political_power", "government", "focus", "military_production", "construction", "army", "unavailable")}
    research = observed.get("research", {})
    own["research"] = {key: value for key, value in research.items() if key != "screen_evidence"}
    own["divisions"] = [{key: unit.get(key) for key in ("id", "name", "location", "organisation", "strength") if key in unit}
                        for unit in observed.get("divisions", [])]
    own["owned_state_count"] = len(observed.get("owned_states", []))
    add("observation", own)
    path = trial / "private_intents" / (tag + ".json")
    if path.exists():
        intent = read(path)
        if intent.get("country") != tag:
            raise ValueError("Intent is not owned by the learning country")
        add("intent", intent)
    path = trial / "public_diplomacy.json"
    if path.exists():
        for message in read(path):
            add("public", message, message["country"] + "：" + message["message"])
    path = trial / "private_diplomacy" / (tag + ".json")
    if path.exists():
        inbox = read(path)
        for direction in ("sent", "received"):
            for message in inbox.get(direction, []):
                sender, recipient = message.get("country"), message.get("to")
                if sender not in result["countries"] or (recipient is not None and recipient not in result["countries"]) or sender == recipient:
                    raise ValueError("Malformed private message route in own lesson evidence")
                if direction == "sent" and sender != tag or direction == "received" and recipient != tag:
                    raise ValueError("Private lesson inbox contains another country's private conversation")
                if recipient is not None:
                    add("private", message, f"{sender} → {recipient}：{message['message']}")
    add("decision", request, "原始提交理由：" + request.get("reason", "") + "\n" +
        ("\n".join(action_text(action) for action in request["actions"]) or "保持现有命令"))
    for entry in result.get("actions", []):
        if entry["country"] == tag:
            value = {"country": tag, "action": entry["action"], "status": entry["status"],
                     "verification": entry.get("verification"),
                     "ui_error": "execution_adapter_error" if entry.get("ui_error") else None}
            add("execution", value)
    for notice in result.get("public_notices", []):
        if notice.get("proof", {}).get("major") == "yes":
            add("news", {"title": notice.get("title"), "text": notice.get("text"), "observed_date": notice.get("observed_date")})
    final = result.get("final_save", {})
    path = turn / (final.get("name", "").removeprefix("hoi4live_") + ".json")
    if final.get("name") and path.exists():
        state = read(path)
        country = state["countries"][tag]
        technology = country.get("technology", {})
        completed = {key for key, value in technology.get("technologies", {}).items()
                     if isinstance(value, dict) and value.get("level", 0) > 0}
        lines = [{key: row.get(key) for key in ("id", "requested_factories", "active_factories", "equipment_variant_index")}
                 for row in many(country.get("production", {}).get("military_lines"))]
        outcome = {"country": tag, "date": state["date"], "political_power": country.get("politics", {}).get("political_power"),
                   "focus": country.get("focus", {}), "research": {"slots": technology.get("slots", {}),
                       "completed_count": len(completed), "completed_since_observation": sorted(completed - set(research.get("completed", [])))},
                   "production": lines, "divisions": [{"id": object_id(unit["id"]), **{key: unit.get(key) for key in ("location", "organisation", "strength", "army_current_supply_ratio", "path", "move_priority", "execute_order", "last_combat_date") if key in unit}}
                                                         for unit in many(country.get("units", {}).get("division"))],
                   "owned_state_count": sum(item.get("owner") == tag for item in state.get("states", {}).values())}
        from army_actions import own_active_battles, own_wars
        outcome["active_battles"] = own_active_battles(state, tag)
        outcome["combat_observation_available"] = "combat" in state
        outcome["own_wars"] = own_wars(country, tag)
        add("outcome", outcome)
    add("clock", {"status": result["status"], "elapsed_game_hours": result.get("elapsed_game_hours"),
                  "final_date": final.get("parsed_date")})
    return {"country": tag, "turn_id": result["turn_id"], "match_id": result["match_id"], "evidence": evidence}


def validate_lessons(value, context):
    if not isinstance(value, dict) or set(value) != {"country", "turn_id", "lessons"}:
        raise ValueError("Lesson record has an unexpected schema")
    if value["country"] != context["country"] or type(value["turn_id"]) is not int or value["turn_id"] != context["turn_id"]:
        raise ValueError("Lesson identity differs from the own-country turn")
    lessons = value["lessons"]
    if not isinstance(lessons, list) or len(lessons) > 3:
        raise ValueError("At most three lessons may be retained")
    evidence = {row["id"] for row in context["evidence"]}
    for lesson in lessons:
        if not isinstance(lesson, dict) or set(lesson) != {"claim", "evidence_ids", "when_to_apply", "confidence", "category"}:
            raise ValueError("Lesson fields differ from the knowledge contract")
        if lesson["category"] not in CATEGORIES:
            raise ValueError("Unknown lesson category")
        if any(not isinstance(lesson[key], str) or not lesson[key].strip() or len(lesson[key]) > 800 for key in ("claim", "when_to_apply")):
            raise ValueError("Lessons require a bounded claim and application condition")
        confidence = lesson["confidence"]
        if type(confidence) not in (float, int) or not 0 <= confidence <= 1:
            raise ValueError("Lesson confidence must be a number between zero and one")
        ids = lesson["evidence_ids"]
        if not isinstance(ids, list) or not ids or any(not isinstance(identity, str) or identity not in evidence for identity in ids):
            raise ValueError("A lesson cites unknown or invisible evidence")
    return value


def _generate_country(trial, turn, tag, config):
    context = country_evidence(trial, turn, tag)
    instructions = ("你是本国赛后复盘员，只能使用给定的本国观察、事前摘要、原命令、执行核验、本国结果、公开讨论和本人参与的私信。"
                    "没有别国秘密或观众解说。总结最多3条有用经验，也可为空。不要从结果倒推出自己或他国事前必有某种阴谋。"
                    "区分观察缺失、执行器故障、策略错误和外交变化；命令请求不等于实际获得资源。"
                    "经验必须给出适用条件和0到1置信度，避免把单局现象写成普遍规律。"
                    "每条claim至多80字，when_to_apply至多60字；总输出简短，适配1800 token上限。"
                    "每条必须引用给定evidence id；引用存在仍需谨慎推断。"
                    "只输出{country,turn_id,lessons:[{claim,evidence_ids,when_to_apply,confidence,category}]}，"
                    "category只能是observation/execution/strategy/diplomacy。")
    messages = [{"role": "system", "content": instructions}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    settings = {key: config[key] for key in ("base_url", "model", "api_key_env", "temperature", "max_tokens", "enable_thinking",
                                            "include_thinking_parameter", "json_mode", "request_parameters") if key in config}
    directory = Path(trial) / "private_lessons"
    path = directory / "model_calls" / (tag + ".json")
    if path.exists():
        record = read(path)
        if record.get("messages") != messages or record.get("settings") != settings:
            raise ValueError("Cached private lesson belongs to different evidence or model settings")
    else:
        value, metadata = complete(config, messages)
        record = {"audience": "country_private", "country": tag, "messages": messages,
                  "settings": settings, "response": value, "metadata": metadata}
        atomic_json(path, record)
    value = validate_lessons(record["response"], context)
    atomic_json(directory / "evidence" / (tag + ".json"), context)
    atomic_json(directory / (tag + ".json"), value)
    return value


def generate_lessons(trial, turn, config, configs_by_country=None):
    """Generate isolated country lessons; successful per-country calls survive other failures."""
    trial, turn = Path(trial), Path(turn)
    tags = read(turn / "result.json")["countries"]
    output, failures = {}, {}
    with ThreadPoolExecutor(max_workers=len(tags)) as pool:
        futures = {pool.submit(_generate_country, trial, turn, tag, (configs_by_country or {}).get(tag, config)): tag for tag in tags}
        for future in as_completed(futures):
            tag = futures[future]
            try:
                output[tag] = future.result()
            except Exception as error:
                failures[tag] = str(error)
    if failures:
        atomic_json(trial / "private_lessons/failures.json", {"failures": failures, "completed": sorted(output)})
        raise RuntimeError("Private lesson generation failed for: " + ", ".join(sorted(failures)))
    failure_path = trial / "private_lessons/failures.json"
    if failure_path.exists():
        failure_path.unlink()
    return {tag: output[tag] for tag in tags}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--turn", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    args = parser.parse_args()
    result = generate_lessons(args.trial, args.turn, read(args.model_config))
    print(json.dumps({"countries": list(result), "lessons": {tag: len(value["lessons"]) for tag, value in result.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
