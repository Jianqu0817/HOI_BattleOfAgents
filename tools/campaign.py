"""IRON: configure, check, run/resume a bounded match, and export episodes."""
import argparse
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from m0_probe import ROOT, Probe, ai_receipt_state, countries_from, settled_pause_state, write_json
from scoreboard import validate_rules, rules_text, score, controllers


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
    if "clock_policy" in config:
        rules = rules.replace(f"每回合{config['step_hours']}游戏小时", "每回合使用当轮公布的共同时间预算，重要事件可提前暂停")
    show["common_rules"] = show.get("common_rules", "") + rules
    from production import configure_character_show
    configure_character_show(config)
    return config


def config_digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


SWITCH_FIELDS = ("base_url", "model", "api_key_env", "enable_thinking", "temperature", "max_tokens",
                 "timeout_seconds", "include_thinking_parameter", "json_mode", "request_parameters")
SWITCH_IDENTITY_FIELDS = ("base_url", "model", "api_key_env", "enable_thinking", "temperature",
                          "timeout_seconds", "include_thinking_parameter", "json_mode", "request_parameters")


def validate_switch_spec(spec):
    """A recorded provider switch may only change identity and transport fields; never messages or secrets."""
    if not isinstance(spec, dict) or not spec:
        raise ValueError("model switch spec must be a non-empty object")
    unknown = set(spec) - set(SWITCH_FIELDS)
    if unknown:
        raise ValueError("model switch spec may only contain: " + ", ".join(sorted(SWITCH_FIELDS)))
    for key in ("base_url", "model"):
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            raise ValueError("model switch spec requires a non-empty " + key)
    if "api_key_env" in spec and (not isinstance(spec["api_key_env"], str) or not spec["api_key_env"].strip()):
        raise ValueError("model switch api_key_env must be a non-empty string")
    return copy.deepcopy(spec)


def apply_model_switch(config, spec):
    """Return a new config with the switch applied to every model slot; the frozen manifest config is not mutated."""
    spec = validate_switch_spec(spec)
    result = copy.deepcopy(config)
    for slot in ("model", "commentary_model"):
        if slot in result:
            result[slot] = {**result[slot], **spec}
    show = result.get("show", {})
    if isinstance(show, dict):
        if isinstance(show.get("referee_model"), dict):
            show["referee_model"] = {**show["referee_model"], **spec}
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
    old_players = list(old["player_models"].values())
    if not new["player_models"] or all(switch_identity(value) == switch_identity(old_players[0])
                                       for value in new["player_models"].values()):
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


def acquire_campaign_lock(directory, session):
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "campaign.lock.json").open("a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        owner = identity(session, os.getpid())
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(owner).encode("utf-8"))
        handle.flush()
        return handle
    except OSError as error:
        handle.close()
        raise ValueError("该对局已有活动进程，请勿同时运行") from error
    except Exception:
        handle.close()
        raise


def release_campaign_lock(handle):
    # OS locks are released on close and on process death; metadata need not be deleted.
    handle.close()


