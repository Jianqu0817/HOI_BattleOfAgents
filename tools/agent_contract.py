"""Country observations and the bounded ordinary-action contract for IRON."""
import argparse
import hashlib
import json
from pathlib import Path


def many(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def object_id(value):
    return many(value)[0]["id"]


def construction_queue(country):
    return many(country.get("production", {}).get("general_lines", {}).get("building"))


def equipment_variant_name(state, variant_id, country=None, names=None, prefer_short=False):
    variants = [(kind, item) for kind, values in state.get("equipments", {}).items() for item in many(values)
                if isinstance(item, dict) and "id" in item and object_id(item["id"]) == variant_id]
    if len(variants) != 1:
        return None
    kind, variant = variants[0]
    if prefer_short and not variant.get("name"):
        short = (names or {}).get(f"{country}_{kind}_short") or (names or {}).get(kind + "_short")
        if short:
            return short
    return variant.get("name") or (names or {}).get(f"{country}_{kind}") or (names or {}).get(kind)


def country_fingerprint(state, country):
    own = state["countries"][country]
    values = {key: own.get(key) for key in ("focus", "focus_tree", "research_slot", "technology", "production", "units", "politics")}
    from army_actions import army_structure
    values["army_orders"] = army_structure(own)
    values["states"] = {sid: value.get("buildings", {}) for sid, value in state.get("states", {}).items() if value.get("owner") == country}
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def observation(state, country, match_id, turn_id, source, technology_index, focus_index, names, army_context=None):
    from hoi4_live.gamedata import tech, focus
    own = state["countries"][country]
    technology = own.get("technology", {})
    completed_tech = [key for key, value in technology.get("technologies", {}).items()
                      if isinstance(value, dict) and value.get("level", 0) > 0]
    researching = [key for key in technology.get("slots", {}) if key != "empty"]
    saved_focus = own.get("focus", {})
    focuses = focus.tree_focuses(focus_index, own.get("focus_tree"))
    focus_candidates = [] if saved_focus.get("current") else [
        {"id": key, "name": names.get(key, key), "days": int(item["cost"] * focus.DAYS_PER_COST),
         "requires": item.get("available"), "requires_ui_check": True}
        for key, item in focuses.items()
        if focus.status(item, many(saved_focus.get("completed")), None) == "available"
        and "[" not in names.get(key, key)]
    lines = [{"id": object_id(line["id"]), "display_line": line["priority"] + 1,
              "requested_factories": line["requested_factories"],
              "active_factories": line.get("active_factories"),
              "equipment_variant_id": object_id(line["equipment_variant_index"]),
              "equipment_name": equipment_variant_name(state, object_id(line["equipment_variant_index"]), country, names)}
             for line in many(own.get("production", {}).get("military_lines"))]
    data = {
        "schema_version": 1, "match_id": match_id, "turn_id": turn_id, "country": country,
        "date": state["date"], "observation_mode": "public_debug", "source": source,
        "state_fingerprint": country_fingerprint(state, country),
        "political_power": own.get("politics", {}).get("political_power"),
        "government": own.get("politics", {}).get("ruling_party"),
        "focus": saved_focus, "research": {"slot_count": own.get("research_slot"),
                                             "slots": technology.get("slots", {}),
                                             "completed": completed_tech},
        "military_production": lines, "construction": construction_queue(own),
        "owned_states": [{"id": int(sid), "name": names.get(f"STATE_{sid}", f"STATE_{sid}"),
                          "buildings": value.get("buildings", {})}
                         for sid, value in state.get("states", {}).items() if value.get("owner") == country],
        "divisions": [{"id": object_id(unit["id"]), "location": unit.get("location")}
                      for unit in many(own.get("units", {}).get("division"))],
        "candidates": {
            "focus": focus_candidates,
            "research": [{"id": key, "name": names.get(key, key), "year": item.get("year"),
                          "base_cost_units": item.get("cost"),
                          "ahead_years": max(0, (item.get("year") or 0) - int(state["date"].split(".")[0])),
                          "requires_ui_check": True}
                         for key, item in technology_index["techs"].items()
                         if tech.status(item, completed_tech, researching) == "available" and item.get("folders")],
            "construction_buildings": ["industrial_complex"]},
        "unavailable": {"available_manpower": "country manpower field not yet calibrated",
                        "free_building_slots": "requires game UI check",
                        "army_orders": "only calibrated ID/name-bound army actions are enabled"},
    }
    from army_actions import army_observation
    data["army"] = army_observation(state, country, army_context)
    data["divisions"] = data["army"]["divisions"]
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    data["observation_id"] = hashlib.sha256(encoded).hexdigest()
    return data


def validate_request(request, observed):
    """Validate the whole batch before any action can affect the game."""
    if not isinstance(request, dict):
        raise ValueError("Request must be an object")
    for key in ("match_id", "turn_id", "country", "date", "observation_id"):
        if type(request.get(key)) is not type(observed[key]) or request.get(key) != observed[key]:
            raise ValueError(f"Request does not match observation: {key}")
    actions = request.get("actions")
    if not isinstance(actions, list) or len(actions) > 6:
        raise ValueError("A turn must contain 0–6 actions")
    identifiers, targets = set(), set()
    schemas = {"research.start": {"slot", "technology_id"},
               "focus.select": {"focus_id"},
               "production.assign": {"line_id", "factories"},
               "construction.queue": {"state_id", "building_id", "count"},
               "army.move": {"army_id", "target_province_id"}, "army.attack": {"army_id", "target_province_id", "target_state_id"}, "army.stop": {"army_id"}}
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"id", "type", "args"}:
            raise ValueError("Action needs exactly id, type and args")
        identity, kind, args = action["id"], action["type"], action["args"]
        if not isinstance(identity, str) or not identity or len(identity) > 80 or identity in identifiers:
            raise ValueError("Action IDs must be nonempty and unique")
        if not isinstance(kind, str) or kind not in schemas or not isinstance(args, dict) or set(args) != schemas[kind]:
            raise ValueError("Unknown action or argument schema")
        identifiers.add(identity)
        if kind.startswith("army."):
            from army_actions import validate_army_action
            target = validate_army_action(action, observed)
        elif kind == "research.start":
            if type(args["slot"]) is not int or not 1 <= args["slot"] <= observed["research"]["slot_count"]:
                raise ValueError("Research slot is outside this country's range")
            if args["technology_id"] not in [item["id"] for item in observed["candidates"]["research"]]:
                raise ValueError("Technology is not an observed candidate")
            target = kind, args["slot"]
        elif kind == "focus.select":
            if args["focus_id"] not in [item["id"] for item in observed["candidates"]["focus"]]:
                raise ValueError("Focus is not an observed candidate")
            target = kind, "current"
        elif kind == "production.assign":
            line = next((row for row in observed["military_production"] if type(args["line_id"]) is int and row["id"] == args["line_id"]), None)
            if line is None or type(args["factories"]) is not int or args["factories"] < 0 or abs(args["factories"] - line["requested_factories"]) != 1:
                raise ValueError("Production must adjust a known line by exactly one factory")
            target = kind, args["line_id"]
        else:
            if type(args["state_id"]) is not int or args["state_id"] not in [row["id"] for row in observed["owned_states"]]:
                raise ValueError("Construction state is not owned by this country")
            if args["building_id"] not in observed["candidates"]["construction_buildings"] or type(args["count"]) is not int or args["count"] != 1:
                raise ValueError("Construction supports one civilian factory per action")
            target = kind, args["state_id"], args["building_id"]
        if target in targets:
            raise ValueError("Batch repeats an action target; observe again before repeating")
        targets.add(target)
    return actions


