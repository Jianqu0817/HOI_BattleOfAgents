"""Ordinary player actions for IRON's verified Chinese 720p profile."""
import time
import re


def focus_start(session, focus_id, known):
    from hoi4_live.game import panels, focusview
    from hoi4_live.gamedata import focus, loc
    saved = known.get("focus", {})
    if saved.get("current"):
        raise ValueError("A focus is already running")
    index = focus.for_session(session)
    focuses = focus.tree_focuses(index, known["focus_tree"])
    item = focuses.get(focus_id)
    completed = saved.get("completed", [])
    completed = completed if isinstance(completed, list) else [completed]
    if item is None or focus.status(item, completed, None) != "available":
        raise ValueError("Requested focus is not an installed candidate")
    names = loc.for_session(session).get_many(list(focuses))
    name = names.get(focus_id)
    if not name or "[" in name:
        raise ValueError("This action requires a static localised focus name")
    focusview.open_tree(session)
    found = focusview.search(session, name)
    if not found or found[0] != 1 or found[1] < 1:
        raise RuntimeError(f"Focus search has no confirmed first result: {found}")
    session.input.key("enter")
    time.sleep(1)
    image = session.grab()
    evidence = session.artifact(image, "iron_focus_detail")
    detail = focusview.read_detail(session, img=image)
    if not detail or focusview.title_focus_id(detail.get("title"), names) != focus_id:
        raise RuntimeError(f"Focus identity not confirmed: {detail}; evidence={evidence}")
    if detail.get("start_enabled") is not True or detail.get("unmet"):
        raise ValueError("The focus Start button or requirements are not satisfied")
    x, y, w, h = detail["start_button"]
    session.input.click(x + w // 2, y + h // 2, hold=.15)
    time.sleep(1)
    detail["days_ocr"] = detail.pop("days", None)
    return {"focus": focus_id, "name": name, "expected_days": int(item["cost"] * focus.DAYS_PER_COST),
            "search_matches": found, "detail": detail, "evidence": evidence,
            "input_path": "ordinary UI", "save_verification_required": True}


def construction_queue_one(session, country, state_id, building, known):
    from hoi4_live.game import panels, mapview
    from hoi4_live.gamedata import loc
    from hoi4_live.perception import tooltip
    if building != "industrial_complex":
        raise ValueError("This verified profile currently queues civilian factories only")
    state = known["states"].get(str(state_id))
    if state is None or state.get("owner") != country:
        raise ValueError("Target state is not owned by the acting country")
    panels.open_panel(session, "construction")
    names = loc.for_session(session).get_many(["industrial_complex"] + [f"STATE_{sid}" for sid in known["states"]])
    camera = mapview.go_to_id(session, state_id)
    # At 720p the construction panel covers the camera centre. Move it into the map.
    session.input.drag(925, 360, 1205, 360, button="middle", seconds=.8)
    session.input.move(80, 100)
    time.sleep(.4)
    target = (920, 400)
    place, _, _ = tooltip.read(session, *target)
    observed = re.search(r"属于(.+?)的", re.sub(r"\s+", "", " ".join(place.get("lines", []))))
    scores = [(panels.similar(observed[1], names.get(f"STATE_{sid}", "")), int(sid)) for sid in known["states"]] if observed else []
    hits = [sid for score, sid in scores if score >= .75]
    debug_states = {int(match[1]) for line in place.get("lines", [])
                    if (match := re.search(r"StateID[:：=]?(\d+)", re.sub(r"\s+", "", line), re.I))}
    if hits != [state_id] and not (not hits and debug_states == {state_id}):
        raise RuntimeError(f"Target state not uniquely identified: {place}; candidates={hits}")
    session.input.move(80, 100)
    time.sleep(.3)
    button, _, _ = tooltip.read(session, 521, 290)
    if panels.norm(names["industrial_complex"]) not in {panels.norm(line) for line in [button.get("title", ""), *button.get("lines", [])]}:
        raise RuntimeError(f"Civilian factory button not confirmed: {button}")
    evidence = session.artifact(session.grab(), "iron_construction_target")
    session.input.click(521, 290, hold=.15)
    time.sleep(.6)
    session.input.click(*target, hold=.15)
    time.sleep(.6)
    session.input.click(521, 290, hold=.15)
    return {"state": state_id, "building": building, "count": 1, "camera": camera,
            "target_tooltip": place, "button_tooltip": button, "evidence": evidence,
            "input_path": "ordinary UI", "save_verification_required": True}


def research_tile_point(session, image, expected_names):
    import numpy as np
    from hoi4_live.game import panels
    labels = {panels.norm(label) for label in expected_names}
    codes = {panels.norm(token) for label in expected_names for token in re.findall(r"[A-Za-z][A-Za-z0-9./-]*", label)
             if len(panels.norm(token)) >= 4 and any(char.isdigit() for char in token)}
    # White lettering on gold available-tech cards needs its background removed.
    high_contrast = np.repeat(np.where(image.min(2) > 175, 0, 255).astype("uint8")[:, :, None], 3, axis=2)
    for observed in (image, high_contrast):
        texts = session.ocr.recognize(observed, region=[0, 145, image.shape[1], image.shape[0] - 165], scale=2).lines
        hits = [line for line in texts if panels.norm(line.text) in labels | codes]
        if len(hits) == 1:
            return int(hits[0].center[0]), int(hits[0].center[1]) + 24
    return None


def research_start(session, country, technology, slot, known):
    from hoi4_live.game import panels, researchview
    from hoi4_live.gamedata import tech, loc
    index = tech.for_session(session)
    item = index["techs"].get(technology)
    if item is None:
        raise ValueError("Unknown technology ID")
    learned = [key for key, value in known.get("technologies", {}).items() if isinstance(value, dict) and value.get("level")]
    if tech.status(item, learned, list(known.get("slots", {}))) != "available":
        raise ValueError("Technology is not an available candidate")
    folder = item["folders"][0]["name"]
    names = loc.for_session(session).get_many(tech.display_keys(item, country))
    expected_names = list(names.values())
    panels.open_panel(session, "research")
    session.input.move(80, 100)
    time.sleep(.8)
    slots = researchview.read_slots_settled(session)
    chosen = next((row for row in slots if row["index"] == slot and row["state"] == "idle"), None)
    if chosen is None:
        raise ValueError("Requested research slot is not visibly idle")
    x, y, w, h = chosen["box"]
    session.input.click(x + w // 2, y + 40, hold=.15)
    time.sleep(1)
    session.input.click(*researchview.tab_point(index, folder), hold=.15)
    time.sleep(.8)
    session.input.move(1100, 140)
    time.sleep(.4)
    image = session.grab()
    point = research_tile_point(session, image, expected_names)
    if point is None:
        calibration = researchview.calibrate(image, index, folder)
        if calibration is None:
            raise RuntimeError("Technology grid could not be located")
        offset = tech.tile_offset(index, technology, folder, image.shape[1])
        point = (round(calibration[0] + offset[0]), round(calibration[1] + offset[1]))
        if point[1] >= image.shape[0] - 35:
            session.input.scroll(850, 420, -3)
            session.input.move(1100, 140)
            time.sleep(.5)
            image = session.grab()
            point = research_tile_point(session, image, expected_names)
            if point is None:
                raise RuntimeError("Target technology is not identified after scrolling")
    session.input.click(*point, hold=.15)
    time.sleep(1)
    image = session.grab()
    evidence = session.artifact(image, "iron_research_detail")
    detail = researchview.read_detail(session, img=image)
    if not detail or not any(panels.similar(detail.get("title"), label) >= .8 for label in expected_names):
        raise RuntimeError(f"Technology detail not confirmed: {detail}; evidence={evidence}; point={point}")
    bx, by, bw, bh = detail["research_button"]
    session.input.click(bx + bw // 2, by + bh // 2, hold=.15)
    time.sleep(1)
    return {"country": country, "technology": technology, "slot": slot, "detail": detail,
            "detail_evidence": evidence, "input_path": "ordinary UI", "save_verification_required": True}


def production_assign_one(session, display_line, target, known, expected_name=None):
    from hoi4_live.game import panels, productionview
    military = known["military_lines"]
    military = military if isinstance(military, list) else [military]
    selected = next((line for line in military if line.get("priority") == int(display_line) - 1), None)
    if selected is None:
        raise ValueError("Requested visible line is not a known military production line")
    difference = target - selected["requested_factories"]
    if difference not in (-1, 1):
        raise ValueError("This verified action changes one factory per call")
    panels.open_panel(session, "production")
    productionview.scroll_list(session, 40)
    session.input.move(80, 100)
    time.sleep(.6)
    image = session.grab()
    if expected_name:
        # Circled row numbers can be absent from Chinese OCR. Identify the saved
        # variant name at the expected visible row after resetting the scrollbar.
        expected_y = productionview.LIST_TOP + 17 + (int(display_line) - 1) * productionview.LINE_PITCH
        rows = session.ocr.recognize(image, region=[0, productionview.LIST_TOP, 360, image.shape[0] - productionview.LIST_TOP - 8], scale=2).lines
        labels = expected_name if isinstance(expected_name, list) else [expected_name]
        hits = [row for row in rows if abs(row.box[1] - expected_y) <= 12 and any(panels.norm(label) in panels.norm(row.text) for label in labels)]
        line = {"row_top": hits[0].box[1] - 7, "observed_title": hits[0].text} if len(hits) == 1 else None
        if line is None and expected_y < image.shape[0] - 35:
            # Read the title without the circled index or warning icon. Mixed
            # Latin/Chinese fonts can merge "Mk I" into a single Chinese glyph.
            headers = session.ocr.recognize(image, region=[45, expected_y - 5, 310, 35], scale=3).lines
            codes = {panels.norm(token) for label in labels for token in re.findall(r"[A-Za-z][A-Za-z0-9./-]*", label)
                     if len(panels.norm(token)) >= 3 and any(char.isdigit() for char in token)}
            matches = [row for row in headers if max(panels.similar(label, row.text) for label in labels) >= .8
                       and any(code in panels.norm(row.text) for code in codes)]
            if len(matches) == 1:
                line = {"row_top": matches[0].box[1] - 7, "observed_title": matches[0].text}
    else:
        line = next((row for row in productionview.read_lines(session, img=image) if row["number"] == int(display_line)), None)
    if line is None:
        raise RuntimeError("Requested production line is not visible")
    evidence = session.artifact(session.grab(), "iron_production_before")
    x = productionview.ROW["plus" if difference > 0 else "minus"]
    session.input.click(x, line["row_top"] + 12, hold=.15)
    time.sleep(.5)
    return {"line_id": selected["id"], "before": selected["requested_factories"], "target": target,
            "display_line": display_line, "evidence": evidence, "input_path": "ordinary UI",
            "expected_variant_name": expected_name, "observed_title": line.get("observed_title", line.get("name")), "save_verification_required": True}
