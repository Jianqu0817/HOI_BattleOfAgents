"""Own-army observations and calibrated ordinary movement for the Chinese UI profile."""
import argparse
import copy
from collections import deque
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid

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


def own_wars(own, country):
    result = []
    for enemy, relation in own.get("diplomacy", {}).get("active_relations", {}).items():
        war = relation.get("war_relation") if isinstance(relation, dict) else None
        if isinstance(war, dict) and {war.get("first"), war.get("second")} == {country, enemy} and enemy != country:
            result.append({"enemy": enemy, "since": war.get("start_date"),
                           "our_casualties": war.get("first_casualties" if war.get("first") == country else "second_casualties")})
    return result


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
        enemies = {row["enemy"] for row in own_wars(state["countries"][country], country)}
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
    wars = own_wars(own, country)
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


def _fold(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def clear_hover(session):
    # The pinned backend's park_cursor is intentionally a no-op. Tooltip
    # differencing needs a frame with the previous hover genuinely gone.
    session.input.move(min(600, session.window.size()[0] - 40), 60)
    time.sleep(.25)


def debug_response_state(results):
    text = "\n".join(line for entry in results for line in entry.get("response", []))
    matches = re.findall(r"debug\s+(ENABLED|DISABLED)", text, flags=re.I)
    return matches[-1].lower() if matches else None


def ensure_debug_tooltips(session):
    from hoi4_live.game import console
    for _ in range(2):
        state = debug_response_state(console.run(session, ["tdebug"], keep_open=False))
        if state == "enabled":
            session._iron_debug_tooltips_enabled = True
            return
        if state != "disabled":
            break
    raise RuntimeError("Could not confirm ENABLED state from tdebug console response")


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


def bind_visible_armies(session, country, source_groups):
    from hoi4_live.game import armyview
    from hoi4_live.perception import tooltip
    names = [row.get("name") for row in source_groups.values() if row.get("name")]
    existing_names = {row.get("name") for row in source_groups.values()}
    nonce = 100000 + uuid.uuid4().int % 899000
    while any(str(nonce + index) in existing_names for index in range(1, 100)):
        nonce = 100000 + uuid.uuid4().int % 899000
    records, skipped = [], []
    height = session.window.size()[1]
    for cx in armyview.card_boxes(session.grab()):
        clear_hover(session)
        tip, _, _ = tooltip.read(session, cx, height - 70)
        source_name = tooltip_army_name(tip.get("lines", []), names)
        if not existing_army_tooltip(tip.get("lines", [])):
            skipped.append({"x": cx, "reason": "tooltip is not an exact existing own-army name", "tooltip": tip.get("lines", [])})
            continue
        marker = str(nonce + len(records) + 1)
        armyview.select_card(session, cx)
        armyview.rename_army(session, marker)
        records.append({"card_x": cx, "old_title": source_name, "name": marker})
    return records, skipped


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


def target_province_point(session, province_id, enable_debug=False):
    from hoi4_live.perception import tooltip
    width, height = session.window.size()
    observed, any_id = [], False
    offsets = [(0, 0)]
    for radius in (12, 40, 56):
        offsets.extend([(radius, 0), (-radius, 0), (0, radius), (0, -radius)])
    offsets.extend([(40, 40), (-40, 40), (40, -40), (-40, -40)])
    points = [(width // 2 + dx, height // 2 + dy) for dx, dy in offsets]
    preferred = getattr(session, "_iron_province_points", {}).get(province_id)
    if preferred:
        points = [preferred] + [point for point in points if point != preferred]
    for point in points:
        clear_hover(session)
        data, _, full_image = tooltip.read(session, *point)
        lines = data.get("lines", [])
        actual, candidates, evidence = full_frame_province_id(session, full_image)
        data["full_frame_identity_lines"] = evidence
        data["full_frame_identity_candidates"] = candidates
        any_id = any_id or bool(candidates) or province_id_from_tooltip(lines) is not None
        observed.append({"point": point, "province_id": actual, "lines": lines, "identity_lines": evidence})
        if actual == province_id:
            session._iron_debug_tooltips_enabled = True
            if not hasattr(session, "_iron_province_points"):
                session._iron_province_points = {}
            session._iron_province_points[province_id] = point
            data["confirmed_province_id"] = actual
            data["identity_screenshot"] = session.artifact(full_image, "iron_province_identity")
            return point, data
    if enable_debug and not any_id and not getattr(session, "_iron_debug_tooltips_enabled", False):
        ensure_debug_tooltips(session)
        return target_province_point(session, province_id, enable_debug=False)
    raise RuntimeError("Requested map province not confirmed by debug tooltip: " + repr(observed))


def select_bound_army(session, name, expected_count):
    from hoi4_live.game import armyview, panels
    from hoi4_live.perception import tooltip
    panels.to_map(session)
    seen = []
    height = session.window.size()[1]
    for cx in armyview.card_boxes(session.grab()):
        card = {"x": cx, "expected_divisions": expected_count}
        clear_hover(session)
        tip, _, tip_image = tooltip.read(session, cx, height - 70)
        texts = tip.get("lines", [])
        seen.extend(texts)
        first = re.sub(r"\s+", "", next(iter(texts), "")).casefold()
        if re.search(r"create|newarmy|创建|新建|新集团军|新军团", first, re.I):
            continue
        if name.isdigit():
            tip_label = None
            if tip.get("found") and tip.get("region"):
                x, top, width, size = tip["region"]
                tip_label, _, reads = numeric_army_identity(session, tip_image, [x, top, width, min(size, 120)])
                seen.extend(reads)
            exact_tip = tip_label == name
        else:
            exact_tip = any(_fold(text) == _fold(name) for text in texts)
        if not exact_tip and not existing_army_tooltip(texts):
            continue
        armyview.select_card(session, card["x"])
        image = session.grab()
        if name.isdigit():
            label, _, reads = numeric_army_identity(session, image, [62, 84, 145, 38])
            seen.extend(reads)
            if label == name:
                return card
            continue
        for scale in (2, 3):
            lines = session.ocr.recognize(image, region=[62, 84, 145, 38], scale=scale).lines
            texts = [line.text for line in lines]
            seen.extend(texts)
            if any(_fold(text) == _fold(name) for text in texts):
                return card
    raise RuntimeError("Bound army header and member count not confirmed: " + repr(seen))


def execute_army_action(session, country, action, state, allow_probe=False):
    from hoi4_live.game import console, armyview, mapview, panels
    context = context_for_session(session)
    args, kind = action["args"], action["type"]
    group = armies(state["countries"][country]).get(args["army_id"])
    profile = context["profile"].get("countries", {}).get(country, {}).get(str(args["army_id"]), {})
    if not group or not profile.get("name") or group.get("name") != profile["name"]:
        raise ValueError("Army lacks a current exact ID/name binding")
    if not allow_probe and kind not in context.get("operations", []):
        raise ValueError("Army action has no live acceptance")
    if kind in ("army.move", "army.attack"):
        targets = movement_targets(state, country, group, context, limit=128, attack=kind == "army.attack")
        if args["target_province_id"] not in [row["province_id"] for row in targets]:
            raise ValueError("Target is not a friendly land route for every member")
        if kind == "army.attack" and not any(row["province_id"] == args["target_province_id"] and row["state_id"] == args["target_state_id"] for row in targets):
            raise ValueError("Attack province/state identity changed")
        # Camera movement is referee administration; the order itself is an ordinary right click.
        # At world zoom the engine does not place the requested province at the
        # screen centre; establish the accepted province scale before the jump.
        console.close_console(session)
        panels.to_map(session)
        mapview.zoom_abs(session, "province")
        console.run(session, ["goto_province " + str(args["target_province_id"])], keep_open=False)
        time.sleep(1)
        target_province_point(session, args["target_province_id"], enable_debug=True)
    else:
        console.close_console(session)
    card = select_bound_army(session, group["name"], len(members(group)))
    evidence = session.artifact(session.grab(), "iron_army_selected")
    target_tip = None
    if kind in ("army.move", "army.attack"):
        point, target_tip = target_province_point(session, args["target_province_id"])
        session.input.click(*armyview.card_points(session, card["x"])["stop"])
        time.sleep(.3)
        session.input.click(*point, button="right", hold=.15)
    elif kind == "army.stop":
        session.input.click(*armyview.card_points(session, card["x"])["stop"])
        time.sleep(.4)
        # The installed unitview.gui binds btn_hold / HALT_UNIT to H.
        session.input.key("h")
    else:
        raise ValueError("Unsupported army operation")
    time.sleep(.8)
    return {"input_path": "ordinary UI", "army_id": args["army_id"], "division_ids": members(group),
            "selected_evidence": evidence, "target_tooltip": target_tip, "save_verification_required": True}


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


def verify_army_action(action, before, after, country):
    args, kind = action["args"], action["type"]
    first, last = before["countries"][country], after["countries"][country]
    old_groups, new_groups = armies(first), armies(last)
    group, newer = old_groups.get(args["army_id"]), new_groups.get(args["army_id"])
    ids = members(group) if group else []
    x, y = divisions(first), divisions(last)
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
    checks["own_economy_unchanged"] = all(first.get(key) == last.get(key) for key in ("focus", "technology", "production", "politics"))
    checks["buildings_unchanged"] = all(value.get("buildings") == after.get("states", {}).get(sid, {}).get("buildings") for sid, value in before.get("states", {}).items())
    checks["ordered_units_other_state_unchanged"] = all({key: value for key, value in x[identity].items() if key not in ORDER_FIELDS} == {key: value for key, value in y.get(identity, {}).items() if key not in ORDER_FIELDS} for identity in ids if identity in x)
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
            checks["declared_enemy_target"] = "provinces" in before and target_controller in {row["enemy"] for row in own_wars(first, country)}
    elif kind == "army.stop":
        checks["paths_cleared"] = bool(ids) and all(identity in y and not items(y[identity].get("path")) and not y[identity].get("execute_order", 0) for identity in ids)
        responded = [identity for identity in ids if identity in y and not items(y[identity].get("path")) and not y[identity].get("execute_order", 0)]
    else:
        raise ValueError("Unsupported army verification")
    verified = all(checks.values())
    return {"verified": verified, "effect_status": ("partial" if blocked else "full") if verified else "unverified",
            "checks": checks, "observations": {"members": ids, "responded_units": responded, "blocked_units": blocked,
                                               "unknown_units": unknown, "new_path_units": new_paths,
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
        prior_casualties = sum(row.get("our_casualties") or 0 for row in own_wars(old, country))
        current_casualties = sum(row.get("our_casualties") or 0 for row in own_wars(new, country))
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


def main():
    from m0_probe import Probe, settled_pause_state, write_json, countries_from
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["bind", "bind-all", "probe"])
    parser.add_argument("--country", required=True)
    parser.add_argument("--army-id", type=int)
    parser.add_argument("--card-index", type=int)
    parser.add_argument("--card-x", type=int, help="Observed count-frame x; bind only")
    parser.add_argument("--binding-name", help="Unique UI label for bind; default: numeric army ID")
    parser.add_argument("--action", choices=["army.move", "army.stop", "army.attack"])
    parser.add_argument("--target-province-id", type=int)
    parser.add_argument("--known-disabled-pid", required=True, type=int)
    parser.add_argument("--name", required=True)
    parser.add_argument("--match-config", type=Path, default=ROOT / "configs/match.example.json")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Z][A-Z0-9]{2}", args.country) or not re.fullmatch(r"[A-Za-z0-9_]+", args.name):
        parser.error("country/name is invalid")
    if args.operation != "bind-all" and (args.army_id is None or args.army_id < 1):
        parser.error("bind/probe needs a positive army-id")
    if args.binding_name is not None and not re.fullmatch(r"[A-Za-z0-9 _-]{1,24}", args.binding_name):
        parser.error("binding-name must be 1–24 ASCII letters, digits, spaces, underscores or hyphens")
    if args.operation == "bind" and args.card_x is None and (args.card_index is None or args.card_index < 1):
        parser.error("bind needs a positive raw card-index or observed card-x")
    if args.operation == "probe" and (not args.action or (args.action in ("army.move", "army.attack") and not args.target_province_id)):
        parser.error("probe needs action and move needs target-province-id")
    directory = ROOT / "runs/army" / args.name
    if (directory / "result.json").exists():
        parser.error("Run exists; inspect its journal before another attempt")
    tags = countries_from(json.loads(args.match_config.read_text(encoding="utf-8")))
    if args.country not in tags:
        parser.error("country must be configured in match-config")
    probe, result = Probe(directory, tags), {"operation": args.operation, "country": args.country, "army_id": args.army_id, "status": "starting"}
    try:
        with probe.session.lock.hold("IRON army calibration"):
            status = probe.call("hoi4_status", {})
            if status["game"]["pids"] != [args.known_disabled_pid] or settled_pause_state(probe.session)[0] != "paused":
                raise ValueError("Same controlled process and paused game required")
            probe.receipts([args.country], "army_calibrate")
            result["before_save"] = probe.save("iron_" + args.name + "_before")
            before = json.loads((directory / ("iron_" + args.name + "_before.json")).read_text(encoding="utf-8"))
            group = armies(before["countries"][args.country]).get(args.army_id)
            if not group and args.operation != "bind-all":
                raise ValueError("Own army ID is absent")
            from hoi4_live.game import console, armyview, panels
            console.close_console(probe.session)
            panels.to_map(probe.session)
            profile_path = Path(probe.session.cfg.data.get("iron_army_profile", DEFAULT_PROFILE))
            profile = load_profile(profile_path)
            result["ui_profile"] = ui_profile_identity(probe.session)
            marker = args.binding_name or str(args.army_id)
            try:
                if args.operation == "bind-all":
                    result["binding_attempts"], result["skipped_frames"] = bind_visible_armies(probe.session, args.country, armies(before["countries"][args.country]))
                elif args.operation == "bind":
                    if any(key != args.army_id and value.get("name") == marker for key, value in armies(before["countries"][args.country]).items()):
                        raise ValueError("Binding name is already used by another own army")
                    cards = [{"x": cx} for cx in armyview.card_boxes(probe.session.grab())]
                    if args.card_x is not None:
                        matched = [row for row in cards if abs(row["x"] - args.card_x) <= 3]
                        if len(matched) != 1:
                            raise ValueError("card-x is not a uniquely observed army count frame")
                        chosen = matched[0]
                    elif args.card_index > len(cards):
                        raise ValueError("Requested army card is not present")
                    else:
                        chosen = cards[args.card_index - 1]
                    armyview.select_card(probe.session, chosen["x"])
                    armyview.rename_army(probe.session, marker)
                    result["binding_name"] = marker
                else:
                    action = {"id": "calibration", "type": args.action, "args": {"army_id": args.army_id}}
                    if args.action in ("army.move", "army.attack"):
                        action["args"]["target_province_id"] = args.target_province_id
                    if args.action == "army.attack":
                        action["args"]["target_state_id"] = context_for_session(probe.session)["map"].get("provinces", {}).get(str(args.target_province_id), {}).get("state")
                    result["action"] = action
                    result["ui_result"] = execute_army_action(probe.session, args.country, action, before, allow_probe=True)
            except Exception as error:
                result["ui_error"] = str(error)
            result["after_save"] = probe.save("iron_" + args.name + "_after")
            after = json.loads((directory / ("iron_" + args.name + "_after.json")).read_text(encoding="utf-8"))
            if args.operation in ("bind", "bind-all"):
                expected = copy.deepcopy(before)
                before_groups, after_groups = armies(before["countries"][args.country]), armies(after["countries"][args.country])
                bound = {}
                if args.operation == "bind":
                    bound[args.army_id] = marker
                else:
                    for attempt in result.get("binding_attempts", []):
                        matches = [key for key, value in after_groups.items() if value.get("name") == attempt["name"]]
                        if len(matches) == 1 and matches[0] in before_groups:
                            bound[matches[0]] = attempt["name"]
                    result["binding_map"] = {str(key): value for key, value in bound.items()}
                for identity, name in bound.items():
                    armies(expected["countries"][args.country])[identity]["name"] = name
                # Ignore log receipt flags; every gameplay field and army order must match.
                from agent_contract import country_fingerprint
                result["verified"] = before["date"] == after["date"] and all(country_fingerprint(expected, tag) == country_fingerprint(after, tag) for tag in tags)
                result["verified"] = result["verified"] and all(after_groups.get(key, {}).get("name") == name for key, name in bound.items())
                if args.operation == "bind-all":
                    result["verified"] = result["verified"] and len(bound) == len(result.get("binding_attempts", [])) and (bool(bound) or not before_groups)
                if result["verified"]:
                    registered = profile.setdefault("countries", {}).setdefault(args.country, {})
                    for identity, name in bound.items():
                        kept = retained_acceptance(registered.get(str(identity), {}), args.country, identity, before_groups[identity], after_groups[identity])
                        registered[str(identity)] = {"name": name, **kept, "binding_evidence": str(directory / "result.json")}
            else:
                result["verification"] = verify_army_action(action, before, after, args.country)
                result["verified"] = result["verification"]["verified"]
                if result["verified"]:
                    entry = profile["countries"][args.country][str(args.army_id)]
                    entry["accepted_actions"] = sorted(set(entry.get("accepted_actions", []) + [args.action]))
                    entry.setdefault("acceptance_evidence", {})[args.action] = str(directory / "result.json")
                    profile.setdefault("accepted_operations", {})[args.action] = {"ui_profile": result["ui_profile"], "evidence": str(directory / "result.json")}
            if result["verified"]:
                write_json(profile_path, profile)
            result["status"] = "verified" if result["verified"] else "needs_inspection"
            console.close_console(probe.session)
            clear_hover(probe.session)
            result["screenshot"] = probe.call("hoi4_screenshot", {})
    except Exception as error:
        result.update(status="interrupted", error=str(error))
        raise
    finally:
        write_json(directory / "result.json", result)
        probe.session.shutdown()
        print(json.dumps({key: result.get(key) for key in ("status", "verified", "error")}, ensure_ascii=False))
    if not result.get("verified"):
        raise SystemExit(1)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
