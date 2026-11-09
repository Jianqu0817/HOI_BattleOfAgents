"""Acknowledge news only when the installed event option contains no effects."""
from pathlib import Path


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
