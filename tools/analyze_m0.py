"""Compare the exact before/after saves of a completed multicountry probe."""
import argparse
import json
from pathlib import Path

from m0_probe import game_hours, write_json


def rows(value):
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


def decisions(country):
    slots = country.get("technology", {}).get("slots", {})
    production = country.get("production", {})
    return {
        "research": sorted(k for k in slots if k != "empty"),
        "focus": country.get("focus", {}).get("current"),
        "military_lines": [
            {k: line.get(k) for k in ("id", "equipment_variant_index", "requested_factories")}
            for line in rows(production.get("military_lines"))],
    }


def divisions(country):
    return {json.dumps(unit.get("id"), sort_keys=True): unit for unit in rows(country.get("units", {}).get("division"))}


def analyze(directory):
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    if report["status"] != "completed_control_probe":
        raise ValueError("Probe did not complete; no completed-run analysis")
    before = json.loads((directory / ("iron_" + directory.name + "_before.json")).read_text(encoding="utf-8"))
    after = json.loads((directory / ("iron_" + directory.name + "_after.json")).read_text(encoding="utf-8"))
    result = {
        "countries": report["countries"], "turns": len(report["turns"]),
        "start": before["date"], "end": after["date"],
        "elapsed_game_hours": game_hours(after["date"]) - game_hours(before["date"]),
        "switch_receipts": sum(len(turn["before"]["receipts"]) for turn in report["turns"]),
        "every_step_exactly_24_hours": all(
            game_hours(turn["after"]["date"]) - game_hours(turn["before"]["date"]) == 24
            for turn in report["turns"]),
        "country_changes": {},
    }
    for tag in report["countries"]:
        first, last = before["countries"][tag], after["countries"][tag]
        a, b = decisions(first), decisions(last)
        old_units, new_units = divisions(first), divisions(last)
        shared = old_units.keys() & new_units.keys()
        result["country_changes"][tag] = {
            "new_research_selected": sorted(set(b["research"]) - set(a["research"])),
            "focus_before": a["focus"], "focus_after": b["focus"],
            "new_focus_selected": bool(b["focus"] and b["focus"] != a["focus"]),
            "military_line_configuration_unchanged": a["military_lines"] == b["military_lines"],
            "military_absolute_priorities_changed": [line.get("priority") for line in rows(first.get("production", {}).get("military_lines"))] != [line.get("priority") for line in rows(last.get("production", {}).get("military_lines"))],
            "divisions_before": len(old_units), "divisions_after": len(new_units),
            "existing_divisions_changed_location": sum(old_units[k].get("location") != new_units[k].get("location") for k in shared),
            "existing_divisions_changed_organisation": sum(old_units[k].get("organisation") != new_units[k].get("organisation") for k in shared),
        }
    write_json(directory / "analysis.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.run), ensure_ascii=False, indent=2))
