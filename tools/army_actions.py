"""Own-army observations and calibrated ordinary movement for the Chinese UI profile."""
import copy
from collections import deque
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "configs/army.local.json"
ORDER_FIELDS = {"path", "move_priority", "movement_progress", "execute_order", "order_move_progress", "support_attack", "last_combat_date"}


def many(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def ident(value):
    return many(value)[0].get("id") if many(value) and isinstance(many(value)[0], dict) else None


def items(value):
    return value.get("__items__", []) if isinstance(value, dict) else many(value)


def divisions(country):
    return {ident(row.get("id")): row for row in many(country.get("units", {}).get("division"))
            if isinstance(row, dict) and type(ident(row.get("id"))) is int}


def armies(country):
    return {ident(row.get("id")): row for theatre in many(country.get("theatres", {}).get("theatre"))
            for row in many(theatre.get("orders_group"))
            if isinstance(row, dict) and type(ident(row.get("id"))) is int}


def members(army):
    return [ident(row.get("unit")) for row in many(army.get("member")) if isinstance(row, dict)]


def army_structure(country):
    # Front caches may be rebuilt when changing player country; these are actual orders.
    return {str(key): {field: value.get(field) for field in ("name", "member", "order_instance", "execution_type")}
            for key, value in armies(country).items()}


def army_activity(country):
    """Additional current activity checks, without changing historical fingerprints."""
    return {str(identity): {"training": bool(group.get("training", False)),
                            "stop_training_at_max_xp": bool(group.get("stop_training_at_max_xp", False))}
            for identity, group in armies(country).items()}


def load_profile(path=DEFAULT_PROFILE):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 1, "countries": {}}


@lru_cache(maxsize=8)
def _file_digest(path, size, modified_ns):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ui_profile_identity(session):
    from hoi4_live.gamedata import scriptdb
    executable = Path(session.cfg.game_dir) / "hoi4.exe"
    stat = executable.stat()
    mods = scriptdb.mods_for(session)
    layouts = [path for path in scriptdb.effective_files("interface", session.cfg.game_dir, mods, ext=(".gui",))
               if Path(path).name.lower() in ("unitview.gui", "leadergroups.gui")]
    layout_digest = hashlib.sha256()
    for path in layouts:
        layout_digest.update(Path(path).name.encode("utf-8"))
        layout_digest.update(Path(path).read_bytes())
    fonts = scriptdb.effective_files("gfx/fonts", session.cfg.game_dir, mods, ext=(".fnt", ".dds", ".ttf", ".otf"), recursive=True)
    return {"adapter_version": 1, "exe_sha256": _file_digest(str(executable), stat.st_size, stat.st_mtime_ns),
            "render_size": session.cfg.data.get("iron_profile", {}).get("render_size", [1280, 720]),
            "ocr_language": session.cfg.data.get("ocr", {}).get("language"),
            "layout_sha256": layout_digest.hexdigest(), "font_signature": scriptdb.signature(fonts, "iron-army-fonts-1")}


def verified_operations(profile, identity):
    result = []
    for operation, entry in profile.get("accepted_operations", {}).items():
        if operation not in ("army.move", "army.stop", "army.attack") or entry.get("ui_profile") != identity:
            continue
        path = Path(entry.get("evidence", ""))
        if not path.is_file():
            continue
        try:
            proof = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        checks = proof.get("verification", {}).get("checks", {})
        if (proof.get("operation") == "probe" and proof.get("status") == "verified" and proof.get("verified") is True
                and proof.get("ui_profile") == identity and proof.get("action", {}).get("type") == operation
                and proof.get("verification", {}).get("verified") is True and checks
                and all(value is True for value in checks.values())
                and proof.get("action", {}).get("args", {}).get("army_id") == proof.get("army_id")
                and proof.get("verification", {}).get("observations", {}).get("members")):
            result.append(operation)
    return sorted(result)


