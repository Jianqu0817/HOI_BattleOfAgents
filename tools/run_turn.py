"""Run one saved, deterministic 3–5-country turn through the Agent action contract."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from m0_probe import Probe, ROOT, countries_from, game_hours, order_for, write_json, settled_pause_state
from agent_contract import country_fingerprint, validate_request, execute_action, verify_action


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def saved_state(directory, save):
    return read(directory / (save["name"].removeprefix("hoi4live_") + ".json"))


def check_diplomacy_baseline(state, observed, checkpoint=None):
    from diplomacy_actions import diplomacy_fingerprint
    hashes = [value.get("diplomacy", {}).get("baseline_sha256") for value in observed.values()]
    if not any(hashes):
        return None  # Legacy observations did not expose declaration actions.
    if any(value is None for value in hashes) or len(set(hashes)) != 1:
        raise ValueError("Country observations disagree on the diplomatic baseline")
    expected = diplomacy_fingerprint(checkpoint) if checkpoint is not None else hashes[0]
    actual = diplomacy_fingerprint(state)
    if actual != expected:
        raise ValueError("War, goal or faction state changed after the frozen observation/checkpoint")
    return {"expected": expected, "actual": actual, "verified": True}


def check_naval_air_baseline(state, observed, checkpoint=None):
    from naval_air_actions import naval_air_fingerprint
    hashes = {tag: value.get("naval_air", {}).get("state_fingerprint") for tag, value in observed.items()}
    if not any(hashes.values()):
        return None
    if any(value is None for value in hashes.values()):
        raise ValueError("An observation lacks its naval/air baseline")
    expected = {tag: naval_air_fingerprint(checkpoint, tag) for tag in observed} if checkpoint is not None else hashes
    actual = {tag: naval_air_fingerprint(state, tag) for tag in observed}
    if actual != expected:
        raise ValueError("Naval/air state changed after the frozen observation/checkpoint")
    return {"expected": expected, "actual": actual, "verified": True}


def check_army_activity_baseline(state, observed, checkpoint=None):
    from army_actions import army_activity
    if not any("army_activity" in value for value in observed.values()):
        return None
    if any("army_activity" not in value for value in observed.values()):
        raise ValueError("An observation lacks its army activity baseline")
    expected = ({tag: army_activity(checkpoint["countries"][tag]) for tag in observed} if checkpoint is not None
                else {tag: value["army_activity"] for tag, value in observed.items()})
    actual = {tag: army_activity(state["countries"][tag]) for tag in observed}
    if actual != expected:
        raise ValueError("Army activity changed after the frozen observation/checkpoint")
    return {"expected": expected, "actual": actual, "verified": True}


def resume_checkpoint(result, directory):
    """Resolve saved action outcomes before resuming a paused execution phase."""
    if result["status"] not in ("needs_inspection", "interrupted") or "before_clock" in result:
        raise ValueError("Only interrupted pre-clock execution can resume")
    if "before_save" not in result:
        if result.get("actions"):
            raise ValueError("Actions exist without an initial checkpoint")
        return None, []
    previous = saved_state(directory, result["before_save"])
    resolutions = []
    for entry in result["actions"]:
        if "save" not in entry:
            raise ValueError("An action lacks an after-save; inspect its live outcome before recovery")
        after = saved_state(directory, entry["save"])
        if entry["status"] == "unknown":
            verified = verify_action(entry["action"], previous, after, entry["country"])
            from diplomacy_actions import diplomacy_fingerprint
            from naval_air_actions import naval_air_fingerprint
            from army_actions import army_activity
            unchanged = (previous["date"] == after["date"]
                         and all(country_fingerprint(previous, tag) == country_fingerprint(after, tag) for tag in result["countries"])
                         and all(naval_air_fingerprint(previous, tag) == naval_air_fingerprint(after, tag) for tag in result["countries"])
                         and all(army_activity(previous["countries"][tag]) == army_activity(after["countries"][tag]) for tag in result["countries"])
                         and diplomacy_fingerprint(previous) == diplomacy_fingerprint(after))
            if verified["verified"]:
                entry["status"] = "applied"
                entry["verification"] = verified
                entry["effect_status"] = verified.get("effect_status", "full")
            elif unchanged:
                entry["status"] = "not_applied"
                entry["effect_status"] = "none"
            else:
                raise ValueError("Unknown action changed gameplay without the expected effect; manual inspection required")
            resolutions.append({"country": entry["country"], "id": entry["action"]["id"], "resolved_status": entry["status"], "evidence": entry["save"]["path"]})
        elif entry["status"] not in ("applied", "not_applied"):
            raise ValueError("Unsupported action journal state")
        previous = after
    return previous, resolutions


def finish_clock(probe, result, result_path, reference_date, hours, advanced=0):
    from hoi4_live.game import console, timectl, panels
    from hoi4_live.perception import topbar
    countries = result["countries"]
    result["status"] = "advancing"
    result.setdefault("clock_steps", [])
    write_json(result_path, result)
    console.close_console(probe.session)
    panels.to_map(probe.session)
    timectl.set_speed(probe.session, 4)
    while advanced < hours:
        chunk = min(24, hours - advanced)
        probe.command([f"pause_in_hours {chunk}"])
        deadline = time.monotonic() + 90
        while True:
            pause, _ = topbar.pause_state(probe.session.grab())
            if pause == "paused":
                break
            if time.monotonic() > deadline:
                if pause == "running":
                    console.close_console(probe.session)
                    probe.session.input.key("space")
                raise RuntimeError("Timed out waiting for the bounded engine pause")
            time.sleep(.4)
        receipt = probe.receipts([countries[0]], "turn_clock_step")
        advanced += chunk
        if game_hours(receipt["date"]) - game_hours(reference_date) != advanced:
            raise RuntimeError("Engine clock differs from the intermediate target")
        result["clock_steps"].append({"hours": chunk, "advanced_hours": advanced, "receipt": receipt,
                                      "observed_at": datetime.now(timezone.utc).isoformat()})
        write_json(result_path, result)
        print(f"CLOCK {advanced}/{hours} hours: {receipt['date']}", flush=True)
    result["after_clock"] = probe.receipts(countries + [countries[0]], "turn_after_clock")
    result["elapsed_game_hours"] = game_hours(result["after_clock"]["date"]) - game_hours(reference_date)
    if result["elapsed_game_hours"] != hours:
        raise RuntimeError("Engine date differs from the requested common step")
    result["final_save"] = probe.save("iron_" + probe.directory.name + "_final")
    console.close_console(probe.session)
    panels.to_map(probe.session)
    from army_actions import clear_hover
    clear_hover(probe.session)
    result["after_map"] = probe.session.artifact(probe.session.grab(), "turn_final_map")
    probe.call("hoi4_screenshot", {})
    result["status"] = "completed_deterministic_turn"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--decision-source", default="deterministic fixture files")
    parser.add_argument("--resume", action="store_true", help="Recover a paused pre-clock run from exact saved outcomes")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/match.example.json")
    parser.add_argument("--known-disabled-pid", type=int, required=True,
                        help="Same game process in which all configured country AI disable receipts were obtained")
    args = parser.parse_args()
    if not args.name.replace("_", "").isalnum():
        parser.error("name must contain only letters, digits and underscores")
    config = read(args.config)
    countries = countries_from(config)
    hours = config["step_hours"]
    if type(hours) is not int or not 1 <= hours <= 360:
        parser.error("This turn runner supports 1–360 hours per turn")
    observed = {tag: read(args.observations / (tag + ".json")) for tag in countries}
    requests = {tag: read(args.requests / (tag + ".json")) for tag in countries}
    reference = observed[countries[0]]
    for tag in countries:
        if observed[tag]["country"] != tag:
            raise ValueError("Observation file belongs to another country")
        for key in ("match_id", "turn_id", "date"):
            if observed[tag][key] != reference[key]:
                raise ValueError(f"Country observations disagree on {key}")
        if observed[tag]["source"]["sha256"] != reference["source"]["sha256"]:
            raise ValueError("Observations must come from one common save")
        validate_request(requests[tag], observed[tag])
    directory = ROOT / "runs" / "turns" / args.name
    result_path = directory / "result.json"
    checkpoint, resolutions = None, []
    if args.resume:
        if not result_path.exists():
            parser.error("resume needs an existing run")
        result = read(result_path)
        if result["requests"] != requests or result["countries"] != countries or result["known_disabled_pid"] != args.known_disabled_pid:
            raise ValueError("Resume must use the original frozen requests, countries and controlled process")
        checkpoint, resolutions = resume_checkpoint(result, directory)
        attempts = result.setdefault("resume_attempts", [])
        attempts.append({"number": len(attempts) + 1, "resolutions": resolutions,
                         "skipped_applied": [entry["action"]["id"] for entry in result["actions"] if entry["status"] == "applied"]})
        result["status"] = "resuming"
    elif result_path.exists():
        raise SystemExit("Turn already started; inspect its saved journal instead of replaying")
    else:
        result = {"status": "starting", "match_id": reference["match_id"], "turn_id": reference["turn_id"],
                  "countries": countries, "decision_source": args.decision_source, "actions": [],
                  "target_hours": hours,
                  "known_disabled_pid": args.known_disabled_pid, "requests": requests}
    write_json(result_path, result)
    probe = Probe(directory, countries)
    try:
        with probe.session.lock.hold("IRON deterministic multicountry turn"):
            status = probe.call("hoi4_status", {})
            if status["game"]["pids"] != [args.known_disabled_pid]:
                raise RuntimeError("Game process changed; re-establish country AI control before running a turn")
            from hoi4_live.game import console, timectl, panels
            from hoi4_live.perception import topbar
            if settled_pause_state(probe.session)[0] != "paused":
                raise RuntimeError("A turn must start in a confirmed paused game")
            receipt = probe.receipts(countries + [countries[0]], "turn_resume" if args.resume else "turn_initial")
            suffix = "resume_" + str(len(result["resume_attempts"])) if args.resume else "before"
            fresh_save = probe.save("iron_" + args.name + "_" + suffix)
            if args.resume:
                result["resume_attempts"][-1].update(save=fresh_save, actor_receipts=receipt)
            else:
                result.update(initial_switch=receipt, before_save=fresh_save)
            state = saved_state(directory, fresh_save)
            if game_hours(state["date"]) != game_hours(reference["date"]):
                raise RuntimeError("Game date changed after observation")
            expected = {tag: country_fingerprint(checkpoint, tag) if checkpoint is not None else observed[tag]["state_fingerprint"] for tag in countries}
            mismatches = [tag for tag in countries if country_fingerprint(state, tag) != expected[tag]]
            if mismatches:
                raise RuntimeError(f"Gameplay state changed after observation: {mismatches}")
            result["diplomacy_baseline_verification"] = check_diplomacy_baseline(state, observed, checkpoint)
            result["naval_air_baseline_verification"] = check_naval_air_baseline(state, observed, checkpoint)
            result["army_activity_baseline_verification"] = check_army_activity_baseline(state, observed, checkpoint)
            if "before_save" not in result:
                result.update(initial_switch=receipt, before_save=fresh_save)
            result["fresh_state_verified"] = True
            result["execution_order"] = order_for(countries, reference["turn_id"])
            result["status"] = "executing"
            result.pop("error", None)
            if "before_map" not in result:
                from army_actions import clear_hover
                console.close_console(probe.session)
                panels.to_map(probe.session)
                clear_hover(probe.session)
                result["before_map"] = probe.session.artifact(probe.session.grab(), "turn_initial_map")
            write_json(result_path, result)
            applied = {(entry["country"], entry["action"]["id"]) for entry in result["actions"] if entry["status"] == "applied"}
            for tag in result["execution_order"]:
                receipt = probe.receipts([tag], "turn_actor_" + tag)
                console.close_console(probe.session)
                panels.to_map(probe.session)
                for action in requests[tag]["actions"]:
                    if (tag, action["id"]) in applied:
                        print(f"SKIPPED applied {tag}: {action['id']}", flush=True)
                        continue
                    entry = {"country": tag, "action": action, "actor_receipt": receipt, "status": "unknown",
                             "started_at": datetime.now(timezone.utc).isoformat()}
                    result["actions"].append(entry)
                    write_json(result_path, result)
                    try:
                        entry["ui_result"] = execute_action(probe.session, tag, action, state)
                    except Exception as error:
                        entry["ui_error"] = str(error)
                    name = "iron_" + args.name + "_action_" + str(len(result["actions"]))
                    entry["save"] = probe.save(name)
                    after = read(directory / (name + ".json"))
                    entry["verification"] = verify_action(action, state, after, tag)
                    if not entry["verification"]["verified"]:
                        result["status"] = "needs_inspection"
                        raise RuntimeError(f"Action outcome is unverified: {action['id']}; time will not advance")
                    entry["status"] = "applied"
                    entry["effect_status"] = entry["verification"].get("effect_status", "full")
                    entry["ended_at"] = datetime.now(timezone.utc).isoformat()
                    state = after
                    write_json(result_path, result)
                    print(f"APPLIED {tag}: {action['type']} ({action['id']})", flush=True)
            result["before_clock"] = probe.receipts(countries + [countries[0]], "turn_before_clock")
            finish_clock(probe, result, result_path, reference["date"], hours)
    except Exception as error:
        result["error"] = str(error)
        if result["status"] != "needs_inspection":
            result["status"] = "interrupted"
        raise
    finally:
        result["completion_notices"] = getattr(probe.session, "_iron_completion_notices", [])
        result.setdefault("public_notices", []).extend(getattr(probe.session, "_iron_public_notices", []))
        write_json(result_path, result)
        probe.session.shutdown()
        print(json.dumps({key: result.get(key) for key in ("status", "error", "fresh_state_verified", "execution_order", "elapsed_game_hours")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
