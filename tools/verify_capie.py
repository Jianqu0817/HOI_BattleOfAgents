"""Verify the two-field CAPIE fixture across the configured 3–5 countries."""
import json
from pathlib import Path
import re
import sys
import time

from m0_probe import Probe, countries_from, game_hours, write_json
from verify_army_motion import division


def bridge_vars(country):
    return {key: value for key, value in country.get("variables", {}).items()
            if key.startswith("iron_bridge_")}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    directory = Path("runs/capie_bridge/five_country")
    result_path = directory / "result.json"
    if result_path.exists():
        raise SystemExit("This fixture already started; inspect its result before any replay")
    tags = countries_from(json.loads(Path("configs/match.example.json").read_text(encoding="utf-8")))
    baseline = json.loads(Path("runs/capie_bridge/runtime/iron_capie_verified.json").read_text(encoding="utf-8"))
    result = {"status": "running", "countries": tags, "receipts": [], "verified": False}
    write_json(result_path, result)
    probe = Probe(directory, tags)
    try:
        with probe.session.lock.hold("IRON CAPIE country isolation fixture"):
            result["before_save"] = probe.save("iron_capie_five_before")
            before = json.loads((directory / "iron_capie_five_before.json").read_text(encoding="utf-8"))
            if before["date"] != baseline["date"]:
                raise RuntimeError("Fixture date changed; inspect the paused game")
            expected = {}
            for index, tag in enumerate(tags):
                epoch = bridge_vars(before["countries"][tag]).get("iron_bridge_epoch", 0) + 1
                sequence, argument = str(index + 1), str(42 + index)
                offset = probe.game_log.stat().st_size
                probe.command([f"tag {tag}", 'eval_effect FROM = { add_to_variable = { iron_bridge_epoch = 1 } set_country_flag = iron_bridge_open }'])
                # Console is known open after command(); close before touching the panel.
                probe.call("hoi4_key", {"keys": "grave", "verify": True})
                for y, text in ((336, sequence), (372, argument)):
                    probe.call("hoi4_click", {"x": 610, "y": y, "verify": True})
                    probe.call("hoi4_key", {"keys": "ctrl+a"})
                    probe.call("hoi4_type", {"text": text, "paste": True, "enter": False, "verify": True})
                probe.call("hoi4_key", {"keys": "enter", "verify": True})
                time.sleep(.3)
                with probe.game_log.open("rb") as stream:
                    stream.seek(offset)
                    fresh = stream.read().decode("utf-8", "replace")
                codes = [53 + int(char) for char in sequence + argument]
                payload = (f"IRON_CAPIE_ACK country={tag} epoch={epoch} api=8 seq_len=1 "
                           f"seq0={codes[0]} arg_len=2 arg0={codes[1]} arg1={codes[2]}")
                row = next((line for line in fresh.splitlines() if payload in line), None)
                if row is None or "IRON_CAPIE_REJECT" in fresh:
                    raise RuntimeError(f"Fresh CAPIE receipt failed for {tag}: {fresh}")
                match = re.search(r"\[(\d{4}\.\d{2}\.\d{2}\.\d{2})\]:", row)
                if match is None:
                    raise RuntimeError("Receipt lacks an engine date")
                expected[tag] = {"iron_bridge_api": 8, "iron_bridge_epoch": epoch + 1,
                                 "iron_bridge_seq_len": 1, "iron_bridge_seq_0": codes[0],
                                 "iron_bridge_arg_len": 2, "iron_bridge_arg_0": codes[1],
                                 "iron_bridge_arg_1": codes[2]}
                result["receipts"].append({"country": tag, "sequence": sequence, "argument": argument,
                                           "date": match[1], "log": row})
                write_json(result_path, result)
                print(f"ACK {tag}: sequence={sequence} argument={argument}", flush=True)
            probe.command([f"tag {tags[0]}"])
            result["after_save"] = probe.save("iron_capie_five_after")
            after = json.loads((directory / "iron_capie_five_after.json").read_text(encoding="utf-8"))
            result["variables"] = {tag: bridge_vars(after["countries"][tag]) for tag in tags}
            result["country_payloads_verified"] = result["variables"] == expected
            result["same_date"] = before["date"] == after["date"] and all(game_hours(r["date"]) == game_hours(before["date"]) for r in result["receipts"])
            result["research_retained"] = "basic_machine_tools" in after["countries"]["GER"]["technology"]["slots"]
            result["rifle_factories_retained"] = next(line for line in after["countries"]["GER"]["production"]["military_lines"] if line["id"]["id"] == 1)["requested_factories"] == 9
            result["army_destination_retained"] = division(after)["location"] == 9398
            result["peer_gameplay_unchanged"] = all(before["countries"][tag][domain] == after["countries"][tag][domain] for tag in tags for domain in ("technology", "production", "units"))
            result["verified"] = all(result[key] for key in ("country_payloads_verified", "same_date", "research_retained", "rifle_factories_retained", "army_destination_retained", "peer_gameplay_unchanged"))
            result["status"] = "verified" if result["verified"] else "needs_inspection"
            from hoi4_live.game import console
            console.close_console(probe.session)
            probe.call("hoi4_screenshot", {})
    except Exception as error:
        result["status"] = "failed"
        result["error"] = str(error)
        raise
    finally:
        write_json(result_path, result)
        probe.session.shutdown()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