def land_graph(session):
    """Pixel-edge land adjacency from the same installed bitmap as the map index."""
    from hoi4_live.gamedata import mapdata, scriptdb
    paths = [str(Path(session.cfg.game_dir) / "map" / name) for name in ("provinces.bmp", "definition.csv")]
    signature = scriptdb.signature(paths, "iron-land-1")
    path = Path(scriptdb.cache_dir()) / ("iron_land_" + signature + ".json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    colours, kinds = mapdata._province_colours(session.cfg.game_dir)
    _, _, bitmap = mapdata._centroids(session.cfg.game_dir, colours)
    graph = {}
    for a, b in mapdata._province_edges(bitmap):
        if kinds.get(a) == kinds.get(b) == "land":
            graph.setdefault(str(a), []).append(b)
            graph.setdefault(str(b), []).append(a)
    graph = {key: sorted(value) for key, value in graph.items()}
    path.write_text(json.dumps(graph), encoding="utf-8")
    return graph


@lru_cache(maxsize=2)
def _passability_files(paths, signature):
    from hoi4_live.gamedata import scriptdb
    result = {}
    for path in paths:
        try:
            parsed = scriptdb.parse_file(path)
        except (OSError, ValueError, KeyError):
            continue
        for state in scriptdb.as_list(parsed.get("state")):
            if not isinstance(state, dict) or type(state.get("id")) is not int:
                continue
            flag = state.get("impassable")
            if flag in (None, "no", False):
                result[str(state["id"])] = True
            elif flag in ("yes", True):
                result[str(state["id"])] = False
    return result


def static_passability(session):
    from hoi4_live.gamedata import scriptdb
    paths = tuple(scriptdb.effective_files("history/states", session.cfg.game_dir, scriptdb.mods_for(session)))
    return _passability_files(paths, scriptdb.signature(paths, "iron-state-passability-1"))


def context_for_session(session):
    from hoi4_live.gamedata import mapdata, loc, scriptdb
    path = Path(session.cfg.data.get("iron_army_profile", DEFAULT_PROFILE))
    profile, identity = load_profile(path), ui_profile_identity(session)
    # The pinned backend's bitmap projector reads the base map. Do not claim a
    # compatible map when a loaded total conversion replaces these files.
    replaced = [value for value in scriptdb.effective_files("map", session.cfg.game_dir, scriptdb.mods_for(session), ext=(".bmp", ".csv"))
                if Path(value).name.lower() in ("provinces.bmp", "definition.csv")
                and Path(value).resolve().parent != (Path(session.cfg.game_dir) / "map").resolve()]
    if replaced:
        return {"map": {}, "graph": {}, "profile": profile, "ui_profile": identity, "operations": [], "map_unavailable": "Loaded map bitmap replacement needs separate projector acceptance"}
    index = mapdata.for_session(session)
    passability = static_passability(session)
    unknown = sorted(int(sid) for sid in index["states"] if str(sid) not in passability)
    return {"map": index, "graph": land_graph(session), "profile": profile, "ui_profile": identity,
            "operations": verified_operations(profile, identity),
            "passability": passability, "passability_available": not unknown, "passability_unknown_states": unknown,
            "state_names": loc.for_session(session).get_many(["STATE_" + str(sid) for sid in index["states"]])}


def _controller(state, province, index):
    if "provinces" not in state:
        return None
    sid = index.get("provinces", {}).get(str(province), {}).get("state")
    value = state.get("states", {}).get(str(sid), {})
    return state["provinces"].get(str(province), {}).get("controller", value.get("owner"))


def _passable(province, context):
    sid = context.get("map", {}).get("provinces", {}).get(str(province), {}).get("state")
    return context.get("passability", {}).get(str(sid)) is True


def own_wars(own, country, state=None):
    from diplomacy_actions import public_war_graph
    result = []
    for pair, war in public_war_graph(state if state is not None else {"countries": {country: own}}).items():
        if country in pair:
            result.append({"enemy": war["second"] if war["first"] == country else war["first"],
                           "since": war.get("start_date"),
                           "our_casualties": war.get("first_casualties" if war["first"] == country else "second_casualties")})
    return sorted(result, key=lambda row: row["enemy"])


def own_active_battles(state, country):
    owned = {identity for identity, unit in divisions(state["countries"][country]).items()
             if unit.get("logical_country", country) == country}
    result = []
    for battle in many(state.get("combat", {}).get("land_combat")):
        if not isinstance(battle, dict):
            continue
        for side in ("attacker", "defender"):
            participating = sorted({ident(reference) for row in many(battle.get(side))
                                    if isinstance(row, dict) for reference in many(row.get("unit"))} & owned)
            if participating:
                result.append({"battle_id": ident(battle.get("id")), "province_id": battle.get("location"),
                               "role": side, "own_division_ids": participating,
                               "duration_raw": battle.get("duration"), "day_raw": battle.get("day")})
    return result


def movement_targets(state, country, army, context, limit=8, attack=False):
    index, graph = context.get("map", {}), context.get("graph", {})
    own = divisions(state["countries"][country])
    ids = members(army)
    if not ids or len(set(ids)) != len(ids) or any(identity not in own or own[identity].get("logical_country", country) != country for identity in ids):
        return []
    origins = {own[identity].get("location") for identity in ids}
    if None in origins or any(not _passable(origin, context) for origin in origins):
        return []
    distances = []
    for origin in origins:
        found, queue = {origin: 0}, deque([origin])
        while queue:
            current = queue.popleft()
            if found[current] >= 12:
                continue
            for neighbour in graph.get(str(current), []):
                if neighbour not in found and _passable(neighbour, context) and _controller(state, neighbour, index) == country:
                    found[neighbour] = found[current] + 1
                    queue.append(neighbour)
        distances.append(found)
    if attack:
        enemies = {row["enemy"] for row in own_wars(state["countries"][country], country, state)}
        candidates = []
        for found in distances:
            candidate = {}
            for current, distance in found.items():
                for neighbour in graph.get(str(current), []):
                    if _passable(neighbour, context) and _controller(state, neighbour, index) in enemies:
                        candidate[neighbour] = min(candidate.get(neighbour, 1000), distance + 1)
            candidates.append(candidate)
        common = set.intersection(*(set(row) for row in candidates))
        distances = candidates
    else:
        common = set.intersection(*(set(row) for row in distances))
    # A target must be reachable on friendly land from every member, without sea transport.
    targets = sorted((pid for pid in common if any(pid != origin for origin in origins)
                      and (attack or _controller(state, pid, index) == country)),
                     key=lambda pid: (max(row[pid] for row in distances), sum(row[pid] for row in distances), pid))
    result = []
    for pid in targets[:limit]:
        province = index["provinces"].get(str(pid), {})
        result.append({"province_id": pid, "state_id": province.get("state"),
                       "state_name": context.get("state_names", {}).get("STATE_" + str(province.get("state"))),
                       "controller": _controller(state, pid, index), "max_land_steps": max(row[pid] for row in distances),
                       "controller_resolution": "province_override_then_state_owner"})
    return result


def army_observation(state, country, context=None):
    context = context or {}
    own, index = state["countries"][country], context.get("map", {})
    units, groups = divisions(own), armies(own)
    observed_units = []
    for identity, unit in sorted(units.items()):
        if unit.get("logical_country", country) != country:
            continue
        manpower = unit.get("army_manpower", {})
        def amount(key):
            value = manpower.get(key, {}).get("value", {})
            return value.get("value") if value.get("tag") == country else None
        name = unit.get("division_name", {}).get("override")
        observed_units.append({"id": identity, "name": name or ("division #" + str(identity)),
                               "name_is_fallback": not bool(name), "location": unit.get("location"),
                               "state_id": index.get("provinces", {}).get(str(unit.get("location")), {}).get("state"),
                               "organisation": unit.get("organisation"), "strength_raw": unit.get("strength"),
                               "manpower": amount("army_manpower_value"), "needed_manpower": amount("army_manpower_need"),
                               "supply_ratio_raw": unit.get("army_current_supply_ratio"),
                               "path": items(unit.get("path")), "move_priority": unit.get("move_priority"),
                               "last_combat_date": unit.get("last_combat_date"),
                               "executing_plan": bool(unit.get("execute_order", 0))})
    profiles = context.get("profile", {}).get("countries", {}).get(country, {})
    group_rows, menu = [], {}
    for identity, group in sorted(groups.items()):
        ids = members(group)
        profile = profiles.get(str(identity), {})
        bound = profile.get("name") == group.get("name") and bool(group.get("name")) and "\ufffd" not in group["name"]
        targets = movement_targets(state, country, group, context) if bound else []
        attack_targets = movement_targets(state, country, group, context, attack=True) if bound else []
        row = {"id": identity, "name": group.get("name"), "division_ids": ids,
               "order_types": [order.get("type") for order in many(group.get("order_instance"))],
               "training": bool(group.get("training", False)),
               "stop_training_at_max_xp": bool(group.get("stop_training_at_max_xp", False)),
               "bound": bound, "movement_candidates": targets, "attack_candidates": attack_targets}
        group_rows.append(row)
        for action in ("army.move", "army.stop", "army.attack"):
            if bound and action in context.get("operations", []) and ids and all(i in units for i in ids):
                options = attack_targets if action == "army.attack" else targets
                if action in ("army.move", "army.attack") and not options:
                    continue
                menu.setdefault(action, {"armies": []})["armies"].append(
                    {"id": identity, "name": group["name"], "division_ids": ids,
                     **({"targets": options} if action in ("army.move", "army.attack") else {})})
    wars = own_wars(own, country, state)
    return {"divisions": observed_units, "armies": group_rows, "wars": wars, "action_options": menu,
            "active_battles": own_active_battles(state, country), "combat_observation_available": "combat" in state,
            "visibility": "own units only; own war relations; public province control; no enemy divisions",
            "province_control_available": "provinces" in state,
            "passability_observation_available": context.get("passability_available", False),
            "unknown_passability_states": context.get("passability_unknown_states", []),
            "unavailable": {"enemy_units": "not observed", "front_plans": "not live accepted",
                            **({"active_battles": "fresh save with combat block required"} if "combat" not in state else {}),
                            **({"land_passability": "static state passability is incomplete; unknown states excluded"} if not context.get("passability_available", False) else {}),
                            **({"province_control": "fresh save with provinces block required"} if "provinces" not in state else {})}}


def validate_army_action(action, observed):
    kind, args = action["type"], action["args"]
    schema = {"army.move": {"army_id", "target_province_id"}, "army.attack": {"army_id", "target_province_id", "target_state_id"}, "army.stop": {"army_id"}}
    if kind not in schema or not isinstance(args, dict) or set(args) != schema[kind] or any(type(value) is not int or value <= 0 for value in args.values()):
        raise ValueError("Invalid ordinary army action schema")
    menu = observed.get("army", {}).get("action_options", {}).get(kind, {})
    group = next((row for row in menu.get("armies", []) if row["id"] == args["army_id"]), None)
    own = {row["id"] for row in observed.get("army", {}).get("divisions", [])}
    if not group or not group["division_ids"] or len(set(group["division_ids"])) != len(group["division_ids"]) or any(identity not in own for identity in group["division_ids"]):
        raise ValueError("Army is foreign, ambiguous, or has no live acceptance")
    if kind in ("army.move", "army.attack") and args["target_province_id"] not in [row["province_id"] for row in group.get("targets", [])]:
        raise ValueError("Target province is outside the observed friendly land routes")
    if kind == "army.attack" and not any(row["province_id"] == args["target_province_id"] and row["state_id"] == args["target_state_id"] for row in group["targets"]):
        raise ValueError("Attack province/state identity does not match the installed map")
    return "army", args["army_id"]


def debug_response_state(results):
    text = "\n".join(line for entry in results for line in entry.get("response", []))
    matches = re.findall(r"debug\s+(ENABLED|DISABLED)", text, flags=re.I)
    return matches[-1].lower() if matches else None


def tooltip_army_name(lines, known_names):
    """Exact source names, with only spacing and a displayed unit-count suffix removed."""
    normal = lambda text: re.sub(r"\s+", "", text or "").casefold()
    first = next((normal(line) for line in lines if line.strip()), "")
    if re.search(r"create|newarmy|创建|新建|新集团军|新军团", first, re.I):
        return None
    found = set()
    for line in lines:
        title = re.sub(r"\s*[(（].*?[)）]\s*$", "", line)
        title = re.sub(r"\s*\d+\s*(?:divisions?|个?师)\s*$", "", title, flags=re.I)
        for name in known_names:
            if name and normal(title) == normal(name):
                found.add(name)
    return next(iter(found)) if len(found) == 1 else None


def existing_army_tooltip(lines):
    compact = [re.sub(r"\s+", "", line).casefold() for line in lines if line.strip()]
    if not compact or re.search(r"create|newarmy|创建|新建|新集团军|新军团", compact[0], re.I):
        return False
    select = any(re.fullmatch(r"[0o○]?(?:点)?击选中[。.!！]?|clicktoselect[.!]?", line, re.I) for line in compact)
    # This is the observed existing-army explanation; army-group and '+' tips
    # have different text. A lost numeric title is not used as identity evidence.
    army_help = any("创建集团军之后" in line for line in compact)
    return select and army_help


def existing_army_tooltip_frame(session, tip, image):
    lines = tip.get("lines", [])
    if existing_army_tooltip(lines):
        return True, lines
    # The differential box can select a flashing UI element instead of the
    # tooltip. Read the same captured frame; do not change hover or infer a name.
    lines = [line.text for line in session.ocr.recognize(image, scale=1.5).lines]
    return existing_army_tooltip(lines), lines


def theatre_shield_points(image):
    """Hover candidates from observed coloured shield rows in the calibrated 720p sidebar."""
    if image.shape[:2] != (720, 1280):
        return []
    left, right, top, bottom = 968, 1230, 130, 620
    pixels = image[top:bottom, left:right].astype("int16")
    coloured = (pixels.max(2) - pixels.min(2) > 65) & (pixels.max(2) > 65)
    counts = coloured.sum(1)
    rows, start = [], None
    for offset in range(len(counts) + 1):
        active = offset < len(counts) and counts[offset] >= 12
        if active and start is None:
            start = offset
        elif not active and start is not None:
            # A single native shield can supply only ~12 coloured pixels in
            # one scanline. Its sprite is 32px tall; larger bands are map/UI.
            if 12 <= offset - start <= 32:
                rows.append(top + start + min(10, (offset - start) // 2))
            start = None
    # Most theatre rows contain one army at the left. Try that column in every
    # row before walking the empty area to the right of the first shield.
    return [(x, y) for x in range(left + 5, right, 6) for y in rows]


def retained_acceptance(previous, country, army_id, before_group, after_group):
    result = {"accepted_actions": [], "acceptance_evidence": {}}
    if previous.get("name") != before_group.get("name") or members(before_group) != members(after_group):
        return result
    for action in previous.get("accepted_actions", []):
        path = Path(previous.get("acceptance_evidence", {}).get(action, ""))
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (record.get("verified") is True and record.get("country") == country and record.get("army_id") == army_id
                and record.get("action", {}).get("type") == action
                and record.get("action", {}).get("args", {}).get("army_id") == army_id
                and record.get("verification", {}).get("verified") is True
                and record.get("verification", {}).get("observations", {}).get("members") == members(before_group)):
            result["accepted_actions"].append(action)
            result["acceptance_evidence"][action] = str(path)
    return result


def validate_creation_selection(state, country, province_id, division_ids, from_army_id=None):
    own = divisions(state["countries"][country])
    groups = armies(state["countries"][country])
    assigned = {identity for group in groups.values() for identity in members(group)}
    if (type(province_id) is not int or province_id <= 0 or not division_ids
            or any(type(identity) is not int or identity <= 0 for identity in division_ids)
            or len(set(division_ids)) != len(division_ids)):
        raise ValueError("Creation needs unique positive division IDs and an exact province")
    at_province = {identity for identity, unit in own.items() if unit.get("location") == province_id}
    if (set(division_ids) != at_province
            or any(own[identity].get("logical_country", country) != country for identity in division_ids)):
        raise ValueError("Creation must select exactly all own divisions at the source province")
    if from_army_id is None:
        if assigned & at_province:
            raise ValueError("Creation source divisions are already assigned; an exact from-army-id is required")
    else:
        source = groups.get(from_army_id)
        source_members = members(source) if source else []
        if (type(from_army_id) is not int or not source or not at_province < set(source_members)
                or any(sum(identity in members(group) for group in groups.values()) != 1 for identity in at_province)):
            raise ValueError("Transfer needs one exact source army retaining at least one other division")


def new_army_tooltip(lines):
    compact = re.sub(r"\s+", "", " ".join(lines)).casefold()
    if "集团军群" in compact or "armygroup" in compact:
        return False
    return ("创建一个新的集团军" in compact and "编入已选中的陆军师" in compact
            or bool(re.search(r"newarmy", compact)) and "selected" in compact)


def verify_army_creation(before, after, country, province_id, division_ids, binding_name, from_army_id=None):
    from agent_contract import country_fingerprint
    source_error = None
    try:
        validate_creation_selection(before, country, province_id, division_ids, from_army_id)
    except ValueError as error:
        source_error = str(error)
    old, new = armies(before["countries"][country]), armies(after["countries"][country])
    added = sorted(new.keys() - old.keys())
    group = new[added[0]] if len(added) == 1 else {}
    expected = copy.deepcopy(after)
    for theatre in many(expected["countries"][country].get("theatres", {}).get("theatre")):
        remaining = [row for row in many(theatre.get("orders_group")) if ident(row.get("id")) not in added]
        if remaining:
            theatre["orders_group"] = remaining if len(remaining) != 1 else remaining[0]
        else:
            theatre.pop("orders_group", None)
    expected_old = copy.deepcopy(old)
    if from_army_id in old:
        remaining = [row for row in many(old[from_army_id].get("member")) if ident(row.get("unit")) not in division_ids]
        actual_members = new.get(from_army_id, {}).get("member")
        expected_old[from_army_id]["member"] = actual_members if many(actual_members) == remaining else remaining
        if from_army_id in armies(expected["countries"][country]):
            armies(expected["countries"][country])[from_army_id]["member"] = old[from_army_id].get("member")
    checks = {"source_selection_valid": source_error is None,
              "same_date": before["date"] == after["date"], "one_new_army": len(added) == 1,
              "old_armies_only_requested_members_transferred": all(new.get(identity) == group for identity, group in expected_old.items()),
              "exact_members": len(members(group)) == len(division_ids) and set(members(group)) == set(division_ids),
              "exact_name": group.get("name") == binding_name,
              "no_plan_or_commander": not group.get("order_instance") and not group.get("leader") and not group.get("field_marshal_group", False),
              "all_divisions_unchanged": before["countries"][country].get("units") == after["countries"][country].get("units"),
              "other_gameplay_unchanged": all(country_fingerprint(before, tag) == country_fingerprint(expected, tag) for tag in before["countries"]),
              "province_control_unchanged": before.get("provinces") == after.get("provinces"),
              "states_unchanged": before.get("states") == after.get("states")}
    return {"verified": all(checks.values()), "checks": checks,
            "observations": {"army_id": added[0] if len(added) == 1 else None, "members": members(group),
                             "source_province_id": province_id, "from_army_id": from_army_id,
                             **({"source_error": source_error} if source_error else {})}}


def _province_identifiers(lines):
    identifiers = set()
    for line in lines:
        # Keep numeric token boundaries: "132祸" is not the province 132.
        label = r"(?:Province\s*I\s*D|省\s*份\s*(?:I\s*D|编\s*号))"
        identifiers.update(int(value) for value in re.findall(
            label + r"\s*[:：=\-]?\s*([0-9]+)(?=[ \t]+[(（]|[ \t]*$)", line, re.I))
    return identifiers


def province_id_from_tooltip(lines):
    identifiers = _province_identifiers(lines)
    return next(iter(identifiers)) if len(identifiers) == 1 else None


def full_frame_province_id(session, image):
    evidence = []
    # Province ID is an ASCII debug field. Chinese OCR can merge its trailing
    # digits into one character; read this field in English before any fallback.
    for language in ("en-US", None):
        identifiers = set()
        for scale in (1.5, 2.0):
            try:
                options = {"scale": scale, **({"language": language} if language else {})}
                lines = [line.text for line in session.ocr.recognize(image, **options).lines]
            except Exception:
                if language:
                    break
                raise
            matching = [line for line in lines if re.search(r"\bProvince\b|省\s*份", line, re.I)]
            identifiers.update(_province_identifiers(matching))
            evidence.extend(f"[{language or 'configured'} {scale}] {line}" for line in matching)
        if identifiers:
            return (next(iter(identifiers)) if len(identifiers) == 1 else None), sorted(identifiers), evidence
    return None, [], evidence


def numeric_army_identity(session, image, region):
    """Read a numeric name field without using the requested army's label."""
    evidence = []
    for language in ("en-US", None):
        labels = set()
        for scale in (2.0, 3.0):
            try:
                options = {"region": region, "scale": scale, **({"language": language} if language else {})}
                texts = [line.text for line in session.ocr.recognize(image, **options).lines]
            except Exception:
                if language:
                    break
                raise
            for text in texts:
                token = re.fullmatch(r"[ \t]*([0-9]+)[ \t]*", text)
                if token:
                    labels.add(token[1])
            evidence.extend(f"[{language or 'configured'} {scale}] {text}" for text in texts)
        if labels:
            return (next(iter(labels)) if len(labels) == 1 else None), sorted(labels), evidence
    return None, [], evidence


def _orders_compatible_with_stop(before_country, after_country, army_id):
    expected, actual = copy.deepcopy(armies(before_country)), copy.deepcopy(armies(after_country))
    changes = []
    if army_id not in expected or army_id not in actual:
        return False, changes
    old_orders = many(expected[army_id].get("order_instance"))
    new_orders = many(actual[army_id].get("order_instance"))
    if len(old_orders) != len(new_orders):
        return False, changes
    for old, new in zip(old_orders, new_orders):
        if old.get("can_execute") == 1 and ("can_execute" not in new or new["can_execute"] == 0):
            changes.append({"army_id": army_id, "instance_id": old.get("instance_id"),
                            "before_can_execute": 1, "after_can_execute": new.get("can_execute")})
            old.pop("can_execute")
            new.pop("can_execute", None)
    return expected == actual, changes


def _defending_at_own_location(state, country, identity):
    unit = divisions(state["countries"][country]).get(identity, {})
    return {(battle["battle_id"], battle["province_id"]) for battle in own_active_battles(state, country)
            if battle["role"] == "defender" and identity in battle["own_division_ids"]
            and type(battle["battle_id"]) is int and battle["province_id"] == unit.get("location")}


def stopped_battle_settlement(before, after, army_ids):
    """A paused halt can settle accrued losses for both sides of a battle it ends."""
    def battles(state):
        return {ident(row["id"]): row for row in many(state.get("combat", {}).get("land_combat"))
                if isinstance(row, dict) and ident(row.get("id")) is not None}
    old, new = battles(before), battles(after)
    removed = old.keys() - new.keys()
    if not removed or any(old.get(key) != row for key, row in new.items()):
        return set()
    participants = set()
    for key in removed:
        battle = old[key]
        attackers = {ident(row) for row in many(battle.get("attacker", {}).get("unit"))}
        if not attackers or not attackers <= set(army_ids):
            return set()
        participants.update(attackers)
        participants.update(ident(row) for row in many(battle.get("defender", {}).get("unit")))
    if any(type(i) is not int for i in participants):
        return set()
    def losses(state):
        totals = {}
        for war in state.get("public_wars", []):
            for side in ("first", "second"):
                totals[war[side]] = totals.get(war[side], 0) + war.get(side + "_casualties", 0)
        return totals
    first, last = losses(before), losses(after)
    for tag, own in before["countries"].items():
        prev, curr = divisions(own), divisions(after["countries"][tag])
        deltas = [curr[k].get("killed", 0) - prev[k].get("killed", 0) for k in participants & prev.keys() & curr.keys()]
        if any(n < 0 for n in deltas) or sum(deltas) != last.get(tag, 0) - first.get(tag, 0):
            return set()
    return participants


def settlement_units(country, participants):
    units = copy.deepcopy(country.get("units", {}))
    for row in many(units.get("division")):
        if ident(row.get("id")) in participants:
            for field in ("experience", "org_damage", "str_damage", "army_manpower", "dig_in_cap", "killed", "equipment", "last_combat_date"):
                row.pop(field, None)
    return units


def verify_army_action(action, before, after, country):
    args, kind = action["args"], action["type"]
    first, last = before["countries"][country], after["countries"][country]
    old_groups, new_groups = armies(first), armies(last)
    group, newer = old_groups.get(args["army_id"]), new_groups.get(args["army_id"])
    ids = members(group) if group else []
    x, y = divisions(first), divisions(last)
    settled = stopped_battle_settlement(before, after, ids) if kind == "army.stop" else set()
    compatible_orders, plan_stop_changes = _orders_compatible_with_stop(first, last, args["army_id"])
    responded, blocked, unknown, new_paths = [], [], [], []
    checks = {"same_date": before["date"] == after["date"], "army_members_unchanged": bool(ids) and newer is not None and members(newer) == ids,
              "all_units_retained": x.keys() == y.keys(),
              "other_units_unchanged": all(x[key] == y[key] for key in x.keys() & y.keys() if key not in ids),
              "army_orders_compatible_with_plan_stop": compatible_orders,
              "peers_unchanged": all(before["countries"][tag].get(domain) == after["countries"][tag].get(domain)
                                      for tag in before["countries"] if tag != country
                                      for domain in ("focus", "technology", "production", "units")),
              "peers_army_orders_unchanged": all(army_structure(before["countries"][tag]) == army_structure(after["countries"][tag]) for tag in before["countries"] if tag != country)}
    checks["peers_army_activity_unchanged"] = all(army_activity(before["countries"][tag]) == army_activity(after["countries"][tag])
                                                for tag in before["countries"] if tag != country)
    checks["own_economy_unchanged"] = all(first.get(key) == last.get(key) for key in ("focus", "technology", "production", "politics"))
    checks["buildings_unchanged"] = all(value.get("buildings") == after.get("states", {}).get(sid, {}).get("buildings") for sid, value in before.get("states", {}).items())
    checks["ordered_units_other_state_unchanged"] = all({key: value for key, value in x[identity].items() if key not in ORDER_FIELDS} == {key: value for key, value in y.get(identity, {}).items() if key not in ORDER_FIELDS} for identity in ids if identity in x)
    if settled:
        own_before = divisions({"units": settlement_units(first, settled)})
        own_after = divisions({"units": settlement_units(last, settled)})
        checks["ordered_units_other_state_unchanged"] = all(
            {k:v for k,v in own_before[i].items() if k not in ORDER_FIELDS} ==
            {k:v for k,v in own_after.get(i, {}).items() if k not in ORDER_FIELDS}
            for i in ids if i in own_before)
        checks["peers_unchanged"] = all(
            all(before["countries"][t].get(d) == after["countries"][t].get(d) for d in ("focus", "technology", "production"))
            and settlement_units(before["countries"][t], settled) == settlement_units(after["countries"][t], settled)
            for t in before["countries"] if t != country)
    if kind in ("army.move", "army.attack"):
        target = args["target_province_id"]
        for identity in ids:
            unit = y.get(identity, {})
            path = items(unit.get("path"))
            has_order = bool(path) and path[-1] == target and unit.get("move_priority") == "player_order"
            arrived = unit.get("location") == target and not path
            if identity in y and (has_order or arrived):
                responded.append(identity)
                if has_order and path != items(x[identity].get("path")):
                    new_paths.append(identity)
                continue
            defenses = _defending_at_own_location(before, country, identity) & _defending_at_own_location(after, country, identity)
            if kind == "army.attack" and identity in x and identity in y and x[identity] == y[identity] and defenses:
                battle_id, province = sorted(defenses)[0]
                blocked.append({"division_id": identity, "reason": "defending_local_battle", "battle_id": battle_id, "province_id": province})
            else:
                unknown.append(identity)
        checks["all_members_accounted_for"] = bool(ids) and not unknown
        checks["responding_paths_valid"] = bool(responded)
        checks["blocked_defenders_unchanged"] = all(x[row["division_id"]] == y[row["division_id"]] for row in blocked)
        checks["partial_has_new_dispatch"] = not blocked or bool(new_paths)
        checks["not_teleported"] = all(x[identity].get("location") == y.get(identity, {}).get("location") for identity in ids)
        if kind == "army.attack":
            target_owner = before.get("states", {}).get(str(args["target_state_id"]), {}).get("owner")
            target_controller = before.get("provinces", {}).get(str(target), {}).get("controller", target_owner)
            checks["declared_enemy_target"] = "provinces" in before and target_controller in {row["enemy"] for row in own_wars(first, country, before)}
    elif kind == "army.stop":
        checks["paths_cleared"] = bool(ids) and all(identity in y and not items(y[identity].get("path")) and not y[identity].get("execute_order", 0) for identity in ids)
        responded = [identity for identity in ids if identity in y and not items(y[identity].get("path")) and not y[identity].get("execute_order", 0)]
    else:
        raise ValueError("Unsupported army verification")
    verified = all(checks.values())
    return {"verified": verified, "effect_status": ("partial" if blocked else "full") if verified else "unverified",
            "checks": checks, "observations": {"members": ids, "responded_units": responded, "blocked_units": blocked,
                                               "settled_battle_units": sorted(settled),
                                               "unknown_units": unknown, "new_path_units": new_paths,
                                               "army_activity_before": army_activity(first).get(str(args["army_id"])),
                                               "army_activity_after": army_activity(last).get(str(args["army_id"])),
                                               "plan_stop_changes": plan_stop_changes,
                                               "paths_after": {str(identity): items(y[identity].get("path")) for identity in ids if identity in y}}}


def retained_army_order(action, checkpoint, current, country):
    old, new = checkpoint["countries"][country], current["countries"][country]
    group = armies(old).get(action["args"]["army_id"])
    newer = armies(new).get(action["args"]["army_id"])
    ids, units = members(group) if group else [], divisions(new)
    if not ids:
        return False
    if action["type"] == "army.attack":
        from m0_probe import game_hours
        survivors = [identity for identity in ids if identity in units]
        losses = len(survivors) != len(ids)
        if newer is None and survivors:
            return False
        if newer is not None and set(members(newer)) != set(survivors):
            return False
        prior_casualties = sum(row.get("our_casualties") or 0 for row in own_wars(old, country, checkpoint))
        current_casualties = sum(row.get("our_casualties") or 0 for row in own_wars(new, country, current))
        if losses:
            engaged = {identity for battle in own_active_battles(checkpoint, country) for identity in battle["own_division_ids"]}
            lost = set(ids) - set(survivors)
            if not (game_hours(current["date"]) > game_hours(checkpoint["date"]) and current_casualties > prior_casualties and lost <= engaged):
                return False
        target = action["args"]["target_province_id"]
        return (bool(survivors) or losses) and all(
            units[identity].get("location") == target
            or (items(units[identity].get("path")) and items(units[identity]["path"])[-1] == target)
            or (bool(_defending_at_own_location(checkpoint, country, identity) & _defending_at_own_location(current, country, identity))
                and units[identity].get("location") == divisions(old)[identity].get("location")
                and items(units[identity].get("path")) == items(divisions(old)[identity].get("path")))
            or (game_hours(current["date"]) > game_hours(checkpoint["date"])
                and game_hours(units[identity].get("last_combat_date", "1.1.1.1")) >= game_hours(checkpoint["date"]))
            for identity in survivors)
    if newer is None or members(newer) != ids or any(identity not in units for identity in ids):
        return False
    if action["type"] in ("army.move", "army.attack"):
        target = action["args"]["target_province_id"]
        return all(units[identity].get("location") == target or (items(units[identity].get("path")) and items(units[identity]["path"])[-1] == target) for identity in ids)
    return all(not items(units[identity].get("path")) and not units[identity].get("execute_order", 0) for identity in ids)