def doctor(config, directory):
    probe = Probe(directory, config["countries"])
    try:
        status = probe.call("hoi4_status", {})
        pids = status["game"]["pids"]
        pause, evidence = settled_pause_state(probe.session) if len(pids) == 1 else ("unavailable", {})
        result = {"game_running": len(pids) == 1, "pids": pids, "pause": pause,
                  "pause_evidence": evidence, "model_keys": {key: bool(os.environ.get(key)) for key in key_envs(config)},
                  "version": status["game"].get("version"), "window": status.get("window"),
                  "note": "中文 1280×720 渲染配置；运行前载入单人非铁人存档。模型密钥只检查是否已设置。"}
        write_json(directory / "doctor.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        probe.session.shutdown()


def establish_control(config, directory, known_disabled_pid=None):
    """Normalize toggles while paused; verify every response, never infer toggle state."""
    tags = config["countries"]
    probe = Probe(directory / "control", tags)
    try:
        with probe.session.lock.hold("IRON campaign control setup"):
            status = probe.call("hoi4_status", {})
            pids = status["game"]["pids"]
            if len(pids) != 1 or settled_pause_state(probe.session)[0] != "paused":
                raise ValueError("需要唯一游戏进程和已暂停的存档")
            current = identity(probe.session, pids[0])
            path = directory / "control.json"
            existing = read(path) if path.exists() else None
            if existing and existing.get("identity") == current and existing.get("disabled_ai") == tags and existing.get("status") == "verified":
                return pids[0]
            if existing and existing.get("identity") != current:
                raise ValueError("游戏进程已变。请载入正确检查点后运行 control --refresh，重新建立本轮控制。")
            record = {"identity": current, "disabled_ai": [], "status": "checking", "toggles": []}
            write_json(path, record)
            if known_disabled_pid is not None:
                if known_disabled_pid != pids[0]:
                    raise ValueError("已知 AI 停用证据不属于当前进程")
                record["disabled_ai"] = tags
                record["basis"] = "operator supplied same-process disabled-AI evidence"
            else:
                for tag in tags:
                    # `ai TAG` is a toggle. Its response proves the resulting state.
                    for attempt in range(2):
                        record["pending"] = tag
                        write_json(path, record)
                        response = probe.command(["ai " + tag])
                        text = " ".join(response["results"][0].get("response", []))
                        record["toggles"].append({"country": tag, "response": text})
                        write_json(path, record)
                        resulting_state = ai_receipt_state(response["results"][0].get("response", []), tag)
                        if resulting_state == "disabled":
                            record["disabled_ai"].append(tag)
                            break
                        if resulting_state != "enabled":
                            raise ValueError("无法确认 AI 状态，保持暂停: " + tag)
                    else:
                        raise ValueError("无法停用原生国家 AI: " + tag)
            record.pop("pending", None)
            record["receipt"] = probe.receipts(tags + [tags[0]], "campaign_control")
            record["status"] = "verified"
            write_json(path, record)
            from hoi4_live.game import console, panels
            console.close_console(probe.session)
            panels.to_map(probe.session)
            return pids[0]
    finally:
        probe.session.shutdown()


def execution_mode(result):
    if result.get("status") == "completed_deterministic_turn":
        return "done"
    if result.get("final_save"):
        raise ValueError("已有最终存档但回合未提交完成，请核查日志")
    if "before_clock" in result:
        return "clock"
    return "actions"


def run_child(arguments):
    result = subprocess.run([sys.executable, *map(str, arguments)], cwd=ROOT)
    if result.returncode:
        raise RuntimeError("子阶段停止，保持原记录供 resume: " + str(arguments[0]))


def production_marker(directory, turn, phase):
    with (directory / "production.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"turn": turn, "phase": phase, "epoch": time.time(),
                                 "recorded_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")


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
        write_json(destination, list(merged.values())[-100:])


def update_standings(config, manifest, directory):
    if "scoring" not in config:
        return
    completed = [entry for entry in manifest["turns"] if entry.get("status") == "completed"]
    if not completed:
        return
    from run_turn import saved_state
    from army_actions import context_for_session
    first_dir, last_dir = resolve(completed[0]["turn"]), resolve(completed[-1]["turn"])
    first, last = read(first_dir / "result.json"), read(last_dir / "result.json")
    probe = Probe(directory / "score_data", config["countries"])
    try:
        context = context_for_session(probe.session)
        standings = score(saved_state(first_dir, first["before_save"]), saved_state(last_dir, last["final_save"]),
                          config["countries"], context["map"], config["scoring"], manifest["status"] == "completed")
        manifest["standings"] = standings
        write_json(directory / "standings.json", standings)
    finally:
        probe.session.shutdown()


def player_score_context(config, trial, first_trial, directory):
    from army_actions import context_for_session
    tag = config["countries"][0]
    current_source = read(trial / "observations" / (tag + ".json"))["source"]["path"]
    initial_source = read(first_trial / "observations" / (tag + ".json"))["source"]["path"]
    current_state, initial_state = read(current_source), read(initial_source)
    probe = Probe(directory / "score_context", config["countries"])
    try:
        index = context_for_session(probe.session)["map"]
        current, initial = controllers(current_state, index), controllers(initial_state, index)
        objectives = [{"province_id": point, "controller": current[point]} for point in config["scoring"].get("province_ids", [])]
        result = {}
        for tag in config["countries"]:
            start, now = sum(owner == tag for owner in initial.values()), sum(owner == tag for owner in current.values())
            result[tag] = {"mode": config["scoring"]["mode"], "initial_controlled_land": start,
                           "current_controlled_land": now, "land_delta": now - start,
                           "public_objectives": objectives, "campaign_round_limit": config["turn_limit"]}
        return result
    finally:
        probe.session.shutdown()


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


def finish_execution(config, entry, pid):
    trial, turn = resolve(entry["trial"]), resolve(entry["turn"])
    path = turn / "result.json"
    mode = execution_mode(read(path)) if path.exists() else "new"
    if mode == "done":
        return
    if path.exists():
        result = read(path)
        if result["status"] in ("executing", "advancing", "starting", "resuming"):
            # The campaign owns a process lock; an interrupted child cannot still run.
            result.update(status="interrupted", error="process ended before phase commit")
            write_json(path, result)
    match_config = turn.parent / (turn.name + "_config.json")
    write_json(match_config, {"countries": config["countries"], "step_hours": config["step_hours"]})
    if mode == "clock":
        run_child([ROOT / "tools/recover_clock.py", "--name", turn.name, "--config", match_config,
                   "--known-disabled-pid", pid])
    else:
        args = [ROOT / "tools/run_turn.py", "--observations", trial / "observations", "--requests", trial / "requests",
                "--name", turn.name, "--config", match_config, "--known-disabled-pid", pid,
                "--decision-source", "IRON campaign sealed model requests"]
        if mode == "actions":
            args.append("--resume")
        run_child(args)


def run_campaign(name, config_path=None, resume=False, turns=None, previous_trial=None, previous_turn=None,
                 known_disabled_pid=None):
    from model_trial import prepare, decide
    from episode import export_episode, generate_commentary
    directory = ROOT / "runs/campaigns" / name_ok(name)
    path = directory / "manifest.json"
    if resume:
        manifest = read(path)
        config = manifest["config"]
        if config_path and config_digest(load_config(config_path)) != manifest["config_sha256"]:
            raise ValueError("恢复配置与已冻结配置不同，请为新配置新开对局")
    else:
        if path.exists():
            raise ValueError("对局名称已存在，请使用 resume 或新名称")
        config = load_config(config_path or "configs/campaign.example.json")
        if bool(previous_trial) != bool(previous_turn):
            raise ValueError("续接需要同时提供 from-trial 与 from-turn")
        for value in (previous_trial, previous_turn):
            if value:
                name_ok(value)
        manifest = {"schema_version": 1, "name": name, "status": "new", "config": config,
                    "config_sha256": config_digest(config), "turns": [], "created_at": time.time(),
                    "previous_trial": previous_trial, "previous_turn": previous_turn}
    # Lock the campaign across model calls too; the backend desktop lock covers input phases.
    probe = Probe(directory / "session", config["countries"])
    try:
        campaign_lock = acquire_campaign_lock(directory, probe.session)
    finally:
        probe.session.shutdown()
    owns_manifest = resume
    try:
        if not resume:
            if path.exists():
                raise ValueError("对局已由另一进程建立，请使用 resume 或新名称")
            write_json(path, manifest)
            owns_manifest = True
        else:
            manifest = read(path)
            config = manifest["config"]
        missing = [key for key in key_envs(config) if not os.environ.get(key)]
        if missing:
            raise ValueError("请设置模型密钥环境变量: " + ", ".join(missing))
        pid = establish_control(config, directory, known_disabled_pid)
        manifest["known_disabled_pid"] = pid
        if config.get("scoring", {}).get("mode", "exhibition") != "exhibition":
            from army_actions import context_for_session
            probe = Probe(directory / "score_preflight", config["countries"])
            try:
                index = context_for_session(probe.session)["map"]
                if not index.get("provinces"):
                    raise ValueError("该地图尚未校准，不能使用领土/目标点计分")
                for point in config["scoring"].get("province_ids", []):
                    if index["provinces"].get(str(point), {}).get("kind") != "land":
                        raise ValueError("目标不是当前地图的陆地省份: " + str(point))
            finally:
                probe.session.shutdown()
        limit = min(config["turn_limit"], len([row for row in manifest["turns"] if row.get("status") == "completed"]) + turns) if turns else config["turn_limit"]
        if turns is not None and (type(turns) is not int or turns < 1):
            raise ValueError("turns 必须为正整数")
        for index in range(limit):
            if index < len(manifest["turns"]):
                entry = manifest["turns"][index]
            else:
                trial_name = f"{name}_t{index + 1:03d}"
                entry = {"index": index + 1, "trial": "runs/" + trial_name,
                         "turn": "runs/turns/" + trial_name + "_exec", "status": "pending"}
                manifest["turns"].append(entry)
            if entry.get("status") == "completed":
                continue
            trial, turn = resolve(entry["trial"]), resolve(entry["turn"])
            if index:
                prior_trial, prior_turn = [resolve(manifest["turns"][index - 1][key]) for key in ("trial", "turn")]
            else:
                prior_trial = ROOT / "runs" / manifest["previous_trial"] if manifest["previous_trial"] else None
                prior_turn = ROOT / "runs/turns" / manifest["previous_turn"] if manifest["previous_turn"] else None
            manifest.update(status="running", current_turn=index + 1)
            entry["status"] = "observing"
            write_json(path, manifest)
            if not (trial / "prepared.json").exists():
                production_marker(directory, index + 1, "observe.start")
                prepare(trial, config["countries"], pid, prior_trial, prior_turn, resume=(trial / "capture").exists())
                production_marker(directory, index + 1, "observe.end")
            entry["status"] = "deciding"
            write_json(path, manifest)
            decision = read(trial / "decision_result.json") if (trial / "decision_result.json").exists() else {}
            if decision.get("status") != "approved":
                production_marker(directory, index + 1, "decide.start")
                show = turn_show_config(config, directory)
                if config.get("scoring", {}).get("mode", "exhibition") != "exhibition":
                    show["score_context"] = player_score_context(config, trial, resolve(manifest["turns"][0]["trial"]), directory)
                decide(trial, config["model"], config["countries"], resume=bool(decision),
                       communication_rounds=config["communication_rounds"], private_diplomacy=True,
                       show_config=show)
                production_marker(directory, index + 1, "decide.end")
            remember_commitments(config, trial, directory)
            entry["status"] = "executing"
            write_json(path, manifest)
            production_marker(directory, index + 1, "execute.start")
            finish_execution(config, entry, pid)
            production_marker(directory, index + 1, "execute.end")
            entry["status"] = "exporting"
            write_json(path, manifest)
            if config.get("summarize_lessons", True):
                from lessons import generate_lessons
                try:
                    player_models = {tag: {**config["model"], **config["show"]["models"].get(tag, {})} for tag in config["countries"]}
                    lessons = generate_lessons(trial, turn, config["model"], configs_by_country=player_models)
                    for tag, record in lessons.items():
                        knowledge_path = directory / "knowledge" / (tag + ".json")
                        knowledge = read(knowledge_path) if knowledge_path.exists() else []
                        knowledge = [row for row in knowledge if row.get("turn_id") != record["turn_id"]]
                        write_json(knowledge_path, (knowledge + [record])[-20:])
                    entry.pop("lessons_error", None)
                except (ValueError, RuntimeError) as error:
                    entry["lessons_error"] = str(error)
                    print("本轮游戏已完成，复盘暂未完成：" + str(error), flush=True)
            commentary = None
            if config.get("model_commentary", True):
                try:
                    commentary = generate_commentary(trial, turn, config["commentary_model"])
                    entry.pop("commentary_error", None)
                except (ValueError, RuntimeError) as error:
                    entry["commentary_error"] = str(error)
                    print("模型解说暂未通过校验，继续导出事实记录：" + str(error), flush=True)
            exported = export_episode(trial, turn, directory / "episodes" / f"turn_{index + 1:03d}", commentary,
                                      load_cached_commentary=commentary is not None,
                                      production_markers=[row for row in (json.loads(line) for line in (directory / "production.jsonl").read_text(encoding="utf-8").splitlines()) if row["turn"] == index + 1])
            production_marker(directory, index + 1, "export.end")
            entry.update(status="completed", final_date=read(turn / "result.json")["final_save"]["parsed_date"],
                         export=exported)
            write_json(path, manifest)
            build_index(directory, manifest)
            print(f"回合 {index + 1} 完成：{entry['final_date']}", flush=True)
        manifest["status"] = "completed" if len(manifest["turns"]) == config["turn_limit"] and all(row["status"] == "completed" for row in manifest["turns"]) else "ready"
        manifest.pop("error", None)
        update_standings(config, manifest, directory)
        write_json(path, manifest)
        build_index(directory, manifest)
        return manifest
    except Exception as error:
        if owns_manifest:
            manifest.update(status="interrupted", error=str(error))
            write_json(path, manifest)
        raise
    finally:
        try:
            if owns_manifest:
                for entry in manifest["turns"]:
                    entry["usage"] = model_usage(resolve(entry["trial"]))
                manifest["usage"] = {key: sum(entry["usage"][key] for entry in manifest["turns"]) for key in ("calls", "prompt_tokens", "completion_tokens", "total_tokens", "calls_without_usage")}
                write_json(path, manifest)
        finally:
            release_campaign_lock(campaign_lock)


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


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["doctor", "control", "run", "resume", "status"])
    parser.add_argument("--config")
    parser.add_argument("--name", default="my_match")
    parser.add_argument("--turns", type=int, help="本次最多新增的回合数")
    parser.add_argument("--from-trial")
    parser.add_argument("--from-turn")
    parser.add_argument("--known-disabled-pid", type=int)
    parser.add_argument("--refresh", action="store_true", help="control：新进程重新建立原生AI控制")
    args = parser.parse_args()
    name_ok(args.name)
    directory = ROOT / "runs/campaigns" / args.name
    if args.command == "status":
        data = read(directory / "manifest.json")
        print(json.dumps({key: data.get(key) for key in ("name", "status", "current_turn", "error", "turns")}, ensure_ascii=False, indent=2))
    elif args.command in ("run", "resume"):
        run_campaign(args.name, args.config, args.command == "resume", args.turns, args.from_trial, args.from_turn, args.known_disabled_pid)
    else:
        config = load_config(args.config or "configs/campaign.example.json")
        if args.command == "doctor":
            doctor(config, directory / "checks")
        else:
            if args.refresh:
                (directory / "control.json").unlink(missing_ok=True)
            print("已控制进程", establish_control(config, directory, args.known_disabled_pid))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        print("IRON 停止：" + str(error), file=sys.stderr)
        raise SystemExit(1)
