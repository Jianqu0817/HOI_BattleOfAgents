"""Normal UI declarations with exact-save goals and explicit war-state verification."""
import hashlib
import json
import re


def many(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def object_id(value):
    values = many(value)
    return values[0].get("id") if values and isinstance(values[0], dict) else None


WAR_FIELDS = ("first", "second", "start_date", "first_casualties", "second_casualties")


def public_war_graph(state):
    """Merge the engine's one-sided shared records, without enemy units or private goals."""
    result = {}

    def add(value, owner=None, other=None):
        if not isinstance(value, dict):
            return
        first, second = value.get("first"), value.get("second")
        if any(not isinstance(tag, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{2}", tag) for tag in (first, second)) or first == second:
            return
        pair = tuple(sorted((first, second)))
        if owner is not None and set(pair) != {owner, other}:
            raise ValueError("A stored war relation names different country endpoints")
        projected = {key: value.get(key) for key in WAR_FIELDS}
        if pair in result and result[pair] != projected:
            raise ValueError("Conflicting public war records for " + " / ".join(pair))
        result[pair] = projected

    for value in many(state.get("public_wars")):
        add(value)
    for country, own in state.get("countries", {}).items():
        for other, relation in own.get("diplomacy", {}).get("active_relations", {}).items():
            if isinstance(relation, dict):
                for value in many(relation.get("war_relation")):
                    add(value, country, other)
    return result


def war_relation(state, country, other):
    return public_war_graph(state).get(tuple(sorted((country, other))))


def reciprocal_war(state, country, other):
    # HOI4 serializes the pair's shared block in one country's relations only.
    # The record must name both endpoints; two physical copies are not required.
    return war_relation(state, country, other) is not None


def war_pairs(state):
    return set(public_war_graph(state))


def faction_memberships(state):
    result = {}
    for faction in many(state.get("faction")):
        if not isinstance(faction, dict):
            continue
        members = faction.get("members", {})
        members = members.get("__items__", []) if isinstance(members, dict) else many(members)
        members = sorted(tag for tag in members if isinstance(tag, str))
        for tag in members:
            result[tag] = {"id": object_id(faction.get("id")), "name": faction.get("name"), "members": members}
    return result


def wargoals(state, country):
    result = []
    source = state.get("countries", {}).get(country, {}).get("diplomacy", {}).get("wargoals", {})
    if not isinstance(source, dict):
        return result
    for goal_type, values in source.items():
        for item in many(values):
            if not isinstance(item, dict) or item.get("wargoaldata_actor") != country:
                continue
            identity = object_id(item.get("id"))
            if type(identity) is not int or identity <= 0 or not isinstance(item.get("wargoaldata_recipient"), str):
                continue
            result.append({"id": identity, "type": item.get("type", goal_type), "target_country": item["wargoaldata_recipient"]})
    return result


def diplomacy_fingerprint(state):
    """Only the separately checked war/goal/alliance baseline, excluding diplomacy caches."""
    values = {"war_pairs": sorted(war_pairs(state)), "factions": faction_memberships(state),
              "goals": {tag: sorted(wargoals(state, tag), key=lambda row: row["id"]) for tag in state.get("countries", {})}}
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def diplomacy_observation(state, country, names=None):
    names = names or {}
    memberships = faction_memberships(state)
    own_goals = wargoals(state, country)
    candidates, blocked = [], []
    for other, target in state.get("countries", {}).items():
        if other == country:
            continue
        goals = [goal for goal in own_goals if goal["target_country"] == other]
        reason = None
        if "faction" not in state:
            reason = "faction_snapshot_unavailable"
        elif war_relation(state, country, other) or war_relation(state, other, country):
            reason = "already_at_war"
        elif memberships.get(country) and memberships.get(country) == memberships.get(other):
            reason = "same_faction"
        elif not goals:
            reason = "no_wargoal"
        elif type(target.get("capital")) is not int or state.get("states", {}).get(str(target["capital"]), {}).get("owner") != other:
            reason = "target_capital_owner_not_confirmed"
        unique = [goal for goal in goals if sum(item["type"] == goal["type"] for item in goals) == 1]
        if reason is None and not unique:
            reason = "ambiguous_same_type_wargoals"
        if reason:
            blocked.append({"country": other, "reason": reason})
            continue
        candidates.append({"country": other, "name": names.get(other, other), "capital_state_id": target["capital"],
                           "wargoals": [{**goal, "name": names.get(goal["type"], goal["type"])} for goal in unique],
                           "faction": memberships.get(other), "requires_ui_check": True,
                           "notice": "A saved goal is not proof the normal Declare War button is enabled. Allies/guarantees may widen the war."})
    return {"baseline_sha256": diplomacy_fingerprint(state), "faction_data_available": "faction" in state,
            "own_faction": memberships.get(country), "wargoals": own_goals,
            "wars": [{"enemy": pair[1] if pair[0] == country else pair[0], "since": value.get("start_date")}
                     for pair, value in public_war_graph(state).items() if country in pair],
            "action_options": {"diplomacy.declare_war": {"targets": candidates}} if candidates else {},
            "blocked_targets": blocked, "justify_supported": False}


def validate_declaration(action, observed):
    args = action["args"]
    if action["type"] != "diplomacy.declare_war" or set(args) != {"target_country", "wargoal_id"}:
        raise ValueError("Declare War needs exactly target_country and wargoal_id")
    target = args["target_country"]
    if not isinstance(target, str) or target == observed["country"] or type(args["wargoal_id"]) is not int:
        raise ValueError("Invalid war target or goal ID")
    menu = observed.get("diplomacy", {}).get("action_options", {}).get("diplomacy.declare_war", {})
    row = next((row for row in menu.get("targets", []) if row["country"] == target), None)
    if row is None or args["wargoal_id"] not in [goal["id"] for goal in row["wargoals"]]:
        raise ValueError("War target and goal were not offered by this observation")
    return "diplomacy.declare_war", target


def norm(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value or "").casefold())


def selected_dialog_goal(lines, label, left, top):
    """The right goal pane is positive selection evidence; the left list is not."""
    right = [line for line in lines if left + 270 <= line.center[0] <= left + 510
             and top + 125 <= line.center[1] < top + 325]
    values = [norm(line.text) for line in right]
    return not any("未选择战争目标" in value or "nowargoalselected" in value for value in values) and values.count(norm(label)) == 1


def state_id_from_tooltip(lines):
    found = {int(match[1]) for line in lines if (match := re.search(r"StateID[:：=]?(\d+)(?!\d)", re.sub(r"\s+", "", line), re.I))}
    return next(iter(found)) if len(found) == 1 else None


def checkbox_state(image, frames, point):
    """Compare the installed two-frame checkbox texture; unknown is never assumed unchecked."""
    import numpy as np
    x, y = point
    frame_h, frame_w = frames.shape[0], frames.shape[1] // 2
    scores = []
    for index in range(2):
        frame = frames[:, index * frame_w:(index + 1) * frame_w]
        mask = frame[..., 3] >= 250
        if int(mask.sum()) < 10:
            return None
        crop = image[y:y + frame_h, x:x + frame_w, :3]
        if crop.shape[:2] != frame.shape[:2]:
            return None
        scores.append(float(np.abs(crop.astype("float32") - frame[..., :3].astype("float32"))[mask].mean()) / 255)
    if min(scores) > .18 or abs(scores[0] - scores[1]) < .012:
        return None
    return scores.index(min(scores)) == 1


def verify_declaration(action, before, after, country):
    from agent_contract import country_fingerprint
    target = action["args"]["target_country"]
    goal_id = action["args"]["wargoal_id"]
    earlier = reciprocal_war(before, country, target)
    first_pairs, last_pairs = war_pairs(before), war_pairs(after)
    added = last_pairs - first_pairs
    goals = [goal for goal in wargoals(before, country) if goal["id"] == goal_id and goal["target_country"] == target]
    memberships = faction_memberships(before)
    related = {country, target} | set(memberships.get(country, {}).get("members", [])) | set(memberships.get(target, {}).get("members", []))
    checks = {"same_date": before["date"] == after["date"], "public_war_relation": reciprocal_war(after, country, target),
              "goal_existed_or_already_at_war": earlier or len(goals) == 1,
              "no_existing_wars_removed": first_pairs <= last_pairs,
              "war_changes_related": all(country in pair or target in pair or set(pair) <= related for pair in added),
              "peers_unchanged": all(country_fingerprint(before, tag) == country_fingerprint(after, tag) for tag in before["countries"] if tag != country),
              "own_orders_economy_unchanged": country_fingerprint(before, country) == country_fingerprint(after, country)}
    if earlier:
        checks["no_new_declaration"] = first_pairs == last_pairs
    else:
        checks["new_target_war"] = tuple(sorted((country, target))) in added
    verified = all(checks.values())
    return {"verified": verified, "effect_status": "already_present" if verified and earlier else "full" if verified else "none",
            "checks": checks, "observations": {"actor": country, "target_country": target, "wargoal_id": goal_id,
                "wargoal_type": goals[0]["type"] if goals else None, "new_war": verified and not earlier,
                "declaration_sent": verified and not earlier, "war_pairs_added": [list(pair) for pair in sorted(added)],
                "additional_opponents": sorted({other for pair in added if country in pair for other in pair if other not in {country, target}}),
                "declaration_status": "already_at_war_before_action" if earlier else "new_declaration" if verified else "not_verified"}}


def retained_declaration(action, checkpoint, current, country):
    return reciprocal_war(current, country, action["args"]["target_country"])
