"""Pure adapter from frozen NativeGame snapshots to the existing country-agent contract."""
from __future__ import annotations

import copy
import hashlib
import json
import re

from native_turn import FIELDS, SCHEMA, validate_plan
from showrunner import check_history

IDENTITY = ("match_id", "turn_id", "country", "date", "observation_id")


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _rows(snapshots, key, country, field="units"):
    value = snapshots.get(key, {})
    if not isinstance(value, dict) or value.get("country", country) != country or value.get("ok", True) is not True:
        raise ValueError("Snapshot belongs to another country or failed: " + key)
    rows = value.get(field, [])
    if not isinstance(rows, list):
        raise ValueError("Snapshot rows must be a list: " + key)
    id_key = "unit_id" if key == "divisions" else "id"
    if field == "units" and len({row[id_key] for row in rows}) != len(rows):
        raise ValueError("Ambiguous native unit identity: " + key)
    return rows


def build_observation(tag, snapshots, match_id, turn_id, source, state=None,
                      map_context=None, history=None):
    """Build bounded candidates without reading or changing the running game.

    `snapshots` contains control, divisions, air, navy, optional goals/diplomacy.
    Parsed ownership is usable only with an explicitly matching state_date_raw.
    All returned candidate actions are exact {id,type,args} choices, not schemas.
    """
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Z]{3}", tag):
        raise ValueError("Invalid country")
    control = snapshots.get("control", snapshots.get("status", {}))
    control = control.get("state", control)
    if control.get("paused") is not True or type(control.get("date_raw")) is not int:
        raise ValueError("Observation requires a frozen native date")
    if control.get("ai_enabled") is not False:
        raise ValueError("Native AI must be disabled before agent observation")
    if control.get("country", tag) != tag:
        raise ValueError("Control snapshot belongs to another country")
    if not isinstance(match_id, str) or not match_id or type(turn_id) is not int or turn_id < 1:
        raise ValueError("Invalid match/turn identity")
    recent = copy.deepcopy(history or [])
    check_history(recent, tag)
    divisions = _rows(snapshots, "divisions", tag)
    air = _rows(snapshots, "air", tag)
    navy = _rows(snapshots, "navy", tag)
    menu = {kind: [] for kind in FIELDS}

    def offer(kind, **args):
        if len(menu[kind]) < 2:
            suffix = kind.replace(".", "_")
            menu[kind].append({"id": f"{turn_id}-{tag}-{suffix}-{len(menu[kind]) + 1}",
                               "type": kind, "args": args})

    fresh_state = state is not None and snapshots.get("state_date_raw") == control["date_raw"]
    own = (state or {}).get("countries", {}).get(tag, {}) if fresh_state else {}
    wars = []
    if fresh_state:
        from army_actions import own_wars
        wars = own_wars(own, tag)
        known = {row["enemy"] for row in wars}
        for relation in state.get("public_wars", []):
            pair = (relation["first"], relation["second"])
            if tag in pair:
                enemy = pair[1] if pair[0] == tag else pair[0]
                if enemy != tag and enemy not in known:
                    wars.append({"enemy": enemy, "since": relation.get("start_date")})
                    known.add(enemy)
    context = map_context or {}
    if fresh_state and context.get("graph") and context.get("map"):
        from army_actions import _controller, _passable
        allowed = {tag} | {war["enemy"] for war in wars}
        for unit in sorted(divisions, key=lambda row: row["unit_id"]):
            origin = unit["province"]
            if unit.get("path") or not _passable(origin, context):
                continue
            targets = sorted(context["graph"].get(str(origin), []),
                             key=lambda target: (_controller(state, target, context["map"]) == tag, target))
            for target in targets:
                if target != origin and _passable(target, context) and _controller(state, target, context["map"]) in allowed:
                    offer("division.move", unit_id=unit["unit_id"], source=origin, target=target)
                    break
            if len(menu["division.move"]) >= 2:
                break
    # Region reachability has not been proven for arbitrary target regions. Keep
    # existing assignments and offer only supported mission changes on them.
    for wing in sorted(air, key=lambda row: row["id"]):
        if not wing.get("region"):
            continue
        for bit in (1, 2, 4):
            if wing["capability_mask"] & bit and not wing["current"] & bit:
                offer("air.mission", wing_id=wing["id"], expected_mask=wing["current"],
                      mission_bit=bit, enabled=True)
                break
    for fleet in sorted(navy, key=lambda row: row["id"]):
        if fleet.get("regions"):
            target = 1 if fleet["current"] != 1 else 2
            offer("navy.mission", task_force_id=fleet["id"],
                  expected_type=fleet["current"], mission_type=target)
    for recipient, relation in sorted(snapshots.get("diplomacy", {}).items()):
        if relation.get("at_war") or relation.get("actor_index") != control.get("country_index"):
            continue
        for goal in relation.get("goals", []):
            if (goal.get("available") and goal.get("actor_index") == control.get("country_index")
                    and goal.get("recipient_index") == relation.get("recipient_index")):
                offer("diplomacy.declare_war", recipient=recipient, goal_handle=goal["handle"])
    politics = own.get("politics", {})
    data = {
        "schema_version": 1, "match_id": match_id, "turn_id": turn_id, "country": tag,
        "date": str(snapshots.get("date", control["date_raw"])), "date_raw": control["date_raw"],
        "execution_backend": "native", "source": source, "observation_mode": "native_country",
        "political_power": politics.get("political_power"), "government": politics.get("ruling_party"),
        "focus": own.get("focus", {}), "research": {"visible_slots": []},
        "military_production": [], "construction": [], "owned_states": [],
        "divisions": [{"id": row["unit_id"], "location": row["province"], "path": row.get("path", [])}
                      for row in divisions],
        "army": {"wars": wars, "division_count": len(divisions)},
        "diplomacy": {"wars": wars},
        "naval_air": {"air_wings": [{key: row[key] for key in ("id", "current", "capability_mask", "region")} for row in air],
                      "task_forces": [{key: row[key] for key in ("id", "current", "fleet_id", "regions")} for row in navy]},
        "action_options": menu, "recent_history": recent[-2:],
        "unavailable": {"economy": "Native economy commands are not exposed in this adapter",
                        "regions": "Arbitrary region reachability is not verified",
                        "ownership": None if fresh_state else "No same-date parsed ownership snapshot"},
    }
    data["state_fingerprint"] = _digest({key: data[key] for key in ("date_raw", "divisions", "naval_air", "action_options")})
    data["observation_id"] = _digest(data)
    return data


