"""Own-fleet/wing missions through calibrated ordinary Chinese UI, with exact save verification."""
import copy
import hashlib
import json
from pathlib import Path
import re


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


if __name__ == "__main__":
    raise SystemExit("此模块用于读取旧记录；对局入口为 tools/native_campaign.py。")
