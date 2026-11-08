"""Own-fleet/wing missions through calibrated ordinary Chinese UI, with exact save verification."""
import copy
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "configs/naval_air.local.json"
AIR_BITS = {"air_superiority": 1, "cas": 2}
NAVY_CODES = {"patrol": 0, "strike_force": 1, "hold": 8}
ACTION_ARGS = {"air.assign": {"wing_id", "region_id", "mission"}, "air.stop": {"wing_id"},
               "navy.assign": {"fleet_id", "region_id", "mission"}, "navy.move": {"fleet_id", "province_id"},
               "navy.stop": {"fleet_id"}}
NAVAL_ORDER_FIELDS = {"mission", "path", "move_priority", "movement_progress"}
FLEET_ORDER_FIELDS = {"strategic_region", "hours_without_patrol_missions_pairs"}
TOOLTIPS = {"air_superiority": r"^(?:夺取制空|制空权|空中优势|air\s*superiority)",
            "cas": r"^(?:密接支援|近距(?:离)?(?:空中)?支援|close\s*air\s*support)",
            "patrol": r"^(?:巡逻|patrol)", "strike_force": r"^(?:打击舰队|strike\s*force)",
            "hold": r"^(?:驻留|待命|停止|hold)"}


def many(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def ident(value):
    rows = many(value)
    return rows[0].get("id") if rows and isinstance(rows[0], dict) else value if type(value) is int else None


def items(value):
    return value.get("__items__", value.get("_items", [])) if isinstance(value, dict) else many(value)


def fleets(state, country):
    rows = many(state["countries"][country].get("units", {}).get("fleet"))
    result = {}
    for row in rows:
        identity = ident(row.get("id"))
        if type(identity) is not int or identity in result:
            raise ValueError("Fleet identity is missing or duplicated")
        result[identity] = row
    return result


def task_forces(fleet):
    result = {}
    for row in many(fleet.get("task_force")):
        identity = ident(row.get("id"))
        if type(identity) is not int or identity in result:
            raise ValueError("Task force identity is missing or duplicated")
        result[identity] = row
    return result


def air_wings(state, country):
    result = {}
    bases = {ident(base.get("id")): base for base in many(state.get("strategic_air", {}).get("air_base"))}
    for pool in many(state.get("strategic_air", {}).get(country, {}).get("air_wing_pool")):
        for wing in many(pool.get("air_wings")):
            if wing.get("tag", country) != country:
                continue
            identity = ident(wing.get("id"))
            if type(identity) is not int or identity in result:
                raise ValueError("Air wing identity is missing or duplicated")
            base_id = ident(pool.get("air_base"))
            result[identity] = {"wing": wing, "pool_id": ident(pool.get("id")), "base_id": base_id,
                                "base_state": bases.get(base_id, {}).get("state"), "definition": pool.get("definition")}
    return result


def _air_state(state, country):
    return air_wings(state, country)


def naval_air_fingerprint(state, country):
    value = {"fleets": fleets(state, country), "air_wings": _air_state(state, country),
             "air_available": "strategic_air" in state}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def load_profile(path=DEFAULT_PROFILE):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {"schema_version": 1, "countries": {}}


def context_for_session(session):
    from army_actions import context_for_session as army_context
    context = army_context(session)
    return {"map": context.get("map", {}), "profile": load_profile(session.cfg.data.get("iron_naval_air_profile", DEFAULT_PROFILE))}


def _binding(context, country, domain, identity):
    return context.get("profile", {}).get("countries", {}).get(country, {}).get(domain, {}).get(str(identity), {})


def _regions(binding, context, naval=False):
    result = []
    for identity in binding.get("region_ids", []):
        row = context.get("map", {}).get("regions", {}).get(str(identity))
        if type(identity) is int and row and (not naval or row.get("naval") is True):
            result.append({"region_id": identity, "name": row.get("name", row.get("name_key", str(identity)))})
    return result


def naval_air_observation(state, country, context=None):
    context = context or {}
    wing_rows, fleet_rows = [], []
    menu = {kind: {"wings" if kind.startswith("air.") else "fleets": []} for kind in ACTION_ARGS}
    for identity, row in air_wings(state, country).items():
        wing, mission = row["wing"], row["wing"].get("mission", {})
        binding = _binding(context, country, "air_wings", identity)
        value = {"id": identity, "name": wing.get("name"), "type": row["definition"], "planes": wing.get("count"),
                 "base_id": row["base_id"], "base_state": row["base_state"], "mission_mask": mission.get("type", 0),
                 "mission_region_id": mission.get("strategic_region"), "executing_mission": mission.get("executing_mission")}
        wing_rows.append(value)
        if not binding or binding.get("name") != wing.get("name") or row["base_state"] is None or mission.get("type", 0) not in (0, 1, 2):
            continue
        targets = _regions(binding, context)
        supported = [name for name in binding.get("missions", []) if name in AIR_BITS and type(wing.get("allow_mission_type")) is int and wing["allow_mission_type"] & AIR_BITS[name]]
        if "air.assign" in binding.get("accepted_actions", []) and targets and supported:
            menu["air.assign"]["wings"].append({"id": identity, "name": wing["name"], "missions": supported, "targets": targets})
        if "air.stop" in binding.get("accepted_actions", []):
            menu["air.stop"]["wings"].append({"id": identity, "name": wing["name"]})
    for identity, fleet in fleets(state, country).items():
        forces = task_forces(fleet)
        binding = _binding(context, country, "fleets", identity)
        fleet_rows.append({"id": identity, "name": fleet.get("name"), "regions": items(fleet.get("strategic_region")),
                           "task_forces": [{"id": tid, "name": tf.get("name"), "location": tf.get("location"),
                                            "ships": len(many(tf.get("ship"))), "mission_code": tf.get("mission", {}).get("mission"),
                                            "path": items(tf.get("path"))} for tid, tf in forces.items()]})
        if not binding or binding.get("name") != fleet.get("name") or not forces or any(tf.get("logical_country", country) != country for tf in forces.values()):
            continue
        targets = _regions(binding, context, naval=True)
        supported = [name for name in binding.get("missions", []) if name in ("patrol", "strike_force")]
        if "navy.assign" in binding.get("accepted_actions", []) and targets and supported:
            menu["navy.assign"]["fleets"].append({"id": identity, "name": fleet["name"], "missions": supported, "targets": targets})
        provinces = [pid for pid in binding.get("province_ids", []) if type(pid) is int and context.get("map", {}).get("provinces", {}).get(str(pid), {}).get("kind") == "sea"]
        if "navy.move" in binding.get("accepted_actions", []) and provinces:
            menu["navy.move"]["fleets"].append({"id": identity, "name": fleet["name"], "targets": [{"province_id": pid} for pid in provinces]})
        if "navy.stop" in binding.get("accepted_actions", []):
            menu["navy.stop"]["fleets"].append({"id": identity, "name": fleet["name"]})
    return {"air_wings": wing_rows, "fleets": fleet_rows, "air_data_available": "strategic_air" in state,
            "action_options": {kind: value if next(iter(value.values())) else None for kind, value in menu.items()},
            "visibility": "own air wings and fleets only; target IDs from installed map and calibrated profile",
            "unavailable": {"air": "strategic_air was not captured" if "strategic_air" not in state else "no own air wings" if not wing_rows else "uncalibrated objects/targets are not executable",
                            "navy": "no own fleets" if not fleet_rows else "uncalibrated objects/targets are not executable"}}


def validate_naval_air_action(action, observed):
    kind, args = action.get("type"), action.get("args")
    if kind not in ACTION_ARGS or not isinstance(args, dict) or set(args) != ACTION_ARGS[kind]:
        raise ValueError("Invalid naval/air action schema")
    key, collection = ("wing_id", "wings") if kind.startswith("air.") else ("fleet_id", "fleets")
    if type(args[key]) is not int or args[key] <= 0:
        raise ValueError("Naval/air identity must be a positive integer")
    menu = observed.get("naval_air", {}).get("action_options", {}).get(kind) or {}
    row = next((value for value in menu.get(collection, []) if value["id"] == args[key]), None)
    if row is None:
        raise ValueError("Naval/air object has no live acceptance in this own-country observation")
    if "mission" in args and args["mission"] not in row["missions"]:
        raise ValueError("Mission is not an observed capability of this object")
    for target in ("region_id", "province_id"):
        if target in args and (type(args[target]) is not int or args[target] not in [value[target] for value in row["targets"]]):
            raise ValueError("Naval/air target is not an observed calibrated destination")
    return "air" if key == "wing_id" else "navy", args[key]


def _restore_fields(target, source, fields):
    for key in fields:
        if key in source:
            target[key] = copy.deepcopy(source[key])
        else:
            target.pop(key, None)


def verify_naval_air_action(action, before, after, country):
    from agent_contract import country_fingerprint
    kind, args = action["type"], action["args"]
    checks = {"same_date": before["date"] == after["date"],
              "peers_unchanged": all(country_fingerprint(before, tag) == country_fingerprint(after, tag) and _air_state(before, tag) == _air_state(after, tag) for tag in before["countries"] if tag != country),
              "map_unchanged": before.get("states") == after.get("states") and before.get("provinces") == after.get("provinces")}
    observations = {}
    if kind.startswith("air."):
        x, y = air_wings(before, country), air_wings(after, country)
        identity = args["wing_id"]
        checks["own_wing_retained"] = identity in x and identity in y and x.keys() == y.keys()
        if not checks["own_wing_retained"]:
            return {"verified": False, "checks": checks, "effect_status": "unknown", "observations": observations}
        expected = copy.deepcopy(y)
        _restore_fields(expected[identity]["wing"], x[identity]["wing"], {"mission"})
        mission = y[identity]["wing"].get("mission", {})
        checks["only_target_mission_changed"] = expected == x
        checks["economy_army_navy_unchanged"] = country_fingerprint(before, country) == country_fingerprint(after, country)
        wanted = AIR_BITS[args["mission"]] if kind == "air.assign" else 0
        checks["exact_mission_mask"] = mission.get("type", 0) == wanted
        if kind == "air.assign":
            checks["exact_region"] = mission.get("strategic_region") == args["region_id"]
        observations = {"wing_id": identity, "mission_before": x[identity]["wing"].get("mission", {}), "mission_after": mission}
    else:
        x, y = fleets(before, country), fleets(after, country)
        identity = args["fleet_id"]
        checks["own_fleet_retained"] = identity in x and identity in y and x.keys() == y.keys()
        if not checks["own_fleet_retained"]:
            return {"verified": False, "checks": checks, "effect_status": "unknown", "observations": observations}
        old, new = task_forces(x[identity]), task_forces(y[identity])
        checks["exact_taskforce_membership"] = old.keys() == new.keys() and bool(old)
        restored = copy.deepcopy(after)
        target = fleets(restored, country)[identity]
        _restore_fields(target, x[identity], FLEET_ORDER_FIELDS)
        for tid, tf in task_forces(target).items():
            if tid in old:
                _restore_fields(tf, old[tid], NAVAL_ORDER_FIELDS)
        checks["only_fleet_orders_changed"] = country_fingerprint(before, country) == country_fingerprint(restored, country)
        checks["air_unchanged"] = _air_state(before, country) == _air_state(after, country)
        if kind == "navy.assign":
            checks["exact_mission"] = bool(new) and all(tf.get("mission", {}).get("mission") == NAVY_CODES[args["mission"]] for tf in new.values())
            checks["exact_region"] = args["region_id"] in items(y[identity].get("strategic_region")) and all(args["region_id"] in items(tf.get("mission", {}).get("accessible_regions")) for tf in new.values())
        elif kind == "navy.move":
            target_pid = args["province_id"]
            checks["exact_destination"] = bool(new) and all(items(tf.get("path")) and items(tf.get("path"))[-1] == target_pid and tf.get("move_priority") == "player_order" for tf in new.values())
        elif kind == "navy.stop":
            checks["held_and_stopped"] = bool(new) and all(tf.get("mission", {}).get("mission") == 8 and not items(tf.get("path")) for tf in new.values())
        else:
            raise ValueError("Unsupported naval/air action")
        checks["not_teleported"] = old.keys() == new.keys() and all(old[tid].get("location") == new[tid].get("location") for tid in old)
        observations = {"fleet_id": identity, "task_force_ids": sorted(new), "regions_after": items(y[identity].get("strategic_region"))}
    verified = all(checks.values())
    unchanged = all(naval_air_fingerprint(before, tag) == naval_air_fingerprint(after, tag) and country_fingerprint(before, tag) == country_fingerprint(after, tag) for tag in before["countries"])
    return {"verified": verified, "checks": checks, "effect_status": "full" if verified else "none" if unchanged else "unknown", "observations": observations}


def retained_naval_air_order(action, checkpoint, current, country):
    kind, args = action["type"], action["args"]
    if kind.startswith("air."):
        row = air_wings(current, country).get(args["wing_id"])
        if row is None:
            return False
        mission = row["wing"].get("mission", {})
        return mission.get("type", 0) == (AIR_BITS[args["mission"]] if kind == "air.assign" else 0) and (kind != "air.assign" or mission.get("strategic_region") == args["region_id"])
    fleet = fleets(current, country).get(args["fleet_id"])
    if fleet is None:
        return False
    forces = task_forces(fleet)
    if kind == "navy.assign":
        return bool(forces) and args["region_id"] in items(fleet.get("strategic_region")) and all(tf.get("mission", {}).get("mission") == NAVY_CODES[args["mission"]] for tf in forces.values())
    if kind == "navy.move":
        return bool(forces) and all(tf.get("location") == args["province_id"] or items(tf.get("path")) and items(tf["path"])[-1] == args["province_id"] for tf in forces.values())
    return bool(forces) and all(tf.get("mission", {}).get("mission") == 8 and not items(tf.get("path")) for tf in forces.values())


def _normal(value):
    return re.sub(r"\s+", "", value or "").casefold()


def rename_selected_ui(session, domain, expected_name, numeric_name, name_region=None):
    """Setup only: rename a currently selected fleet or open wing-details name field."""
    from hoi4_live.desktop import clipboard
    if domain not in ("air_wings", "fleets") or not re.fullmatch(r"[0-9]{6,10}", numeric_name):
        raise ValueError("Binding needs an air/fleet domain and an unambiguous numeric name")
    width, height = session.window.size()
    region = name_region or ([width // 2 - 162, height // 2 - 193, 267, 26] if domain == "air_wings" else [55, 85, 260, 65])
    if domain == "air_wings":
        session.input.move(width // 2 - 250, height // 2 - 225)
        time.sleep(.3)
    if expected_name is None:
        # Discovery binding: the operator has opened an own-unit name field.
        # The after-save discovers its real ID; no similar row/name is assumed.
        hits = [line for line in session.ocr.recognize(session.grab(), region=region, scale=2).lines if line.text.strip()]
        lines = [line.text for line in hits]
    else:
        hits, lines = _exact_lines(session, region, expected_name)
    if len(hits) != 1:
        raise RuntimeError("Current name field is not the exact expected identity: " + str(lines))
    session.input.click(*hits[0].center)
    with clipboard.Preserved():
        session.input.key("ctrl+a")
        clipboard.set_text(numeric_name)
        session.input.key("ctrl+v")
        session.input.key("enter")
    if domain == "air_wings":
        session.input.click(width // 2 - 250, height // 2 - 225)
    time.sleep(.5)
    return {"domain": domain, "old_name": expected_name, "numeric_name": numeric_name,
            "name_region": region, "input_path": "ordinary UI setup", "save_verification_required": True}


def discover_naval_air_binding(before, after, country, domain, numeric_name, base_id=None):
    """Find which actual own ID was renamed, then prove that was the only change."""
    values = air_wings(after, country) if domain == "air_wings" else fleets(after, country) if domain == "fleets" else None
    if values is None:
        raise ValueError("Unknown binding domain")
    matches = [identity for identity, row in values.items() if (row["wing"] if domain == "air_wings" else row).get("name") == numeric_name]
    if len(matches) != 1:
        return {"verified": False, "checks": {"one_numeric_marker": False}, "matches": matches}
    identity = matches[0]
    result = verify_naval_air_binding(before, after, country, domain, identity, numeric_name)
    old = air_wings(before, country) if domain == "air_wings" else fleets(before, country)
    result["checks"]["new_unique_marker"] = not any((row["wing"] if domain == "air_wings" else row).get("name") == numeric_name for row in old.values())
    if domain == "air_wings" and base_id is not None:
        result["checks"]["expected_own_base"] = identity in old and old[identity]["base_id"] == base_id and values[identity]["base_id"] == base_id
    result["verified"] = all(result["checks"].values())
    return result


def verify_naval_air_binding(before, after, country, domain, identity, numeric_name):
    from agent_contract import country_fingerprint
    expected = copy.deepcopy(after)
    if domain == "air_wings":
        x, y = air_wings(before, country), air_wings(after, country)
        valid = identity in x and identity in y and y[identity]["wing"].get("name") == numeric_name
        if valid:
            air_wings(expected, country)[identity]["wing"]["name"] = x[identity]["wing"].get("name")
    elif domain == "fleets":
        x, y = fleets(before, country), fleets(after, country)
        valid = identity in x and identity in y and y[identity].get("name") == numeric_name
        if valid:
            fleets(expected, country)[identity]["name"] = x[identity].get("name")
    else:
        raise ValueError("Unknown binding domain")
    checks = {"exact_numeric_name": bool(valid), "same_date": before["date"] == after["date"],
              "only_named_identity_changed": all(country_fingerprint(before, tag) == country_fingerprint(expected, tag)
                     and _air_state(before, tag) == _air_state(expected, tag) for tag in before["countries"]),
              "map_unchanged": before.get("states") == after.get("states") and before.get("provinces") == after.get("provinces")}
    return {"verified": all(checks.values()), "checks": checks, "country": country, "domain": domain, "id": identity, "name": numeric_name}


def _exact_lines(session, region, name):
    evidence, hits = [], []
    for language in (("en-US", None) if re.fullmatch(r"[0-9]+", name or "") else (None,)):
        for scale in (2.0, 3.0):
            args = {"region": region, "scale": scale, **({"language": language} if language else {})}
            lines = session.ocr.recognize(session.grab(), **args).lines
            evidence.extend(line.text for line in lines)
            hits.extend(line for line in lines if _normal(line.text) == _normal(name))
        if hits:
            break
    unique = []
    for line in hits:
        if not any(abs(line.center[0] - other.center[0]) <= 12 and abs(line.center[1] - other.center[1]) <= 8 for other in unique):
            unique.append(line)
    return unique, evidence


def selected_air_count(lines):
    text = re.sub(r"\s+", "", " ".join(lines)).lower()
    if re.search(r"\d+/\d+", text):
        return None
    values = set()
    for match in re.finditer(r"(?:(\d+)(?:个)?(?:已选中|已选择|selected|空军联队|联队)|(?:已选中|已选择|selected)[:：]?(\d+))", text):
        values.add(int(match[1] or match[2]))
    return next(iter(values)) if len(values) == 1 else None


def green_air_count(image, font_dir):
    """Read every green counter digit against the installed toolbar font, never an expected count."""
    import numpy as np
    from PIL import Image
    font_dir = Path(font_dir)
    definitions = (font_dir / "hoi_18mbs.fnt").read_text(encoding="utf-8")
    atlas = np.asarray(Image.open(font_dir / "hoi_18mbs.dds").convert("RGBA"))[:, :, 3]
    templates = {}
    for digit in range(10):
        line = next(row for row in definitions.splitlines() if re.match(r"char id=" + str(48 + digit) + r"\s", row))
        fields = {key: int(value) for key, value in re.findall(r"(\w+)=(-?\d+)", line)}
        alpha = atlas[fields["y"]:fields["y"] + fields["height"], fields["x"]:fields["x"] + fields["width"]]
        templates[digit] = []
        for threshold in (200, 220, 240):
            mask = alpha > threshold
            ys, xs = np.where(mask)
            templates[digit].append(mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1])
    crop = np.asarray(image)[145:175, 45:150, :3].astype(float)
    green = (crop[:, :, 1] > crop[:, :, 0] * 1.25) & (crop[:, :, 1] > crop[:, :, 2] * 1.15) & (crop[:, :, 1] > 90)
    columns = np.flatnonzero(green.any(axis=0))
    if not len(columns):
        return None, {"method": "installed_font_green_digits", "digits": []}
    groups = np.split(columns, np.flatnonzero(np.diff(columns) > 1) + 1)
    digits, evidence = [], []
    for group in groups:
        block = green[:, group[0]:group[-1] + 1]
        ys = np.flatnonzero(block.any(axis=1))
        glyph = block[ys[0]:ys[-1] + 1]
        scores = sorted((min((float(np.mean(glyph != template)) for template in rows if template.shape == glyph.shape), default=1.0), digit) for digit, rows in templates.items())
        score, digit = scores[0]
        evidence.append({"digit": digit, "error": score, "runner_up_error": scores[1][0], "box": [int(group[0]) + 45, int(ys[0]) + 145, int(len(group)), int(len(ys))]})
        if score > .12 or scores[1][0] - score < .08:
            return None, {"method": "installed_font_green_digits", "digits": evidence}
        digits.append(str(digit))
    text = "".join(digits)
    value = int(text) if text == "0" or not text.startswith("0") else None
    return value, {"method": "installed_font_green_digits", "digits": evidence, "text": text}


def _air_selection(session, name):
    width, height = session.window.size()
    lines, evidence = _exact_lines(session, [220, 215, min(200, width - 220), height - 240], name)
    if len(lines) != 1:
        raise RuntimeError("Air wing row name is not an exact unique identity: " + str(evidence))
    session.input.click(*lines[0].center)
    time.sleep(.5)
    count_region = [35, 140, 190, 45]
    image = session.grab()
    counts = [line.text for line in session.ocr.recognize(image, region=count_region, scale=2).lines]
    count, glyph_evidence = selected_air_count(counts), None
    header = re.sub(r"\s+", "", " ".join(counts))
    if count is None and "已选中" in header and "个" in header:
        count, glyph_evidence = green_air_count(image, Path(session.cfg.game_dir) / "gfx/fonts")
    if count != 1:
        raise RuntimeError("Air action requires exactly one selected wing: " + str(counts))
    return {"name": name, "row_evidence": evidence, "selected_count_evidence": counts, "selected_count_glyphs": glyph_evidence}


def _select_air(session, country, row, binding):
    from hoi4_live.game import console, panels, mapview
    from hoi4_live.tools.military_tools import _air_counters
    console.close_console(session)
    panels.to_map(session)
    session.input.key("f3")
    time.sleep(.7)
    mapview.go_to_id(session, row["base_state"])
    mapview.zoom_abs(session, "province")
    candidates = _air_counters(session.grab())
    width, height = session.window.size()
    candidates.sort(key=lambda p: (p[0] - width / 2) ** 2 + (p[1] - height / 2) ** 2)
    seen = []
    for point in candidates[:6]:
        session.input.click(*point)
        time.sleep(.7)
        try:
            return _air_selection(session, binding["name"])
        except RuntimeError as error:
            seen.append(str(error))
    raise RuntimeError("No exact own wing at the evidenced base: " + str(seen))


def _select_fleet(session, binding):
    from hoi4_live.game import navalairview
    from hoi4_live.perception import tooltip
    from army_actions import clear_hover
    width, height = session.window.size()
    seen = []
    for cx in navalairview.card_strip(session):
        clear_hover(session)
        tip, _, _ = tooltip.read(session, cx, height - 65, wait=.6)
        lines = tip.get("lines", [])
        exact = any(_normal(re.sub(r"\s*[（(].*?[)）]\s*$", "", line)) == _normal(binding["name"]) for line in lines)
        if re.fullmatch(r"[0-9]+", binding["name"]):
            hits, _ = _exact_lines(session, tip["region"], binding["name"]) if tip.get("found") else ([], [])
            exact = len(hits) == 1
        seen.append(lines[:5])
        if not exact:
            continue
        session.input.click(cx, height - 65)
        time.sleep(.8)
        hits, header = _exact_lines(session, [55, 85, 260, 65], binding["name"])
        if len(hits) != 1:
            raise RuntimeError("Selected fleet header does not confirm the exact saved fleet: " + str(header))
        return {"name": binding["name"], "card_x": cx, "tooltip": lines, "header": header}
    raise RuntimeError("Exact own fleet card not found: " + str(seen))


def _button(session, point, mission):
    from hoi4_live.perception import tooltip
    for dx in (0, -6, 6):
        where = (point[0] + dx, point[1])
        tip, _, _ = tooltip.read(session, *where, wait=.6)
        lines = tip.get("lines", [])
        clean = [re.sub(r"\s+", "", line) for line in lines[:3] if line.strip()]
        if clean and re.search(TOOLTIPS[mission], clean[0], re.I) and not any(re.search(r"不能|不可|disabled|unavailable", line, re.I) for line in clean):
            return where, lines
    raise RuntimeError("Mission tooltip did not confirm " + mission)


def _target_point(session, context, province_id):
    from hoi4_live.game import console, mapview
    from army_actions import target_province_point
    console.close_console(session)
    mapview.zoom_abs(session, "province")
    console.run(session, ["goto_province " + str(province_id)], keep_open=False)
    time.sleep(.8)
    point, evidence = target_province_point(session, province_id, enable_debug=True)
    if point is None:
        raise RuntimeError("Target province numeric identity not confirmed: " + str(evidence))
    return point, evidence


def _region_province(context, region_id, naval=False):
    region = context["map"]["regions"][str(region_id)]
    candidates = [pid for pid in region["provinces"] if context["map"]["provinces"].get(str(pid), {}).get("kind") == ("sea" if naval else "land")]
    if not candidates:
        raise ValueError("Region has no compatible target province")
    aim = region["aim"]
    return min(candidates, key=lambda pid: sum((context["map"]["provinces"][str(pid)]["xy"][i] - aim[i]) ** 2 for i in (0, 1)))


def execute_naval_air_action(session, country, action, state, allow_probe=False, context=None):
    from hoi4_live.game import console, panels, navalairview
    context = context or context_for_session(session)
    kind, args = action["type"], action["args"]
    domain, key = ("air_wings", "wing_id") if kind.startswith("air.") else ("fleets", "fleet_id")
    binding = _binding(context, country, domain, args[key])
    accepted_size = binding.get("ui_profile", {}).get("client_size")
    if accepted_size and accepted_size != list(session.window.size()):
        raise ValueError("Current UI size differs from this object's binding evidence")
    if allow_probe and binding:
        context = copy.deepcopy(context)
        binding = _binding(context, country, domain, args[key])
        binding["accepted_actions"] = sorted(set(binding.get("accepted_actions", []) + [kind]))
    validate_naval_air_action(action, {"naval_air": naval_air_observation(state, country, context)})
    result = {"input_path": "ordinary UI", "action": action, "save_verification_required": True}
    if kind.startswith("air."):
        row = air_wings(state, country)[args["wing_id"]]
        result["selection"] = _select_air(session, country, row, binding)
        mask = row["wing"].get("mission", {}).get("type", 0)
        wanted = AIR_BITS[args["mission"]] if kind == "air.assign" else 0
        for mission, bit in AIR_BITS.items():
            if bool(mask & bit) != bool(wanted & bit):
                point, evidence = _button(session, (navalairview.AIR_BUTTONS[mission], navalairview.AIR_BUTTON_Y), mission)
                result.setdefault("buttons", []).append({"mission": mission, "tooltip": evidence})
                session.input.click(*point)
                time.sleep(.5)
        if kind == "air.assign":
            province = _region_province(context, args["region_id"])
            point, evidence = _target_point(session, context, province)
            # Console camera movement must not have dropped or widened the selection.
            result["selection_after_camera"] = _air_selection(session, binding["name"])
            result["target"] = {"province_id": province, "region_id": args["region_id"], "evidence": evidence}
            session.input.click(*point, button="right")
            time.sleep(.7)
    else:
        console.close_console(session)
        panels.to_map(session)
        session.input.key("f2")
        time.sleep(.7)
        if kind != "navy.stop":
            province = args["province_id"] if kind == "navy.move" else _region_province(context, args["region_id"], naval=True)
            point, evidence = _target_point(session, context, province)
            result["target"] = {"province_id": province, "evidence": evidence}
        result["selection"] = _select_fleet(session, binding)
        if kind == "navy.assign":
            mission = args["mission"]
            dx, dy = navalairview.NAVAL_BUTTONS[mission]
            button, evidence = _button(session, (session.window.size()[0] // 2 + dx, session.window.size()[1] + dy), mission)
            session.input.click(*button)
            time.sleep(.4)
            result["button_tooltip"] = evidence
            session.input.click(*point, button="right")
        elif kind == "navy.move":
            session.input.click(*point, button="right")
        else:
            dx, dy = navalairview.NAVAL_BUTTONS["hold"]
            button, evidence = _button(session, (session.window.size()[0] // 2 + dx, session.window.size()[1] + dy), "hold")
            session.input.click(*button)
            result["button_tooltip"] = evidence
        time.sleep(.7)
    result["screenshot"] = session.artifact(session.grab(), "iron_naval_air_action")
    return result


def main():
    from m0_probe import Probe, countries_from, settled_pause_state, write_json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["status", "bind-begin", "bind-selected", "probe"])
    parser.add_argument("--name")
    parser.add_argument("--country")
    parser.add_argument("--domain", choices=["air_wings", "fleets"])
    parser.add_argument("--numeric-name")
    parser.add_argument("--base-id", type=int)
    parser.add_argument("--name-region", help="Observed name-field x,y,width,height; bind-selected only")
    parser.add_argument("--action", choices=sorted(ACTION_ARGS))
    for key in ("wing-id", "fleet-id", "region-id", "province-id"):
        parser.add_argument("--" + key, type=int)
    parser.add_argument("--mission", choices=["air_superiority", "cas", "patrol", "strike_force"])
    parser.add_argument("--known-disabled-pid", type=int)
    parser.add_argument("--match-config", type=Path, default=ROOT / "configs/match.example.json")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    args = parser.parse_args()
    if args.operation == "status":
        profile = load_profile(args.profile)
        print(json.dumps(profile.get("countries", {}).get(args.country, {}) if args.country else profile, ensure_ascii=False, indent=2))
        return
    if not args.name or not re.fullmatch(r"[A-Za-z0-9_]{1,60}", args.name):
        parser.error("name must contain 1–60 letters, digits or underscores")
    directory = ROOT / "runs/naval_air" / args.name
    begin_path, result_path = directory / "bind_begin.json", directory / "result.json"
    if result_path.exists():
        parser.error("Run already has a result; inspect it instead of replaying")
    baseline = json.loads(begin_path.read_text(encoding="utf-8")) if args.operation == "bind-selected" and begin_path.exists() else None
    tags = baseline.get("countries") if baseline and baseline.get("countries") else countries_from(json.loads(args.match_config.read_text(encoding="utf-8-sig")))
    country = baseline["country"] if baseline else args.country
    pid = baseline["pid"] if baseline else args.known_disabled_pid
    if country not in tags or type(pid) is not int:
        parser.error("Provide a configured country and the controlled game process ID")
    if args.operation == "bind-selected" and baseline is None:
        parser.error("bind-selected requires the same named bind-begin record")
    if args.operation == "bind-begin" and (begin_path.exists() or not args.domain or not re.fullmatch(r"[0-9]{6,10}", args.numeric_name or "")):
        parser.error("bind-begin requires a new name, domain and a unique 6–10 digit numeric-name")
    probe = Probe(directory, tags)
    result = {"operation": args.operation, "country": country, "status": "checking"}
    try:
        with probe.session.lock.hold("IRON naval/air calibration"):
            probe.session.cfg.data["iron_naval_air_profile"] = baseline["profile_path"] if baseline else str(args.profile.resolve())
            status = probe.call("hoi4_status", {})
            if status["game"]["pids"] != [pid] or settled_pause_state(probe.session)[0] != "paused":
                raise ValueError("The same controlled process and a paused game are required")
            process = asdict(probe.session.platform.capture_process_identity(pid))
            if baseline and (process != baseline["process_identity"] or args.country and args.country != country or args.numeric_name and args.numeric_name != baseline["numeric_name"]):
                raise ValueError("Binding process or identity differs from the begin checkpoint")
            if args.operation == "bind-selected":
                probe.session.window.ensure_foreground()
                before = json.loads((directory / (baseline["before_save"]["name"].removeprefix("hoi4live_") + ".json")).read_text(encoding="utf-8"))
                result["before_save"] = baseline["before_save"]
                region = [int(value) for value in args.name_region.split(",")] if args.name_region else None
                if region is not None and (len(region) != 4 or min(region) < 0 or min(region[2:]) == 0):
                    raise ValueError("name-region must be x,y,width,height")
                try:
                    result["ui_result"] = rename_selected_ui(probe.session, baseline["domain"], None, baseline["numeric_name"], region)
                except Exception as error:
                    result["ui_error"] = str(error)
            else:
                result["actor"] = probe.receipts([country], "naval_air_calibrate")
                result["before_save"] = probe.save("iron_naval_air_" + args.name + "_before")
                before = json.loads((directory / (result["before_save"]["name"].removeprefix("hoi4live_") + ".json")).read_text(encoding="utf-8"))
                if args.operation == "bind-begin":
                    values = air_wings(before, country) if args.domain == "air_wings" else fleets(before, country)
                    if any((row["wing"] if args.domain == "air_wings" else row).get("name") == args.numeric_name for row in values.values()):
                        raise ValueError("Numeric marker already exists before binding")
                    baseline = {"country": country, "domain": args.domain, "numeric_name": args.numeric_name, "base_id": args.base_id,
                                "countries": tags,
                                "pid": pid, "process_identity": process, "profile_path": str(args.profile.resolve()),
                                "before_save": result["before_save"], "status": "awaiting_own_UI_selection"}
                    write_json(begin_path, baseline)
                    from hoi4_live.game import console, panels
                    console.close_console(probe.session)
                    panels.to_map(probe.session)
                    result["status"] = "awaiting_own_UI_selection"
                    print("Baseline saved. Open the own fleet name or own wing details, then run bind-selected with this name.")
                    return
                if not args.action:
                    raise ValueError("probe requires an action")
                values = {"wing_id": args.wing_id, "fleet_id": args.fleet_id, "region_id": args.region_id,
                          "province_id": args.province_id, "mission": args.mission}
                action = {"id": "calibration", "type": args.action, "args": {key: values[key] for key in ACTION_ARGS[args.action]}}
                result["action"] = action
                context = context_for_session(probe.session)
                domain, identity_key = ("air_wings", "wing_id") if args.action.startswith("air.") else ("fleets", "fleet_id")
                binding = _binding(context, country, domain, action["args"][identity_key])
                if not binding:
                    raise ValueError("Bind the own object before probing it")
                if args.region_id is not None:
                    binding["region_ids"] = sorted(set(binding.get("region_ids", []) + [args.region_id]))
                if args.province_id is not None:
                    binding["province_ids"] = sorted(set(binding.get("province_ids", []) + [args.province_id]))
                if args.mission:
                    binding["missions"] = sorted(set(binding.get("missions", []) + [args.mission]))
                try:
                    result["ui_result"] = execute_naval_air_action(probe.session, country, action, before, allow_probe=True, context=context)
                except Exception as error:
                    result["ui_error"] = str(error)
            result["after_save"] = probe.save("iron_naval_air_" + args.name + "_after")
            after = json.loads((directory / (result["after_save"]["name"].removeprefix("hoi4live_") + ".json")).read_text(encoding="utf-8"))
            if args.operation == "bind-selected":
                proof = discover_naval_air_binding(before, after, country, baseline["domain"], baseline["numeric_name"], baseline.get("base_id"))
                profile_path = Path(baseline["profile_path"])
                profile = load_profile(profile_path)
                if proof["verified"]:
                    profile.setdefault("countries", {}).setdefault(country, {}).setdefault(baseline["domain"], {})[str(proof["id"])] = {
                        "name": baseline["numeric_name"], "accepted_actions": [], "missions": [], "region_ids": [], "province_ids": [],
                        "ui_profile": {"client_size": list(probe.session.window.size()), "binding_name_region": result.get("ui_result", {}).get("name_region")},
                        "binding_evidence": str(result_path)}
            else:
                proof = verify_naval_air_action(action, before, after, country)
                profile_path, profile = args.profile, context["profile"]
                if proof["verified"]:
                    binding["accepted_actions"] = sorted(set(binding.get("accepted_actions", []) + [args.action]))
                    binding.setdefault("acceptance_evidence", {})[args.action] = str(result_path)
            result.update(verification=proof, status="verified" if proof["verified"] else "needs_inspection")
            if proof["verified"]:
                write_json(profile_path, profile)
    except Exception as error:
        result.update(status="interrupted", error=str(error))
        raise
    finally:
        if args.operation != "bind-begin" or not begin_path.exists():
            write_json(result_path, result)
        probe.session.shutdown()
        print(json.dumps({key: result.get(key) for key in ("status", "error", "verification", "ui_error")}, ensure_ascii=False))
    if result["status"] != "verified":
        raise SystemExit(2)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