def validate_request(request, observed):
    """Only exact published candidates can reach the native executor."""
    if not isinstance(request, dict) or set(request) != set(IDENTITY) | {"actions", "reason"}:
        raise ValueError("Request requires exactly identity, actions and reason")
    for key in IDENTITY:
        if type(request.get(key)) is not type(observed[key]) or request[key] != observed[key]:
            raise ValueError("Request does not match observation: " + key)
    if observed.get("execution_backend") != "native":
        raise ValueError("Expected native observation")
    candidate = dict(observed); claimed = candidate.pop("observation_id")
    if _digest(candidate) != claimed:
        raise ValueError("Observation content changed after identity was frozen")
    actions = request["actions"]
    if not isinstance(actions, list) or len(actions) > 6 or not isinstance(request["reason"], str):
        raise ValueError("Request permits a reason and zero to six actions")
    guards, identifiers = set(), set()
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"id", "type", "args"}:
            raise ValueError("Action requires exactly id, type and args")
        kind = action["type"]
        if (not isinstance(kind, str) or kind not in FIELDS
                or _digest(action) not in {_digest(row) for row in observed["action_options"][kind]}):
            raise ValueError("Action is not an exact observed native candidate")
        args = action["args"]
        identity = args.get("unit_id", args.get("wing_id", args.get("task_force_id", args.get("recipient"))))
        guard = kind, identity
        if guard in guards or action["id"] in identifiers:
            raise ValueError("Duplicate action or guarded native field")
        guards.add(guard); identifiers.add(action["id"])
    return actions


def to_native_plan(requests, observations, *, step_hours=24, label="native-model-turn", save_basename=None):
    """Translate validated sealed requests; ordering follows observations."""
    if set(requests) != set(observations):
        raise ValueError("Requests and observations must cover the same countries")
    identities = {(value["match_id"], value["turn_id"], value["date_raw"]) for value in observations.values()}
    if len(identities) != 1:
        raise ValueError("Countries do not share a frozen native turn")
    countries = []
    for tag, observed in observations.items():
        if observed["country"] != tag:
            raise ValueError("Observation key does not match country")
        actions = validate_request(requests[tag], observed)
        countries.append({"tag": tag, "actions": [{"id": row["id"], "kind": row["type"], **row["args"]} for row in actions]})
    plan = {"schema": SCHEMA, "label": label, "step_hours": step_hours, "disable_ai": True, "countries": countries}
    if save_basename is not None:
        plan["save_basename"] = save_basename
    return validate_plan(plan)
