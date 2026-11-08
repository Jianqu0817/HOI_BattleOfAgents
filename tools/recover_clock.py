"""Resume an interrupted time step from the verified current engine date."""
import argparse
from pathlib import Path
import sys

from m0_probe import Probe, ROOT, game_hours, write_json, settled_pause_state
from agent_contract import many, object_id, construction_queue
from run_turn import read, saved_state, finish_clock


def clock_position(reference, current, target_hours, last_verified):
    advanced = game_hours(current) - game_hours(reference)
    if not 0 <= last_verified <= advanced <= target_hours:
        raise ValueError("Current engine date is outside the interrupted turn's verified time range")
    return advanced, target_hours - advanced


def retained_orders(result, checkpoint, current):
    checks = []
    for entry in result["actions"]:
        if entry["status"] != "applied":
            continue
        tag, action = entry["country"], entry["action"]
        own, old = current["countries"][tag], checkpoint["countries"][tag]
        args, kind = action["args"], action["type"]
        if kind.startswith("army."):
            from army_actions import retained_army_order
            kept = retained_army_order(action, checkpoint, current, tag)
        elif kind == "research.start":
            technology = args["technology_id"]
            completed = own["technology"]["technologies"].get(technology, {}).get("level", 0) > old["technology"]["technologies"].get(technology, {}).get("level", 0)
            kept = technology in own["technology"]["slots"] or completed
        elif kind == "focus.select":
            kept = own["focus"].get("current") == args["focus_id"] or args["focus_id"] in many(own["focus"].get("completed"))
        elif kind == "production.assign":
            line = next((row for row in many(own["production"]["military_lines"]) if object_id(row["id"]) == args["line_id"]), None)
            kept = line is not None and line["requested_factories"] == args["factories"]
        elif kind == "construction.queue":
            def commitment(state):
                built = state["states"][str(args["state_id"])].get("buildings", {}).get(args["building_id"], {}).get("level", 0)
                return built + sum(row.get("amount", 1) for row in construction_queue(state["countries"][tag]) if row["building"] == {"template": args["building_id"], "location": args["state_id"]})
            kept = commitment(current) >= commitment(checkpoint)
        else:
            raise ValueError("Unrecognised action in recovery journal")
        checks.append({"country": tag, "action_id": action["id"], "retained_or_completed": bool(kept)})
    return checks


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/match.fifteen_days.json")
    parser.add_argument("--known-disabled-pid", type=int, required=True)
    parser.add_argument("--notices", type=Path)
    args = parser.parse_args()
    if not args.name.replace("_", "").isalnum():
        parser.error("name must be letters, digits or underscores")
    directory = ROOT / "runs/turns" / args.name
    path = directory / "result.json"
    result = read(path)
    hours = read(args.config)["step_hours"]
    if result["status"] != "interrupted" or "before_clock" not in result or "final_save" in result or any(entry["status"] == "unknown" for entry in result["actions"]):
        raise ValueError("Recovery requires an interrupted clock phase with resolved actions and no final save")
    if result["known_disabled_pid"] != args.known_disabled_pid or result.get("target_hours", hours) != hours:
        raise ValueError("Recovery process or target hours differ from the original turn")
    reference = result["before_save"]["parsed_date"]
    latest = next((entry["save"] for entry in reversed(result["actions"]) if entry["status"] == "applied"), result["before_save"])
    checkpoint = saved_state(directory, latest)
    recoveries = result.setdefault("clock_recoveries", [])
    recovery = {"number": len(recoveries) + 1, "status": "checking"}
    recoveries.append(recovery)
    write_json(path, result)
    probe = Probe(directory, result["countries"])
    try:
        with probe.session.lock.hold("IRON interrupted clock recovery"):
            status = probe.call("hoi4_status", {})
            if status["game"]["pids"] != [args.known_disabled_pid] or settled_pause_state(probe.session)[0] != "paused":
                raise ValueError("Recovery requires the same controlled process in a paused game")
            receipt = probe.receipts(result["countries"] + [result["countries"][0]], "clock_recovery")
            last = result.get("clock_steps", [])[-1]["advanced_hours"] if result.get("clock_steps") else 0
            advanced, remaining = clock_position(reference, receipt["date"], hours, last)
            fresh = probe.save("iron_" + args.name + "_clock_recovery_" + str(recovery["number"]))
            current = saved_state(directory, fresh)
            if game_hours(current["date"]) != game_hours(receipt["date"]):
                raise ValueError("Date changed while taking the recovery checkpoint")
            checks = retained_orders(result, checkpoint, current)
            if not all(row["retained_or_completed"] for row in checks):
                raise ValueError("An approved order changed during the interrupted clock phase")
            recovery.update(status="verified", receipt=receipt, save=fresh, advanced_hours=advanced,
                            remaining_hours=remaining, order_checks=checks)
            if advanced > last:
                result.setdefault("clock_steps", []).append({"hours": advanced - last, "advanced_hours": advanced, "receipt": receipt, "recovered": True})
            if args.notices:
                records = read(args.notices)
                if any(record.get("kind") != "passive_news" for record in records):
                    raise ValueError("Only source-verified public news records may be attached")
                result.setdefault("public_notices", []).extend({**record, "observed_date": receipt["date"]} for record in records)
            result["target_hours"] = hours
            result.pop("error", None)
            write_json(path, result)
            print(f"RECOVERED {advanced}/{hours} hours; {remaining} hours remain; no actions replayed", flush=True)
            finish_clock(probe, result, path, reference, hours, advanced)
    except Exception as error:
        result.update(status="interrupted", error=str(error))
        raise
    finally:
        result.setdefault("completion_notices", []).extend(getattr(probe.session, "_iron_completion_notices", []))
        result.setdefault("public_notices", []).extend(getattr(probe.session, "_iron_public_notices", []))
        write_json(path, result)
        probe.session.shutdown()


if __name__ == "__main__":
    main()
