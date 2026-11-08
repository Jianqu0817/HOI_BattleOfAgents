"""Complete the recorded ordinary move of German division 16 in the M1 fixture."""
import json
from pathlib import Path
import sys
import time

from m0_probe import Probe, game_hours, write_json


def division(state):
    return next(unit for unit in state["countries"]["GER"]["units"]["division"]
                if (unit["id"][0] if isinstance(unit["id"], list) else unit["id"])["id"] == 16)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    directory = Path("runs/m1_actions/army_order")
    before = json.loads((directory / "iron_army_order.json").read_text(encoding="utf-8"))
    if (directory / "motion_result.json").exists():
        raise SystemExit("Movement fixture already has a result; do not replay it")
    probe = Probe(directory, ["GER", "ITA", "JAP", "ENG", "SOV"])
    result = {"division_id": 16, "before_location": division(before)["location"],
              "order_priority": division(before).get("move_priority"), "status": "running"}
    try:
        with probe.session.lock.hold("IRON player movement verification"):
            result["switch_before"] = probe.receipts(["ITA", "JAP", "ENG", "SOV", "GER"], "move_switch")
            from hoi4_live.game import console, timectl
            from hoi4_live.perception import topbar
            console.close_console(probe.session)
            timectl.set_speed(probe.session, 4)
            probe.command(["pause_in_hours 96"])
            deadline = time.monotonic() + 90
            while topbar.pause_state(probe.session.grab())[0] != "paused":
                if time.monotonic() > deadline:
                    raise RuntimeError("Bounded movement step did not end")
                time.sleep(.4)
            result["after_clock"] = probe.receipts(["GER"], "move_after")
            result["save"] = probe.save("iron_army_after")
            after = json.loads((directory / "iron_army_after.json").read_text(encoding="utf-8"))
            result["after_location"] = division(after)["location"]
            result["elapsed_game_hours"] = game_hours(after["date"]) - game_hours(before["date"])
            result["research_retained"] = "basic_machine_tools" in after["countries"]["GER"]["technology"]["slots"]
            lines = after["countries"]["GER"]["production"]["military_lines"]
            result["rifle_factories_retained"] = next(line for line in lines if line["id"]["id"] == 1)["requested_factories"] == 9
            result["verified"] = result["order_priority"] == "player_order" and result["after_location"] != result["before_location"] and result["elapsed_game_hours"] == 96 and result["research_retained"] and result["rifle_factories_retained"]
            result["status"] = "verified" if result["verified"] else "needs_inspection"
            console.close_console(probe.session)
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        write_json(directory / "motion_result.json", result)
        probe.session.shutdown()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
