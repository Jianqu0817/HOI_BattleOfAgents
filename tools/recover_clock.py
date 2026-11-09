"""Offline verification of archived clock checkpoints."""

from m0_probe import ROOT, game_hours
from agent_contract import many, object_id, construction_queue


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
        if kind.startswith(("air.", "navy.")):
            from naval_air_actions import retained_naval_air_order
            kept = retained_naval_air_order(action, checkpoint, current, tag)
        elif kind == "diplomacy.declare_war":
            from diplomacy_actions import retained_declaration
            kept = retained_declaration(action, checkpoint, current, tag)
        elif kind.startswith("army."):
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


if __name__ == "__main__":
    raise SystemExit("旧桌面对局入口已移除。请使用 tools/native_campaign.py。")
