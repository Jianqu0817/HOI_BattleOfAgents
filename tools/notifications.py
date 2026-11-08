"""Acknowledge news only when the installed event option contains no effects."""
import hashlib
from pathlib import Path
import time


def passive_option(option):
    return isinstance(option, dict) and isinstance(option.get("name"), str) and set(option) <= {"name", "trigger", "ai_chance", "default"}


def news_index(session):
    if hasattr(session, "_iron_news_index"):
        return session._iron_news_index
    from hoi4_live.gamedata import scriptdb, loc
    entries, keys = [], []
    for path in scriptdb.effective_files("events", session.cfg.game_dir, scriptdb.mods_for(session)):
        if "news_event" not in Path(path).read_text(encoding="utf-8-sig", errors="replace"):
            continue
        block = scriptdb.parse_file(path)
        for event in scriptdb.as_list(block.get("news_event")):
            if not isinstance(event, dict) or event.get("major") != "yes" or event.get("after") or not isinstance(event.get("title"), str) or not isinstance(event.get("id"), str):
                continue
            for option in scriptdb.as_list(event.get("option")):
                if passive_option(option):
                    entries.append({"event": event.get("id"), "major": "yes", "title_key": event.get("title"),
                                    "option_key": option["name"], "option_keys": sorted(option), "source": str(path)})
                    keys.extend([event.get("title"), option["name"]])
    names = loc.for_session(session).get_many([key for key in keys if isinstance(key, str)])
    session._iron_news_index = entries, names
    return entries, names


def acknowledge_passive_news(session):
    from hoi4_live.perception import popups
    from hoi4_live.game import panels
    image = session.grab()
    found = popups.detect(image)
    if not found:
        return []
    popup = found[0]
    if popup.kind != "news" or len(popup.buttons) != 1:
        return []
    popups.read_popup(image, popup, session.ocr)
    if not panels.norm(popup.title) or not panels.norm(popup.buttons[0].text):
        return []
    entries, names = news_index(session)
    matching = [entry for entry in entries
                if panels.norm(names.get(entry["title_key"])) == panels.norm(popup.title)
                and panels.norm(names.get(entry["option_key"])) == panels.norm(popup.buttons[0].text)]
    if len(matching) != 1:
        return []
    proof = matching[0]
    proof = {**proof, "source_sha256": hashlib.sha256(Path(proof["source"]).read_bytes()).hexdigest()}
    record = {"kind": "passive_news", "title": popup.title, "text": popup.text,
              "option": popup.buttons[0].text, "proof": proof,
              "screenshot": session.artifact(image, "iron_passive_news")}
    session.input.click(*popup.buttons[0].center)
    time.sleep(.9)
    remaining = popups.detect(session.grab())
    for other in remaining:
        if other.kind == "news":
            popups.read_popup(session.grab(), other, session.ocr)
            if panels.norm(other.title) == panels.norm(popup.title):
                raise RuntimeError("Passive news remains visible after acknowledgement")
    if not hasattr(session, "_iron_public_notices"):
        session._iron_public_notices = []
    session._iron_public_notices.append(record)
    return [record]