def execute_action(session, country, action, state):
    """Called only after batch validation; the caller must verify the resulting save."""
    from player_actions import research_start, focus_start, production_assign_one, construction_queue_one
    args, kind = action["args"], action["type"]
    own = state["countries"][country]
    if kind.startswith("army."):
        from army_actions import execute_army_action
        return execute_army_action(session, country, action, state)
    if kind == "research.start":
        return research_start(session, country, args["technology_id"], args["slot"], own["technology"])
    if kind == "focus.select":
        return focus_start(session, args["focus_id"], own)
    if kind == "production.assign":
        line = next(row for row in many(own["production"]["military_lines"]) if object_id(row["id"]) == args["line_id"])
        variant_id = object_id(line["equipment_variant_index"])
        from hoi4_live.gamedata import loc
        keys = [key for kind in state.get("equipments", {}) for key in (kind, f"{country}_{kind}", kind + "_short", f"{country}_{kind}_short")]
        names = loc.for_session(session).get_many(keys)
        name = equipment_variant_name(state, variant_id, country, names)
        if not name:
            raise ValueError("The production variant name is missing from the exact save")
        labels = list(dict.fromkeys([name, equipment_variant_name(state, variant_id, country, names, prefer_short=True)]))
        return production_assign_one(session, line["priority"] + 1, args["factories"], own["production"], expected_name=labels)
    if kind == "construction.queue":
        return construction_queue_one(session, country, args["state_id"], args["building_id"], state)
    raise ValueError("Unsupported action")


