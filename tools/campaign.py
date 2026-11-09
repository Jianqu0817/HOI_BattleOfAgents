"""Shared campaign configuration, model routing and episode indexing."""
import copy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re

from m0_probe import ROOT, countries_from, write_json
from scoreboard import validate_rules, rules_text


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def name_ok(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,60}", value):
        raise ValueError("名称限 1–60 个英文字母、数字或下划线")
    return value


def resolve(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def load_config(path):
    config = read(resolve(path))
    if "base_config" in config:
        config = {**read(resolve(config.pop("base_config"))), **config}
    tags = countries_from(config)
    for key, low, high in (("step_hours", 1, 360), ("turn_limit", 1, 100), ("action_limit", 1, 6), ("communication_rounds", 1, 3)):
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise ValueError(f"{key} 必须在 {low}–{high} 之间")
    config["model"] = read(resolve(config["model_config"]))
    show = config.setdefault("show", {})
    show.update(enabled=True, step_hours=config["step_hours"], action_limit=config["action_limit"])
    for key in ("common_rules", "guide"):
        if show.get(key + "_file"):
            show[key] = resolve(show[key + "_file"]).read_text(encoding="utf-8-sig")
    if show.get("host_skills"):
        from host_skills import load_host_readings
        show["host_readings"] = load_host_readings(show["host_skills"])
    if "player_prompt_limits" in show and ("player_prompt_bytes" not in show or not isinstance(show["player_prompt_limits"], dict)):
        raise ValueError("player_prompt_limits requires a model mapping and a default player_prompt_bytes")
    if "player_prompt_bytes" in show:
        if type(show["player_prompt_bytes"]) is not int or show["player_prompt_bytes"] <= 0:
            raise ValueError("player_prompt_bytes must be a positive integer")
        from showrunner import player_prompt_limits
        for model in show.get("player_prompt_limits", {}):
            player_prompt_limits(show, model)
        show["dialogue_guide"] = show.get("guide", "")
    if show.get("knowledge_file"):
        show["guide"] = show.get("guide", "") + "\n\n" + resolve(show["knowledge_file"]).read_text(encoding="utf-8-sig")
    show.setdefault("profiles", {})
    for tag, profile in show["profiles"].items():
        if tag not in tags:
            raise ValueError("人设配置含未参赛国家: " + tag)
        for key in ("persona", "experience"):
            if profile.get(key + "_file"):
                profile[key] = resolve(profile[key + "_file"]).read_text(encoding="utf-8-sig")
    show["models"] = {tag: {**config["model"], **read(resolve(model))} for tag, model in config.get("player_model_configs", {}).items()}
    if not set(show["models"]) <= set(tags):
        raise ValueError("模型配置含未参赛国家")
    show["referee_model"] = {**config["model"], **read(resolve(config["referee_model_config"]))} if config.get("referee_model_config") else config["model"]
    config["commentary_model"] = {**config["model"], **read(resolve(config["commentary_model_config"]))} if config.get("commentary_model_config") else config["model"]
    config["scoring"] = validate_rules(config.get("scoring"))
    rules = rules_text(config["scoring"], config["turn_limit"], config["step_hours"])
    if scenario := config.get("scenario"):
        if (set(scenario["goals"]) != set(tags) or type(scenario["hours"]) is not int or scenario["hours"] <= 0
                or "clock_policy" not in config):
            raise ValueError("A timed scenario requires a private goal per player, positive game hours and a clock policy")
        for tag in tags:
            show["profiles"].setdefault(tag, {})["scenario_goal"] = scenario["goals"][tag]
        rules = f"\n本场展演《{scenario['title']}》持续{scenario['hours']}游戏小时。开场公共消息：{scenario['opening']}"
        show["public_opening"] = scenario["opening"]
        if "public_host_cues" in scenario:
            cues = scenario["public_host_cues"]
            if not isinstance(cues, dict) or any(not re.fullmatch(r"[1-9][0-9]*", turn)
                    or not isinstance(cue, str) or not 0 < len(cue.strip()) <= 600 for turn, cue in cues.items()):
                raise ValueError("public_host_cues requires turn numbers and short public hosting directions")
            show["public_host_cues"] = cues
    if show.get("table_host") and (not show.get("strict_public_context") or config["communication_rounds"] != 2
                                    or not config.get("performance", {}).get("enabled")
                                    or not config["performance"].get("public_host", True)):
        raise ValueError("Table host requires a public performance and two public rounds")
    if "clock_policy" in config:
        rules = rules.replace(f"每回合{config['step_hours']}游戏小时", "每回合使用当轮公布的共同时间预算，重要事件可提前暂停")
    show["common_rules"] = show.get("common_rules", "") + rules
    from production import configure_character_show
    configure_character_show(config)
    return config


def config_digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


SWITCH_FIELDS = ("base_url", "model", "api_key_env", "enable_thinking", "temperature", "max_tokens",
                 "timeout_seconds", "include_thinking_parameter", "json_mode", "api_format", "omit_temperature", "request_parameters")
SWITCH_IDENTITY_FIELDS = ("base_url", "model", "api_key_env", "enable_thinking", "temperature",
                          "timeout_seconds", "include_thinking_parameter", "json_mode", "api_format", "omit_temperature", "request_parameters")


def validate_switch_spec(spec):
    """A recorded provider switch may only change identity and transport fields; never messages or secrets."""
    if not isinstance(spec, dict) or not spec:
        raise ValueError("model switch spec must be a non-empty object")
    unknown = set(spec) - set(SWITCH_FIELDS) - {"country", "target"}
    if unknown:
        raise ValueError("model switch spec may only contain: " + ", ".join(sorted(SWITCH_FIELDS)))
    for key in ("base_url", "model"):
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            raise ValueError("model switch spec requires a non-empty " + key)
    if "api_key_env" in spec and (not isinstance(spec["api_key_env"], str) or not spec["api_key_env"].strip()):
        raise ValueError("model switch api_key_env must be a non-empty string")
    if "country" in spec and (not isinstance(spec["country"], str) or not re.fullmatch(r"[A-Z]{3}", spec["country"])):
        raise ValueError("model switch country must be a three-letter country tag")
    if spec.get("target", "all") not in ("all", "officials") or ("country" in spec and "target" in spec):
        raise ValueError("model switch target must be all or officials, without a country selector")
    return copy.deepcopy(spec)


def apply_model_switch(config, spec):
    """Apply to one selected country or every model slot, preserving the frozen config."""
    spec = validate_switch_spec(spec)
    result = copy.deepcopy(config)
    target = spec.pop("target", "all")
    country = spec.pop("country", None)
    if country is not None:
        if country not in result["countries"]:
            raise ValueError("model switch country is not a participant")
        show = result.setdefault("show", {})
        models = show.setdefault("models", {})
        models[country] = {**result["model"], **models.get(country, {}), **spec}
        for row in show.get("public_cast", []):
            if row.get("country") == country:
                row["execution_model"] = spec["model"]
        return result
    if target == "officials" and not set(result["countries"]) <= set(result.get("show", {}).get("models", {})):
        raise ValueError("officials-only switch requires explicit player model configurations")
    for slot in ("model", "commentary_model"):
        if slot in result:
            result[slot] = {**result[slot], **spec}
    show = result.get("show", {})
    if isinstance(show, dict):
        if isinstance(show.get("referee_model"), dict):
            show["referee_model"] = {**show["referee_model"], **spec}
        if target == "officials":
            if model_slot_fingerprints(config)["player_models"] != model_slot_fingerprints(result)["player_models"]:
                raise ValueError("officials-only switch would alter inherited player parameters")
            return result
        for tag, model in (show.get("models") or {}).items():
            if isinstance(model, dict):
                show["models"][tag] = {**model, **spec}
        for row in show.get("public_cast", []):
            row["execution_model"] = spec["model"]
    return result


def model_slot_fingerprints(config):
    """Non-secret settings fingerprints of every model slot, as cached call records store them."""
    from showrunner import model_settings, country_model, referee_model
    values = {"player_models": {tag: model_settings(country_model(config["model"], config.get("show", {}), tag))
                                for tag in config["countries"]}}
    if config.get("show", {}).get("referee_model"):
        values["referee_model"] = model_settings(referee_model(config["model"], config["show"]))
    if config.get("model_commentary", True) and config.get("commentary_model"):
        values["commentary_model"] = model_settings(config["commentary_model"])
    return values


def switch_identity(fingerprint):
    """max_tokens is legitimately capped per phase and must not decide whether a cached record matches."""
    return {key: value for key, value in fingerprint.items() if key in SWITCH_IDENTITY_FIELDS}


def switch_fingerprint_list(fingerprints):
    """Flatten slot fingerprints (including per-country maps) into comparable identity dicts."""
    rows = []
    for value in fingerprints.values():
        if not isinstance(value, dict):
            continue
        if value and all(isinstance(inner, dict) for inner in value.values()):
            rows.extend(switch_identity(inner) for inner in value.values())
        else:
            rows.append(switch_identity(value))
    return rows


def build_switch_record(config, spec, *, reason, source):
    """Build the auditable ledger entry for a resume-time provider switch; secrets stay out by construction."""
    applied = apply_model_switch(config, spec)
    old, new = model_slot_fingerprints(config), model_slot_fingerprints(applied)
    if not new["player_models"] or switch_fingerprint_list(old) == switch_fingerprint_list(new):
        raise ValueError("model switch must change provider identity or request parameters")
    return {"schema": "iron.model-switch.v1", "spec": spec, "reason": reason, "source": source,
            "from": old, "to": new,
            "from_fingerprints": switch_fingerprint_list(old), "to_fingerprints": switch_fingerprint_list(new)}


def effective_resumed_config(manifest, spec=None):
    """The model configuration a resumed run must use: the CLI spec if given, else recorded switches, else frozen config."""
    config = manifest["config"]
    if spec is not None:
        return apply_model_switch(config, spec)
    for record in manifest.get("model_switches", []):
        config = apply_model_switch(config, record["spec"])
    return config


def identity(session, pid):
    return asdict(session.platform.capture_process_identity(pid))


def key_envs(config):
    models = [config["model"], {**config["model"], **config["show"]["referee_model"]},
              *({**config["model"], **model} for model in config["show"]["models"].values())]
    if config.get("model_commentary", True):
        models.append(config["commentary_model"])
    return sorted({model["api_key_env"] for model in models})


def load_model_environment(config):
    from model_client import api_key_from_environment
    for name in key_envs(config):
        value = api_key_from_environment(name)
        if value and not os.environ.get(name):
            os.environ[name] = value


def execution_mode(result):
    if result.get("status") == "completed_deterministic_turn":
        return "done"
    if result.get("final_save"):
        raise ValueError("已有最终存档但回合未提交完成，请核查日志")
    if "before_clock" in result:
        return "clock"
    return "actions"


def turn_show_config(config, directory):
    show = copy.deepcopy(config["show"])
    for tag in config["countries"]:
        path = directory / "knowledge" / (tag + ".json")
        if path.exists():
            records = read(path)
            if any(row.get("country") != tag for row in records):
                raise ValueError("经验库混入他国私密记录: " + tag)
            profile = show["profiles"].setdefault(tag, {})
            profile["experience"] = profile.get("experience", "") + "\n以下为本国过往实战复盘，结论有适用条件，不是确定事实：\n" + json.dumps(records[-4:], ensure_ascii=False)
    from showrunner import visible_promises
    show["commitment_memory"] = {}
    for tag in config["countries"]:
        path = directory / "diplomacy" / (tag + ".json")
        if path.exists():
            entries = read(path)
            if len(visible_promises(entries, tag)) != len(entries):
                raise ValueError("承诺记忆混入未参与的私信: " + tag)
            if show.get("player_prompt_bytes"):
                show["commitment_memory"][tag] = [row for row in entries[:-30] if row.get("status") == "not_due"] + entries[-30:]
            else:
                show["commitment_memory"][tag] = entries[-30:]
    return show


def remember_commitments(config, trial, directory):
    from showrunner import visible_promises
    path = trial / "commitments.json"
    if not path.exists():
        return
    entries = [{**item, "source_trial": trial.name} for item in read(path)["entries"]]
    for tag in config["countries"]:
        destination = directory / "diplomacy" / (tag + ".json")
        old = read(destination) if destination.exists() else []
        merged = {(item["source_trial"], item["id"]): item for item in old}
        merged.update({(item["source_trial"], item["id"]): item for item in visible_promises(entries, tag)})
        ordered = list(merged.values())
        active = ([row for row in ordered[:-100] if row.get("status") == "not_due"]
                  if config.get("show", {}).get("player_prompt_bytes") else [])
        write_json(destination, active + ordered[-100:])


def model_usage(trial):
    paths = list((trial / "model_calls").glob("*.json")) + list((trial / "private_lessons/model_calls").glob("*.json"))
    commentary = trial / "audience_commentary/model_call.json"
    if commentary.exists():
        paths.append(commentary)
    totals = {"calls": len(paths), "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls_without_usage": 0}
    for path in paths:
        usage = read(path).get("metadata", {}).get("usage")
        if not isinstance(usage, dict) or type(usage.get("total_tokens")) is not int:
            totals["calls_without_usage"] += 1
            continue
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            totals[key] += usage.get(key, 0) if type(usage.get(key, 0)) is int else 0
    return totals


def build_index(directory, manifest):
    import html
    links = []
    for entry in manifest["turns"]:
        if entry.get("status") == "completed":
            target = f"episodes/turn_{entry['index']:03d}/index.html"
            links.append(f'<li><a href="{target}">回合 {entry["index"]} · {html.escape(entry["final_date"])}</a></li>')
    standings = manifest.get("standings", {})
    table = ''.join('<tr>' + ''.join('<td>' + html.escape(str(row.get(key) if row.get(key) is not None else '未知／不计分')) + '</td>' for key in ('country', 'controlled_land', 'land_delta', 'points')) + '</tr>' for row in standings.get('rows', []))
    (directory / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>IRON 对局回看</title><style>body{background:#111827;color:#eee;font:20px system-ui;margin:6vw}a{color:#8ddaff}li{margin:1em}td,th{padding:8px;border-bottom:1px solid #374151}</style><h1>IRON · ' + html.escape(manifest["name"]) + '</h1><p>观众全知视角；含各国私密计划与定向私信。</p><ul>' + ''.join(links) + '</ul><p>' + html.escape(standings.get('rule', '')) + '</p><table><tr><th>国家</th><th>控制陆地省份</th><th>增减</th><th>分数</th></tr>' + table + '</table><p>' + html.escape('获胜：' + '、'.join(standings['winners']) if standings.get('winners') else '') + '</p>', encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit("旧桌面对局入口已移除。请使用 tools/native_campaign.py。")
