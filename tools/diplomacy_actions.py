"""Normal UI declarations with exact-save goals and explicit war-state verification."""
import copy
import hashlib
import json
from pathlib import Path
import re
import time


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


def country_names(session, tag):
    from hoi4_live.gamedata import loc
    keys = [tag, tag + "_DEF"] + [tag + "_" + ideology + suffix for ideology in ("democratic", "fascism", "neutrality", "communism") for suffix in ("", "_DEF")]
    return sorted(set(value for value in loc.for_session(session).get_many(keys).values() if value and "[" not in value))


def state_id_from_tooltip(lines):
    found = {int(match[1]) for line in lines if (match := re.search(r"StateID[:：=]?(\d+)(?!\d)", re.sub(r"\s+", "", line), re.I))}
    return next(iter(found)) if len(found) == 1 else None


def open_country(session, target_country, state):
    """Left-click an exact capital state and its evidenced owner flag; no orders are issued."""
    from hoi4_live.game import console, mapview, panels, diplomacyview
    from hoi4_live.perception import tooltip
    from army_actions import clear_hover, ensure_debug_tooltips
    target = state["countries"].get(target_country, {})
    capital = target.get("capital")
    if type(capital) is not int or state.get("states", {}).get(str(capital), {}).get("owner") != target_country:
        raise ValueError("Target's exact saved capital is not an owned state")
    console.close_console(session)
    panels.to_map(session)
    session.input.key("f1")
    time.sleep(.4)
    camera = mapview.go_to_id(session, capital)
    ensure_debug_tooltips(session)
    console.close_console(session)
    width, height = session.window.size()
    selected, observed = None, []
    for dx, dy in ((0, 0), (30, 0), (-30, 0), (0, 30), (0, -30)):
        point = width // 2 + dx, height // 2 + dy
        clear_hover(session)
        tip, _, image = tooltip.read(session, *point)
        actual = state_id_from_tooltip(tip.get("lines", []))
        observed.append({"point": point, "state_id": actual, "lines": tip.get("lines", [])})
        if actual == capital:
            selected = point
            break
    if selected is None:
        raise RuntimeError("Capital state identity not confirmed: " + repr(observed))
    session.input.click(*selected)
    time.sleep(1.1)
    names = country_names(session, target_country)
    flag_evidence = []
    # Installed countrystateview.gui: lower_left show_position(-6,-655), owners(35,63).
    for point in ((55, height - 577), (110, height - 577), (55, height - 567)):
        clear_hover(session)
        tip, _, image = tooltip.read(session, *point)
        text = " ".join(tip.get("lines", []))
        flag_evidence.append({"point": point, "lines": tip.get("lines", [])})
        if any(norm(name) in norm(text) for name in names):
            session.input.click(*point)
            time.sleep(1.1)
            clear_hover(session)
            header = diplomacyview.read_header(session)
            if norm(header.get("name")) in {norm(name) for name in names}:
                return {"country": target_country, "capital_state_id": capital, "camera": camera,
                        "capital_identity": observed, "flag_evidence": flag_evidence, "header": header,
                        "screenshot": session.artifact(session.grab(), "iron_diplomacy_target")}
            raise RuntimeError("Diplomacy header is not the exact target: " + repr(header))
    raise RuntimeError("Target owner flag not evidenced: " + repr(flag_evidence))


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