def verify_action(action, before, after, country):
    """Check the effect in exact saves, including gameplay isolation from peers."""
    args, kind = action["args"], action["type"]
    if kind.startswith("army."):
        from army_actions import verify_army_action
        return verify_army_action(action, before, after, country)
    first, last = before["countries"][country], after["countries"][country]
    observations = {}
    checks = {"same_date": before["date"] == after["date"],
              "peers_unchanged": all(before["countries"][tag].get(domain) == after["countries"][tag].get(domain)
                                     for tag in before["countries"] if tag != country
                                     for domain in ("focus", "technology", "production", "units"))}
    if kind == "research.start":
        checks["target_active"] = args["technology_id"] not in first["technology"]["slots"] and args["technology_id"] in last["technology"]["slots"]
        checks["not_instantly_researched"] = first["technology"]["technologies"].get(args["technology_id"], {}).get("level", 0) == last["technology"]["technologies"].get(args["technology_id"], {}).get("level", 0)
    elif kind == "focus.select":
        checks["target_current"] = not first["focus"].get("current") and last["focus"].get("current") == args["focus_id"]
        checks["not_instantly_completed"] = first["focus"].get("completed") == last["focus"].get("completed")
    elif kind == "production.assign":
        x = {object_id(row["id"]): row for row in many(first["production"]["military_lines"])}
        y = {object_id(row["id"]): row for row in many(last["production"]["military_lines"])}
        identity = args["line_id"]
        checks["target_factories"] = identity in y and y[identity]["requested_factories"] == args["factories"] and abs(x[identity]["requested_factories"] - args["factories"]) == 1
        checks["other_lines_requested_factories_unchanged"] = x.keys() == y.keys() and all(x[key]["requested_factories"] == y[key]["requested_factories"] for key in x if key != identity)
        checks["other_lines_equipment_unchanged"] = x.keys() == y.keys() and all(x[key]["equipment_variant_index"] == y[key]["equipment_variant_index"] for key in x if key != identity)
        checks["equipment_unchanged"] = x[identity]["equipment_variant_index"] == y.get(identity, {}).get("equipment_variant_index")
        observations["active_factory_changes"] = [{"line_id": key, "before": x[key].get("active_factories"), "after": y[key].get("active_factories")} for key in x.keys() & y.keys() if x[key].get("active_factories") != y[key].get("active_factories")]
    elif kind == "construction.queue":
        def target(entry):
            return entry.get("building", {}).get("template") == args["building_id"] and entry.get("building", {}).get("location") == args["state_id"]
        x, y = construction_queue(first), construction_queue(last)
        checks["one_level_added"] = sum(row.get("amount", 1) for row in y if target(row)) - sum(row.get("amount", 1) for row in x if target(row)) == 1
        checks["other_queue_entries_unchanged"] = [row for row in x if not target(row)] == [row for row in y if not target(row)]
        checks["not_instantly_built"] = all(value.get("buildings") == after["states"][sid].get("buildings") for sid, value in before["states"].items())
    else:
        raise ValueError("Unsupported action")
    return {"verified": all(checks.values()), "checks": checks, "observations": observations}


def export(save_path, output, match_id, turn_id, countries):
    from m0_probe import Probe, write_json
    from hoi4_live.gamedata import tech, focus, loc
    state = json.loads(save_path.read_text(encoding="utf-8"))
    probe = Probe(output / "backend", countries)
    try:
        technologies, focuses = tech.for_session(probe.session), focus.for_session(probe.session)
        keys = list(technologies["techs"]) + [f"STATE_{sid}" for sid in state.get("states", {})]
        for country in countries:
            keys += list(focus.tree_focuses(focuses, state["countries"][country].get("focus_tree")))
            keys += [key for kind in state.get("equipments", {}) for key in (kind, f"{country}_{kind}")]
        names = loc.for_session(probe.session).get_many(keys)
        source = {"kind": "parsed_text_save", "path": str(save_path.resolve()),
                  "sha256": hashlib.sha256(save_path.read_bytes()).hexdigest()}
        for country in countries:
            data = observation(state, country, match_id, turn_id, source, technologies, focuses, names)
            write_json(output / (country + ".json"), data)
            hold = {key: data[key] for key in ("match_id", "turn_id", "country", "date", "observation_id")}
            hold.update(actions=[], reason="Keep existing orders this turn")
            validate_request(hold, data)
            write_json(output / (country + ".hold.json"), hold)
            print(f"{country}: {len(data['candidates']['research'])} research candidates, {len(data['candidates']['focus'])} focus candidates")
    finally:
        probe.session.shutdown()


def main():
    import sys
    from m0_probe import ROOT, countries_from
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("observe")
    create.add_argument("--save", type=Path, required=True, help="Parsed exact-save JSON from an IRON probe")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--match", required=True)
    create.add_argument("--turn", type=int, required=True)
    check = sub.add_parser("validate")
    check.add_argument("--observation", type=Path, required=True)
    check.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "observe":
        if args.turn < 0:
            parser.error("turn must be nonnegative")
        countries = countries_from(json.loads((ROOT / "configs/match.example.json").read_text(encoding="utf-8")))
        # Initialise the pinned backend imports before export's gamedata imports.
        sys.path.insert(0, str(ROOT / ".deps" / "hoi4-live-tools"))
        export(args.save, args.output, args.match, args.turn, countries)
    else:
        observed = json.loads(args.observation.read_text(encoding="utf-8"))
        request = json.loads(args.request.read_text(encoding="utf-8"))
        actions = validate_request(request, observed)
        print(f"VALID: {len(actions)} action(s) for {request['country']}")


if __name__ == "__main__":
    main()
