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

from campaign import (ROOT, build_index, config_digest, key_envs, load_config, model_usage,
                      name_ok, remember_commitments, turn_show_config)
from episode import export_episode, generate_commentary
from m0_probe import write_json
from model_client import complete
from model_trial import decide, digest, own_history
from native_capture import capture, static_context
from native_game import NativeGame
from native_observation import build_observation, to_native_plan
from native_turn import NativeTurn


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def now():
    return datetime.now(timezone.utc).isoformat()


def load_capture(path, context):
    return {"snapshots": read(path / "snapshots.json"), "state": read(path / "world.json"),
            "manifest": read(path / "capture.json"), "map_context": context}


def compact_history(trial, turn, tags):
    """Reuse the existing privacy validator; persist only the two recent own turns."""
    return {tag: own_history(trial, turn, tag) for tag in tags}


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
                 provider_check=False):
    directory = ROOT / "runs" / "campaigns" / name_ok(name)
    manifest_path = directory / "manifest.json"
    if resume:
        manifest = read(manifest_path)
        config = manifest["config"]
    else:
        if manifest_path.exists():
            raise ValueError("campaign exists; use --resume to reuse completed model calls")
        config = load_config(config_path)
        if config.get("execution_backend") != "native":
            raise ValueError("native campaign requires execution_backend=native")
        manifest = {"schema": "iron.native-campaign.v1", "name": name, "config": config,
                    "config_sha256": config_digest(config), "started_at": now(),
                    "status": "starting", "turns": [], "usage": {}, "timings": {}}
        write_json(manifest_path, manifest)
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
                continue
            if entry is None:
                entry = {"index": index, "status": "prepared", "started_at": now(), "trial": str(trial), "turn": str(turn)}
                manifest["turns"].append(entry)
            turn_start = time.monotonic()
            observed = {}
            for tag in tags:
                path = trial / "observations" / (tag + ".json")
                if path.exists():
                    observed[tag] = read(path)
                else:
                    observed[tag] = build_observation(tag, baseline["snapshots"][tag], name, index,
                                                      {"kind": "native_capture", "capture": baseline["manifest"]},
                                                      state=baseline["state"], map_context=context, history=history.get(tag, []))
                    write_json(path, observed[tag])
            if not (trial / "decision_result.json").exists() or read(trial / "decision_result.json")["status"] != "approved":
                entry["status"] = "deciding"; write_json(manifest_path, manifest)
                model_start = time.monotonic()
                decide(trial, config["model"], tags, resume=(trial / "decision_result.json").exists(),
                       communication_rounds=config["communication_rounds"], private_diplomacy=True,
                       show_config=turn_show_config(config, directory))
                entry["model_seconds"] = round(time.monotonic() - model_start, 3)
            requests = verify_decisions(trial, observed)
            plan = to_native_plan(requests, observed, step_hours=config["step_hours"],
                                  label=f"{name} turn {index}", save_basename=f"iron_{name}_t{index}")
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
                native = NativeTurn(game, plan, execution, save_dir=save_dir, poll_interval=0.5).run()
            entry["native_seconds"] = round(time.monotonic() - native_start, 3)
            if (after_dir / "capture.json").exists():
                after = load_capture(after_dir, context)
                after["observations"] = read(after_dir / "observations.json")
            else:
                after = capture(game, tags, after_dir, name, index + 1, map_context=context)
            expected = manifest["initial_date_raw"] + config["step_hours"] * index
            if native["date_raw"] != expected or after["manifest"]["date_raw"] != expected:
                raise ValueError("native campaign clock does not match the exact cumulative bound")
            compatibility = adapt_result(native, execution, trial, observed, after)
            write_json(turn / "result.json", compatibility)
            commentary = generate_commentary(trial, turn, config["commentary_model"]) if config.get("model_commentary", True) else None
            export_episode(trial, turn, directory / "episodes" / f"turn_{index:03d}", commentary=commentary)
            remember_commitments(config, trial, directory)
            history = compact_history(trial, compatibility, tags)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--config", default="configs/campaign.native.example.json")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--save-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--turns", type=int)
    parser.add_argument("--prompt-key", action="store_true")
    parser.add_argument("--provider-check", action="store_true")
    args = parser.parse_args()
    if args.prompt_key:
        config = (read(ROOT / "runs" / "campaigns" / name_ok(args.name) / "manifest.json")["config"]
                  if args.resume else load_config(args.config))
        for key in key_envs(config):
            if not os.environ.get(key):
                os.environ[key] = getpass.getpass(f"{key} (hidden): ")
    manifest = run_campaign(args.name, args.config, args.pid, args.save_dir,
                            resume=args.resume, through_turn=args.turns, provider_check=args.provider_check)
    print(json.dumps({"status": manifest["status"], "turns": len(manifest["turns"]),
                      "elapsed_seconds": manifest.get("elapsed_seconds"), "usage": manifest["usage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