def declare_war(session, country, target_country, wargoal_id, state, preview=False):
    """Select a real goal in the normal declaration dialog and submit only an enabled button."""
    if type(preview) is not bool:
        raise ValueError("preview must be boolean")
    if reciprocal_war(state, country, target_country):
        return {"input_path": "none", "declaration_sent": False, "ready_to_send": False,
                "reason": "already_at_war", "save_verification_required": True}
    goal = next((goal for goal in wargoals(state, country) if goal["id"] == wargoal_id and goal["target_country"] == target_country), None)
    if goal is None:
        raise ValueError("The original declaration goal no longer exists")
    from hoi4_live.game import diplomacyview, panels
    from hoi4_live.gamedata import loc
    from hoi4_live.perception import tooltip
    from army_actions import clear_hover
    from PIL import Image
    import numpy as np
    evidence = open_country(session, target_country, state)
    row = None
    width, height = session.window.size()

    def scroll_actions(notches):
        # Upstream ACTIONS_REGION extends to 1080p; this adapter renders at 720p.
        session.input.scroll(400, min(580, height - 90), notches)
        time.sleep(.6)
        clear_hover(session)

    scroll_actions(15)
    for _ in range(4):
        clear_hover(session)
        image = session.grab()
        lines = session.ocr.recognize(image, region=[265, 475, 270, image.shape[0] - 485], scale=2).lines
        hits = [line for line in lines if norm(line.text) in {"宣战", "declarewar"}]
        if len(hits) == 1:
            row = hits[0]
            mark = diplomacyview._mark(image, round(row.center[1]))
            y = round(row.center[1])
            background = image[max(0, y - 12):y + 12, 275:505].astype("int16")
            # Unilateral Declare War has no acceptance tick in the observed UI.
            green_row = background.size > 0 and float(background[..., 1].mean() - background[..., 0].mean()) > 3
            available = mark == "ok" or mark is None and green_row and panels.text_colour(image, row.box) == "white"
            if not available:
                from hoi4_live.perception import tooltip
                clear_hover(session)
                reason, _, _ = tooltip.read(session, round(row.center[0]), round(row.center[1]))
                screenshot = session.artifact(session.grab(), "iron_declaration_blocked")
                raise ValueError("Normal Declare War action is not visibly legal: " + repr(reason.get("lines", [])) + "; evidence=" + screenshot)
            evidence["declare_row"] = {"box": list(row.box), "mark": mark, "enabled_green_row": bool(green_row)}
            break
        scroll_actions(-4)
    if row is None:
        raise RuntimeError("Declare War row was not found uniquely")
    session.input.click(round(row.center[0]), round(row.center[1]))
    time.sleep(1.1)
    width, height = session.window.size()
    left, top = width // 2 - 263, height // 2 - 266

    def clear_modal_hover():
        # Rest on the modal's neutral frame, not a top-bar officer/spirit icon.
        session.input.move(left + 10, top + 10)
        time.sleep(.25)

    label = loc.for_session(session).get_many([goal["type"]]).get(goal["type"], goal["type"])
    clear_modal_hover()
    image = session.grab()
    lines = session.ocr.recognize(image, region=[left, top, 527, 532], scale=2).lines
    goals = [line for line in lines if norm(line.text) == norm(label) and left + 18 <= line.center[0] < left + 270 and top + 125 <= line.center[1] < top + 325]
    if len(goals) != 1:
        raise RuntimeError("Requested war goal not uniquely identified in the normal dialog")
    # The installed row container is 101px wide although its background/text is 202px.
    # Click the evidenced left part of that row, not the centred text at the hitbox edge.
    goal_point = left + 63, round(goals[0].center[1])
    goal_hover = None
    selected_before = selected_dialog_goal(lines, label, left, top)
    if not selected_before:
        goal_hover, _, _ = tooltip.read(session, *goal_point, wait=1.2)
        if not goal_hover.get("found") or norm(goal_hover.get("title")) != norm(label):
            raise RuntimeError("Requested war goal tooltip was not confirmed at its click point")
        session.input.click(*goal_point)
        time.sleep(.8)
    clear_modal_hover()
    image = session.grab()
    lines = session.ocr.recognize(image, region=[left, top, 527, 532], scale=2).lines
    labels = {norm(line.text) for line in lines}
    if not any("宣战" in value or "declarewar" in value for value in labels) or not selected_dialog_goal(lines, label, left, top):
        raise RuntimeError("Declaration dialog title or selected goal was not confirmed")
    call_allies = [line for line in lines if "盟友" in norm(line.text) or "allies" in norm(line.text)]
    ally_state = "not_present"
    if call_allies:
        frames = np.asarray(Image.open(Path(session.cfg.game_dir) / "gfx/interface/generic_checkbox.dds").convert("RGBA"))
        box = left + 340, top + 427
        checked = checkbox_state(image, frames, box)
        if checked is None:
            raise RuntimeError("Call Allies checkbox state is unknown")
        if checked:
            session.input.click(box[0] + 17, box[1] + 15)
            time.sleep(.3)
            clear_modal_hover()
            checked = checkbox_state(session.grab(), frames, box)
        if checked is not False:
            raise RuntimeError("Call Allies was not confirmed off")
        ally_state = "off"
    clear_modal_hover()
    image = session.grab()
    lines = session.ocr.recognize(image, region=[left + 340, top + 470, 145, 50], scale=2).lines
    send = [line for line in lines if norm(line.text) in {"提交", "send"}]
    if len(send) != 1:
        raise RuntimeError("Normal declaration Send button not identified")
    x, y, w, h = send[0].box
    crop = image[max(0, y - 6):y + h + 6, max(0, x - 30):x + w + 30].astype("int16")
    if crop.size == 0 or float(crop[..., 1].mean() - crop[..., 0].mean()) <= 3:
        from hoi4_live.perception import tooltip
        clear_hover(session)
        reason, _, _ = tooltip.read(session, round(send[0].center[0]), round(send[0].center[1]))
        screenshot = session.artifact(session.grab(), "iron_declaration_send_blocked")
        raise ValueError("Normal declaration Send button is not visibly enabled: " + repr(reason.get("lines", [])) + "; evidence=" + screenshot)
    evidence.update(wargoal_id=wargoal_id, wargoal_type=goal["type"], wargoal_name=label, goal_choice_point=list(goal_point), call_allies=ally_state,
                    goal_hover=goal_hover, goal_selection="already_selected" if selected_before else "selected_once",
                    dialog_screenshot=session.artifact(image, "iron_declaration_ready"), ready_to_send=True)
    if preview:
        return {**evidence, "input_path": "ordinary UI preview", "preview": True,
                "declaration_sent": False, "dialog_left_open": True, "save_verification_required": True}
    session.input.click(round(send[0].center[0]), round(send[0].center[1]))
    time.sleep(.8)
    return {**evidence, "input_path": "ordinary UI", "declaration_sent": True, "save_verification_required": True}


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
