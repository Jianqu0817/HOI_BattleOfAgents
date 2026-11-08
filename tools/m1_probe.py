"""Verify ordinary research, production, focus and construction actions in saves."""
import argparse
import json
import sys

from m0_probe import Probe, ROOT, countries_from, write_json


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=["research", "production", "focus", "construction"])
    parser.add_argument("--country", default="GER")
    parser.add_argument("--tech", default="basic_machine_tools")
    parser.add_argument("--focus", default="GER_the_four_year_plan")
    parser.add_argument("--state", type=int, default=64)
    parser.add_argument("--building", default="industrial_complex")
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument("--line", default="1")
    parser.add_argument("--factories", type=int)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if not args.name.replace("_", "").isalnum():
        parser.error("name must contain only letters, digits and underscores")
    tags = countries_from(json.loads((ROOT / "configs/match.example.json").read_text(encoding="utf-8")))
    if args.country not in tags:
        parser.error("country must be in the match configuration")
    directory = ROOT / "runs" / "m1_actions" / args.name
    if (directory / "result.json").exists():
        parser.error("case already has a result; inspect it before choosing another action")
    probe = Probe(directory, tags)
    result = {"case": args.case, "country": args.country, "status": "started"}
    write_json(directory / "result.json", result)
    try:
        with probe.session.lock.hold("IRON ordinary player action validation"):
            result["actor"] = probe.receipts([args.country], "action_actor")
            before_name = "iron_" + args.name + "_before"
            after_name = "iron_" + args.name + "_after"
            result["before_save"] = probe.save(before_name)
            before = json.loads((directory / (before_name + ".json")).read_text(encoding="utf-8"))
            if args.case == "research":
                if args.slot < 1:
                    raise ValueError("slot must be positive")
            elif args.case == "production":
                if args.factories is None:
                    raise ValueError("production needs --factories")
            try:
                if args.case == "research":
                    from player_actions import research_start
                    result["ui_result"] = research_start(probe.session, args.country, args.tech, args.slot, before["countries"][args.country]["technology"])
                elif args.case == "production":
                    from player_actions import production_assign_one
                    from agent_contract import equipment_variant_name, object_id, many
                    production = before["countries"][args.country]["production"]
                    selected = next(row for row in many(production["military_lines"]) if row.get("priority") == int(args.line) - 1)
                    name = equipment_variant_name(before, object_id(selected["equipment_variant_index"]))
                    result["ui_result"] = production_assign_one(probe.session, args.line, args.factories, production, expected_name=name)
                elif args.case == "focus":
                    from player_actions import focus_start
                    result["ui_result"] = focus_start(probe.session, args.focus, before["countries"][args.country])
                else:
                    from player_actions import construction_queue_one
                    result["ui_result"] = construction_queue_one(probe.session, args.country, args.state, args.building, before)
            except RuntimeError as error:
                result["ui_error"] = str(error)
            result["after_save"] = probe.save(after_name)
            after = json.loads((directory / (after_name + ".json")).read_text(encoding="utf-8"))
            result["same_game_date"] = before["date"] == after["date"]
            result["other_countries_research_unchanged"] = all(before["countries"][tag]["technology"] == after["countries"][tag]["technology"] for tag in tags if tag != args.country)
            if args.case == "research":
                first = before["countries"][args.country]["technology"]
                last = after["countries"][args.country]["technology"]
                result["target"] = args.tech
                result["was_active"] = args.tech in first.get("slots", {})
                result["is_active"] = args.tech in last.get("slots", {})
                result["technology_level_before"] = first.get("technologies", {}).get(args.tech, {}).get("level", 0)
                result["technology_level_after"] = last.get("technologies", {}).get(args.tech, {}).get("level", 0)
                result["verified"] = not result["was_active"] and result["is_active"] and result["technology_level_after"] == result["technology_level_before"] and result["same_game_date"] and result["other_countries_research_unchanged"]
            elif args.case == "production":
                def lines(state, tag):
                    value = state["countries"][tag]["production"]["military_lines"]
                    return value if isinstance(value, list) else [value]
                selected = next(line for line in lines(before, args.country) if line.get("priority") == int(args.line) - 1)
                updated = next(line for line in lines(after, args.country) if line["id"] == selected["id"])
                result["line_id"] = selected["id"]
                result["factories_before"] = selected["requested_factories"]
                result["factories_after"] = updated["requested_factories"]
                result["other_lines_allocation_unchanged"] = all(
                    line["requested_factories"] == next(x for x in lines(after, args.country) if x["id"] == line["id"])["requested_factories"]
                    for line in lines(before, args.country) if line["id"] != selected["id"])
                result["other_countries_production_unchanged"] = all(lines(before, tag) == lines(after, tag) for tag in tags if tag != args.country)
                result["verified"] = result["factories_after"] == args.factories and result["other_lines_allocation_unchanged"] and result["other_countries_production_unchanged"] and result["same_game_date"]
            elif args.case == "focus":
                first = before["countries"][args.country]["focus"]
                last = after["countries"][args.country]["focus"]
                result["focus_before"] = first
                result["focus_after"] = last
                result["other_countries_focus_unchanged"] = all(before["countries"][tag]["focus"] == after["countries"][tag]["focus"] for tag in tags if tag != args.country)
                result["verified"] = not first.get("current") and last.get("current") == args.focus and first.get("completed") == last.get("completed") and result["same_game_date"] and result["other_countries_focus_unchanged"]
            else:
                def queue(state, tag):
                    value = state["countries"][tag]["production"].get("general_lines", {}).get("building", [])
                    return value if isinstance(value, list) else [value]
                first = queue(before, args.country)
                last = queue(after, args.country)
                def target(entry):
                    return entry.get("building", {}).get("template") == args.building and entry.get("building", {}).get("location") == args.state
                result["queue_before"] = first
                result["queue_after"] = last
                result["target_levels_added"] = sum(x.get("amount", 1) for x in last if target(x)) - sum(x.get("amount", 1) for x in first if target(x))
                result["other_queue_entries_unchanged"] = [x for x in first if not target(x)] == [x for x in last if not target(x)]
                result["other_countries_production_unchanged"] = all(before["countries"][tag]["production"] == after["countries"][tag]["production"] for tag in tags if tag != args.country)
                result["built_factories_unchanged"] = before["states"][str(args.state)]["buildings"] == after["states"][str(args.state)]["buildings"]
                result["verified"] = result["target_levels_added"] == 1 and result["other_queue_entries_unchanged"] and result["other_countries_production_unchanged"] and result["built_factories_unchanged"] and result["same_game_date"]
            result["status"] = "verified_save" if result["verified"] else "needs_inspection"
            from hoi4_live.game import console
            console.close_console(probe.session)
            probe.call("hoi4_screenshot", {})
    except Exception as error:
        result["error"] = str(error)
        result["status"] = "interrupted"
        raise
    finally:
        write_json(directory / "result.json", result)
        probe.session.shutdown()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
