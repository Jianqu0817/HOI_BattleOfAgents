"""Run existing isolated diplomacy and sealed decisions through the native game API."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import time

from campaign import (ROOT, apply_model_switch, build_index, build_switch_record, config_digest,
                      effective_resumed_config, key_envs, load_config, model_slot_fingerprints, model_usage,
                      name_ok, remember_commitments, switch_fingerprint_list, turn_show_config,
                      validate_switch_spec)
from episode import export_episode, generate_commentary
from m0_probe import write_json
from model_client import complete
from model_trial import decide, digest, own_history
from native_capture import capture, static_context, read_world
from native_game import NativeGame
from native_observation import build_observation, to_native_plan
from native_turn import NativeTurn
from native_pacing import select_step_hours
from story_channels import build_public_events, compress_own_memory, settle_commitments
from performance import post_seal


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def now():
    return datetime.now(timezone.utc).isoformat()


def load_capture(path, context):
    return {"snapshots": read(path / "snapshots.json"), "state": read(path / "world.json"),
            "manifest": read(path / "capture.json"), "map_context": context}


def compact_history(trial, turn, tags):
    """Reuse the existing privacy validator; persist only the two recent own turns."""
    histories = {tag: own_history(trial, turn, tag) for tag in tags}
    for rows in histories.values():
        if rows:
            rows[-1]["public_events"] = turn.get("public_events", rows[-1].get("public_events", []))
    return histories


def require_no_pending_choice(world):
    if world.get("pending_events", {}).get("pending") or world.get("pending_events", {}).get("resolved_windows"):
        raise RuntimeError("registered pending event needs a qualified normal choice; stop before model calls and game orders")


def apply_campaign_switch(manifest, spec, *, reason, source):
    """Record or reuse an auditable resume-time provider switch; returns the effective config and the active record."""
    frozen = manifest["config"]
    recorded = manifest.setdefault("model_switches", [])
    config = frozen
    for previous in recorded:
        config = apply_model_switch(config, previous["spec"])
    spec = validate_switch_spec(spec)
    if recorded:
        last = recorded[-1]
        if last.get("spec") == spec and switch_fingerprint_list(model_slot_fingerprints(config)) == last.get("to_fingerprints"):
            return config, last
    base = recorded[-1] if recorded else None
    reference = apply_model_switch(frozen, base["spec"]) if base else frozen
    candidate = build_switch_record(reference, spec, reason=reason, source=source)
    if base is not None and candidate["from"] != base["to"]:
        raise ValueError("recorded model switches do not chain from the frozen configuration")
    pending = [entry["index"] for entry in manifest["turns"] if entry.get("status") != "completed"]
    candidate["first_affected_turn"] = min(pending) if pending else frozen["turn_limit"] + 1
    candidate["applied_at"] = now()
    recorded.append(candidate)
    return apply_model_switch(config, spec), candidate


def adapt_result(native, execution, trial, observed, after):
    """Preserve sealed requests verbatim while attaching native effect evidence."""
    requests = {tag: read(trial / "requests" / (tag + ".json")) for tag in observed}
    facts = [json.loads(line) for line in (execution / "audience_facts.jsonl").read_text(encoding="utf-8").splitlines()]
    effects = {(row["country"], row["action"]["id"]): row for row in facts if row["event"] == "action_effect"}
    actions = []
    for tag, request in requests.items():
        for action in request["actions"]:
            effect = effects[(tag, action["id"])]
            verified = effect["effect_verified"]
            pending = action["type"] == "division.move" and effect["observed"]["province"] != action["args"]["target"]
            actions.append({"country": tag, "action": action, "status": "applied" if verified else "unknown",
                            "effect_status": "ordered" if verified and pending else "full" if verified else "unknown",
                            "ended_at": effect["utc"],
                            "verification": {"verified": verified, "checks": {"native_effect_observed": verified},
                                             "observations": effect["observed"]}})
    first = next(iter(observed.values()))
    final_date = after["snapshots"][first["country"]]["date"]
    return {"execution_backend": "native", "status": "completed_native_turn", "countries": list(observed),
            "match_id": first["match_id"], "turn_id": first["turn_id"], "requests": requests,
            "before_save": {"parsed_date": first["date"]},
            "final_save": {"name": native["save"]["basename"], "path": native["save"].get("path"),
                           "parsed_date": final_date, "sha256": native["save"].get("sha256"),
                           "file_verified": native["save"]["file_verified"]},
            "elapsed_game_hours": native["step_hours"], "actions": actions,
            "native_execution": str(execution), "native_result": native,
            "native_after": after["observations"]}


def verify_decisions(trial, observed):
    result = read(trial / "decision_result.json")
    requests = {tag: read(trial / "requests" / (tag + ".json")) for tag in observed}
    reviews = {row["country"]: row for row in result["referee"]["reviews"]}
    if result["status"] != "approved" or set(reviews) != set(observed):
        raise ValueError("all countries need their sealed referee approval")
    for tag, request in requests.items():
        original = read(trial / "private_decisions" / (tag + ".json"))
        if request != original or digest(request) != reviews[tag]["request_sha256"] or not reviews[tag]["approved"]:
            raise ValueError("sealed decision changed after the referee reviewed it")
    return requests


def run_campaign(name, config_path, pid, save_dir, *, resume=False, through_turn=None,
                 provider_check=False, switch_model=None, switch_reason=None):
    directory = ROOT / "runs" / "campaigns" / name_ok(name)
    manifest_path = directory / "manifest.json"
    if resume:
        manifest = read(manifest_path)
        if switch_model is not None:
            if isinstance(switch_model, dict):
                spec = switch_model
            else:
                path = Path(switch_model)
                spec = read(path if path.is_absolute() else ROOT / path)
            config, _ = apply_campaign_switch(manifest, spec,
                                              reason=switch_reason or "resume provider switch",
                                              source="resume command line")
            write_json(manifest_path, manifest)
        else:
            config = effective_resumed_config(manifest)
    else:
        if manifest_path.exists():
            raise ValueError("campaign exists; use --resume to reuse completed model calls")
        if switch_model is not None:
            raise ValueError("--switch-model applies to --resume; set the provider in the model config for a new campaign")
        config = load_config(config_path)
        if config.get("execution_backend") != "native":
            raise ValueError("native campaign requires execution_backend=native")
        config.setdefault("audience_timing", "postmatch")
        manifest = {"schema": "iron.native-campaign.v1", "name": name, "config": config,
                    "config_sha256": config_digest(config), "started_at": now(),
                    "status": "starting", "turns": [], "usage": {}, "timings": {}}
        write_json(manifest_path, manifest)
    if config.get("audience_timing", "inline") not in ("inline", "postmatch"):
        raise ValueError("audience_timing must be inline or postmatch")
    limit = config["turn_limit"] if through_turn is None else through_turn
    if not 1 <= limit <= config["turn_limit"]:
        raise ValueError("requested turn count exceeds the frozen campaign")
    game = NativeGame(pid, directory / "native_requests")
    identity = {"pid": pid, "creation_time": game.creation_time}
    if manifest.get("game_identity", identity) != identity:
        raise ValueError("game process changed; original pending orders require reconciliation")
    manifest["game_identity"] = identity
    tags = config["countries"]
    for key in key_envs(config):
        if not os.environ.get(key):
            raise ValueError("model API key environment variable is missing: " + key)
    if provider_check and not (directory / "provider_check.json").exists():
        value, metadata = complete({**config["model"], "max_tokens": 64},
                                   [{"role": "user", "content": 'Reply only with JSON {"ready":true}.'}])
        if value != {"ready": True}:
            raise ValueError("provider readiness response was invalid")
        write_json(directory / "provider_check.json", {"value": value, "metadata": metadata})
    context = static_context()
    initial = directory / "initial_capture"
    started = time.monotonic()
    if not (initial / "capture.json").exists():
        baseline = capture(game, tags, initial, name, 1, map_context=context)
        manifest["initial_date_raw"] = baseline["manifest"]["date_raw"]
        manifest["initial_date"] = baseline["snapshots"][tags[0]]["date"]
        write_json(manifest_path, manifest)
    else:
        baseline = load_capture(initial, context)
    manifest["initial_date_raw"] = baseline["manifest"]["date_raw"]
    manifest["initial_date"] = baseline["snapshots"][tags[0]]["date"]
    write_json(manifest_path, manifest)
    history = {}
    public_board, host_messages = [], []
    try:
        for index in range(1, limit + 1):
            trial = directory / "trials" / f"turn_{index:03d}"
            execution = directory / "native_turns" / f"turn_{index:03d}"
            turn = directory / "turns" / f"turn_{index:03d}"
            after_dir = directory / "captures" / f"after_{index:03d}"
            entry = next((item for item in manifest["turns"] if item["index"] == index), None)
            if entry and entry["status"] == "completed":
                baseline = load_capture(after_dir, context)
                history = compact_history(trial, read(turn / "result.json"), tags)
                public_board = read(turn / "public_events.json") if (turn / "public_events.json").exists() else []
                host_messages = read(trial / "performance.json").get("host", []) if (trial / "performance.json").exists() else []
                continue
            if entry is None:
                entry = {"index": index, "status": "prepared", "started_at": now(), "trial": str(trial), "turn": str(turn)}
                manifest["turns"].append(entry)
            turn_start = time.monotonic()
            require_no_pending_choice(baseline["state"])
            selection_path = trial / "clock_selection.json"
            if selection_path.exists():
                selection = read(selection_path)
            elif "clock_policy" in config:
                selection = select_step_hours(at_war=bool(baseline["state"].get("public_wars")),
                                              public_events=public_board, policy=config["clock_policy"])
                write_json(selection_path, selection)
            else:
                selection = {"step_hours": config["step_hours"], "reason": "fixed"}
            turn_show = turn_show_config(config, directory)
            turn_show["step_hours"] = selection["step_hours"]
            observed = {}
            for tag in tags:
                path = trial / "observations" / (tag + ".json")
                if path.exists():
                    observed[tag] = read(path)
                else:
                    observed[tag] = build_observation(tag, baseline["snapshots"][tag], name, index,
                                                      {"kind": "native_capture", "capture": baseline["manifest"]},
                                                      state=baseline["state"], map_context=context, history=history.get(tag, []))
                    if config["show"].get("strict_public_context"):
                        observed[tag]["public_board"] = public_board[-256:]
                        observed[tag]["public_host_messages"] = host_messages
                        observed[tag]["event_status"] = baseline["state"].get("pending_events")
                        memory_path = directory / "own_memory" / (tag + ".json")
                        if memory_path.exists():
                            # Two recent turns are already in the normal brief.
                            memory = compress_own_memory(read(memory_path)[:-2], tag, max_turns=6, max_letters=8)
                            while memory["records"] and len(json.dumps(memory, ensure_ascii=False)) > 24000:
                                memory["records"].pop(0)
                            memory["retained_turn_count"] = len(memory["records"])
                            memory["retained_letter_count"] = sum(len(row.get("private_letters", [])) for row in memory["records"])
                            memory["character_budget"] = 24000
                            observed[tag]["own_memory"] = memory
                        from native_observation import _digest
                        observed[tag]["observation_id"] = _digest({key: value for key, value in observed[tag].items() if key != "observation_id"})
                    write_json(path, observed[tag])
            if not (trial / "decision_result.json").exists() or read(trial / "decision_result.json")["status"] != "approved":
                entry["status"] = "deciding"; write_json(manifest_path, manifest)
                model_start = time.monotonic()
                turn_switch = None
                for record in manifest.get("model_switches", []):
                    if record.get("first_affected_turn", config["turn_limit"] + 1) <= index:
                        turn_switch = record
                decide(trial, config["model"], tags, resume=(trial / "decision_result.json").exists(),
                       communication_rounds=config["communication_rounds"], private_diplomacy=True,
                       show_config=turn_show, model_switch=turn_switch)
                entry["model_seconds"] = round(time.monotonic() - model_start, 3)
            requests = verify_decisions(trial, observed)
            deferred = config.get("audience_timing", "inline") == "postmatch"
            performance = post_seal(trial, observed, config["model"], turn_show, config.get("performance", {}),
                                    phase="live" if deferred else "all")
            plan = to_native_plan(requests, observed, step_hours=selection["step_hours"],
                                  label=f"{name} turn {index}", save_basename=f"iron_{name}_t{index}")
            if "policy" in selection:
                plan.update(clock_policy=selection["policy"], clock_selection={"reason": selection["reason"], "shared_clock": True})
            entry["status"] = "approved"; write_json(manifest_path, manifest)
            native_start = time.monotonic()
            if (execution / "result.json").exists():
                native = read(execution / "result.json")
                if read(execution / "plan.json") != plan:
                    raise ValueError("existing native execution differs from sealed decisions")
            else:
                if execution.exists():
                    raise ValueError("native execution has no final receipt; reconcile without replaying orders")
                entry["status"] = "executing"; write_json(manifest_path, manifest)
                def boundary(runner, controls):
                    current = read_world(game, runner.main_status, controls)
                    changes = build_public_events(baseline["state"], current, tags,
                                                  {"before": baseline["manifest"]["date_raw"], "after": runner.date_raw})
                    return {"war_changed": any(row["kind"] == "war_started" for row in changes),
                            "critical": any(row["kind"] == "province_control_changed" for row in changes),
                            "public_events": changes}
                native = NativeTurn(game, plan, execution, save_dir=save_dir, poll_interval=0.5,
                                    boundary_observer=boundary if "clock_policy" in config else None).run()
            if native.get("status") != "completed":
                entry.update(status="interrupted", interruption=native.get("clock_interruption"))
                raise RuntimeError("native turn interrupted; preserve sealed orders and reconcile its remaining clock budget")
            entry["native_seconds"] = round(time.monotonic() - native_start, 3)
            if (after_dir / "capture.json").exists():
                after = load_capture(after_dir, context)
                after["observations"] = read(after_dir / "observations.json")
            else:
                after = capture(game, tags, after_dir, name, index + 1, map_context=context)
            elapsed = native["step_hours"]
            if not 0 < elapsed <= selection["step_hours"] or (elapsed != selection["step_hours"] and not native.get("early_end")):
                raise ValueError("native execution did not honor the sealed clock budget")
            expected = baseline["manifest"]["date_raw"] + elapsed
            if native["date_raw"] != expected or after["manifest"]["date_raw"] != expected:
                raise ValueError("native campaign clock does not match the exact cumulative bound")
            compatibility = adapt_result(native, execution, trial, observed, after)
            public_board = build_public_events(baseline["state"], after["state"], tags,
                {"before": observed[tags[0]]["date"], "after": after["snapshots"][tags[0]]["date"],
                 "before_raw": baseline["manifest"]["date_raw"], "after_raw": after["manifest"]["date_raw"],
                 "match_id": name, "turn_id": index})
            compatibility["public_events"] = public_board
            write_json(turn / "public_events.json", public_board)
            write_json(turn / "result.json", compatibility)
            commentary = generate_commentary(trial, turn, config["commentary_model"]) if config.get("model_commentary", True) and not deferred else None
            export_episode(trial, turn, directory / "episodes" / f"turn_{index:03d}", commentary=commentary)
            if config.get("production"):
                from production import export_production
                export_production(directory / "episodes" / f"turn_{index:03d}", config["production"])
            host_messages = performance.get("host", [])
            remember_commitments(config, trial, directory)
            history = compact_history(trial, compatibility, tags)
            if config["show"].get("strict_public_context"):
                for tag in tags:
                    memory_path = directory / "own_memory" / (tag + ".json")
                    older = read(memory_path) if memory_path.exists() else []
                    unique = {row["turn_id"]: row for row in older + history[tag]}
                    write_json(memory_path, list(unique.values())[-12:])
                    promises_path = directory / "diplomacy" / (tag + ".json")
                    if promises_path.exists():
                        entries = settle_commitments(read(promises_path), public_board, native["date_raw"], index)
                        write_json(promises_path, entries)
                        write_json(turn / "promise_settlements" / (tag + ".json"), entries)
            from scoreboard import score
            origin = load_capture(initial, context)["state"]
            manifest["scoreboard"] = score({**origin, "date": manifest["initial_date"]},
                {**after["state"], "date": compatibility["final_save"]["parsed_date"]}, tags,
                context["map"], config["scoring"], index == config["turn_limit"])
            baseline = after
            entry.update(status="completed", final_date=compatibility["final_save"]["parsed_date"],
                         final_date_raw=native["date_raw"], action_counts={tag: len(requests[tag]["actions"]) for tag in tags},
                         usage=model_usage(trial), elapsed_seconds=round(time.monotonic() - turn_start, 3), completed_at=now())
            manifest["usage"] = {key: sum(row.get("usage", {}).get(key, 0) for row in manifest["turns"])
                                 for key in ("calls", "prompt_tokens", "completion_tokens", "total_tokens", "calls_without_usage")}
            manifest["status"] = "completed" if index == config["turn_limit"] else "ready_for_next_turn"
            manifest.pop("error", None)
            manifest.pop("stopped_at", None)
            write_json(manifest_path, manifest); build_index(directory, manifest)
            print(f"NATIVE TURN {index}/{config['turn_limit']} completed: {entry['final_date']} | actions={sum(entry['action_counts'].values())} | {entry['elapsed_seconds']:.1f}s", flush=True)
    except Exception as error:
        manifest.update(status="needs_inspection", error=str(error), stopped_at=now())
        write_json(manifest_path, manifest)
        raise
    manifest["timings"]["last_run_seconds"] = round(time.monotonic() - started, 3)
    if manifest["status"] == "completed":
        manifest["completed_at"] = now()
        manifest["elapsed_seconds"] = round((datetime.now(timezone.utc) - datetime.fromisoformat(manifest["started_at"])).total_seconds(), 3)
    write_json(manifest_path, manifest)
    return manifest


def complete_audience(name, *, through_turn=None):
    """Generate viewer-only sidecars from frozen turns, without connecting to the game."""
    directory = ROOT / "runs" / "campaigns" / name_ok(name)
    manifest_path = directory / "manifest.json"
    manifest = read(manifest_path)
    if manifest.get("schema") != "iron.native-campaign.v1":
        raise ValueError("audience completion requires a native campaign")
    for entry in manifest["turns"]:
        index = entry["index"]
        if entry["status"] != "completed" or (through_turn is not None and index > through_turn):
            continue
        audience_start = time.monotonic()
        config = manifest["config"]
        for switch in manifest.get("model_switches", []):
            if switch["first_affected_turn"] <= index:
                config = apply_model_switch(config, switch["spec"])
        trial = directory / "trials" / f"turn_{index:03d}"
        turn = directory / "turns" / f"turn_{index:03d}"
        observed = {tag: read(trial / "observations" / (tag + ".json")) for tag in config["countries"]}
        verify_decisions(trial, observed)
        show = {**config["show"], **read(trial / "show_profiles.json")}
        post_seal(trial, observed, config["model"], show, config.get("performance", {}), phase="audience")
        commentary = generate_commentary(trial, turn, config["commentary_model"]) if config.get("model_commentary", True) else None
        output = directory / "episodes_postmatch" / f"turn_{index:03d}"
        export_episode(trial, turn, output, commentary=commentary)
        if config.get("production"):
            from production import export_production
            export_production(output, config["production"])
        entry["usage"] = model_usage(trial)
        entry["audience_completed_at"] = now()
        entry["audience_seconds"] = round(time.monotonic() - audience_start, 3)
        entry["audience_episode"] = str(output)
        manifest["usage"] = {key: sum(row.get("usage", {}).get(key, 0) for row in manifest["turns"])
                             for key in ("calls", "prompt_tokens", "completion_tokens", "total_tokens", "calls_without_usage")}
        write_json(manifest_path, manifest)
        print(f"AUDIENCE TURN {index}: {output}", flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--config", default="configs/campaign.native.example.json")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--save-dir", type=Path)
    parser.add_argument("--postmatch", action="store_true", help="Generate audience interviews and commentary without the game")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--turns", type=int)
    parser.add_argument("--prompt-key", action="store_true")
    parser.add_argument("--provider-check", action="store_true")
    parser.add_argument("--switch-model", help="JSON file with provider identity fields applied to every model slot on resume")
    parser.add_argument("--switch-reason", help="Audit reason recorded with the model switch")
    args = parser.parse_args()
    if not args.postmatch and (args.pid is None or args.save_dir is None):
        parser.error("--pid and --save-dir are required when running the game")
    if args.prompt_key:
        if args.resume or args.postmatch:
            manifest = read(ROOT / "runs" / "campaigns" / name_ok(args.name) / "manifest.json")
            spec = None
            if args.switch_model:
                path = Path(args.switch_model)
                spec = read(path if path.is_absolute() else ROOT / path)
            config = effective_resumed_config(manifest, spec)
        else:
            config = load_config(args.config)
        keys = set(key_envs(config))
        if args.postmatch:
            historical = manifest["config"]
            keys.update(key_envs(historical))
            for switch in manifest.get("model_switches", []):
                historical = apply_model_switch(historical, switch["spec"])
                keys.update(key_envs(historical))
        for key in sorted(keys):
            if not os.environ.get(key):
                os.environ[key] = getpass.getpass(f"{key} (hidden): ")
    if args.postmatch:
        if args.switch_model or args.provider_check:
            parser.error("--postmatch reuses recorded providers and does not perform provider checks")
        manifest = complete_audience(args.name, through_turn=args.turns)
    else:
        manifest = run_campaign(args.name, args.config, args.pid, args.save_dir,
                                resume=args.resume, through_turn=args.turns, provider_check=args.provider_check,
                                switch_model=args.switch_model, switch_reason=args.switch_reason)
    print(json.dumps({"status": manifest["status"], "turns": len(manifest["turns"]),
                      "elapsed_seconds": manifest.get("elapsed_seconds"), "usage": manifest["usage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
