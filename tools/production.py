"""Audience-only character, voice and evidence-linked editing sidecars."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load_registry(path):
    registry = read(path)
    if registry.get("schema") != "iron.characters.v1":
        raise ValueError("Unsupported character registry")
    for identity, row in registry["characters"].items():
        if not identity.replace("_", "").isalnum() or "neutral" not in row.get("portraits", {}):
            raise ValueError("Character requires a safe ID and neutral portrait")
        if row.get("card_file"):
            card_path = Path(row["card_file"])
            card = read(card_path if card_path.is_absolute() else ROOT / card_path)
            if (card.get("schema") != "iron.character-card.v1"
                    or not isinstance(card.get("persona"), str)
                    or not isinstance(card.get("character_card", {}).get("display_name"), str)):
                raise ValueError("Invalid character card: " + identity)
            row["card"] = card
    return registry


def configure_character_show(config):
    """Freeze authored personas and an explicitly public cast into a new campaign config."""
    show = config["show"]
    if not show.get("use_character_cards"):
        return
    from showrunner import country_model
    production = config["production"]
    path = Path(production["registry"])
    registry = load_registry(path if path.is_absolute() else ROOT / path)
    names = {"GER": "德国", "FRA": "法国", "ITA": "意大利", "AUS": "奥地利", "SWI": "瑞士"}
    cast = []
    for slot in [*config["countries"], "host"]:
        identity = production["assignments"][slot]
        card = registry["characters"][identity]["card"]
        face = card["character_card"]
        profile = {"name": face["display_name"], "persona": card["persona"]}
        if slot == "host":
            show["host_profile"] = profile
        else:
            show["profiles"][slot] = {**show["profiles"].get(slot, {}), **profile}
        model = config["model"] if slot == "host" else country_model(config["model"], show, slot)
        cast.append({"country": None if slot == "host" else slot,
                     "country_name": "主持" if slot == "host" else names.get(slot, slot),
                     "role": "host" if slot == "host" else "player", "character_id": identity,
                     "name": face["display_name"], "aliases": copy.deepcopy(face["aliases"]),
                     "character_model": card["model_family"], "execution_model": model["model"],
                     "self_intro": face["self_intro"]})
    show["public_cast"] = cast
    rules_path = Path(show["character_rules_file"])
    rules = (rules_path if rules_path.is_absolute() else ROOT / rules_path).read_text(encoding="utf-8-sig")
    show["common_rules"] += "\n" + rules.replace("{cast}", "见 public_cast 公开演员表")
    show["common_rules"] += ("\n比赛规则解释：角色卡描述性格，不强制兑现外交承诺；允许合作、隐瞒、撒谎和战术欺骗。"
                             "不得伪造系统观察、执行回执或已发生操作，私密命令始终保密。"
                             "public_cast.character_model 是角色拟人的品牌，execution_model 才是实际调用模型；"
                             "测试中同一模型可以扮演不同角色，不代表不同品牌模型已经参赛。")


def make_lines(episode, assignments, *, event_ids=None):
    """Freeze verbatim dialogue only; technical receipts are not character speech."""
    lines = []
    allowed = {"public", "private", "private_reply", "interview", "host", "commentary"}
    selected = None if event_ids is None else set(event_ids)
    for event in episode["events"]:
        if event["kind"] not in allowed or (selected is not None and event["id"] not in selected):
            continue
        narrator = assignments.get("director", assignments.get("host")) if event["kind"] == "commentary" else assignments.get("host") if event["kind"] == "host" else None
        character = assignments.get(event.get("country"), narrator)
        if not character:
            continue
        text = event["text"]
        identity = hashlib.sha256((event["id"] + "\0" + character + "\0" + text).encode()).hexdigest()[:20]
        lines.append({"line_id": identity, "character_id": character, "text": text,
                      "event_id": event["id"], "visibility": event["visibility"],
                      "country": event.get("country"), "emotion": event.get("data", {}).get("emotion", "neutral")})
    return lines


def editing_plan(episode, *, target_seconds=180):
    """Rule-based draft; cuts preserve evidence IDs and never invent footage timecodes."""
    if type(target_seconds) is not int or not 30 <= target_seconds <= 7200:
        raise ValueError("target_seconds must be 30..7200")
    priority = {"war": 10, "public_event": 10, "outcome": 7, "public": 6,
                "private_reply": 6, "interview": 5, "host": 5, "private": 4,
                "decision": 4, "execution": 2, "intent": 3, "referee": 2, "commentary": 7}
    ranked = sorted(enumerate(episode["events"]), key=lambda x: (-priority.get(x[1]["kind"], 1), x[0]))
    selected, spent = {}, 0
    for index, event in ranked:
        duration = max(3, min(18, len(event.get("text", "")) / 5))
        if spent + duration <= target_seconds:
            selected[index] = duration
            spent += duration
    cuts, position = [], 0
    for index, event in enumerate(episode["events"]):
        if index not in selected:
            continue
        duration = selected[index]
        cuts.append({"evidence_id": event["id"], "kind": event["kind"], "visibility": event["visibility"],
                     "editorial_in": round(position, 3), "editorial_out": round(position + duration, 3),
                     "source_recorded_at": event.get("recorded_at"), "source_game_date": event.get("game_date"),
                     "recording_in": None, "recording_out": None,
                     "mode": "dialogue" if event["kind"] in {"public", "private", "private_reply", "interview", "host"} else "summary"})
        position += duration
    return {"schema": "iron.editing.v1", "audience": "audience_full", "method": "deterministic_draft",
            "target_seconds": target_seconds, "editorial_seconds": round(position, 3), "cuts": cuts,
            "omitted_evidence_ids": [row["id"] for index, row in enumerate(episode["events"]) if index not in selected],
            "quiet_time": {"mode": "summary_or_timelapse", "recording_speed": None,
                           "notice": "须用OBS锚点对齐录屏后填写实际时间码与倍速。游戏日期、真实UTC和剪辑秒数分别记录。"}}


def export_production(output, config, *, synthesize=False):
    """Enrich an exported episode. Nothing produced here is player input."""
    output = Path(output)
    episode = read(output / "episode.json")
    registry_path = Path(config.get("registry", "configs/characters.example.json"))
    if not registry_path.is_absolute():
        registry_path = ROOT / registry_path
    registry = load_registry(registry_path)
    assignments = config.get("assignments", {})
    if set(assignments.values()) - set(registry["characters"]):
        raise ValueError("Unknown character assignment")
    plan = editing_plan(episode, target_seconds=config.get("target_seconds", 180))
    write(output / "editing_plan.json", plan)
    lines = make_lines(episode, assignments, event_ids=[row["evidence_id"] for row in plan["cuts"]])
    characters = {}
    for identity in set(assignments.values()):
        row = registry["characters"][identity]
        portraits = {}
        for emotion, filename in row["portraits"].items():
            source = Path(filename)
            if not source.is_absolute():
                source = ROOT / source
            if source.is_file():
                relative = Path("characters") / identity / (emotion + source.suffix.lower())
                (output / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, output / relative)
                portraits[emotion] = relative.as_posix()
        characters[identity] = {"name": row["name"], "portraits": portraits,
                                "card_file": row.get("card_file"),
                                "card_status": "ready" if row.get("card") else "user_pending"}
        if row.get("card"):
            characters[identity]["character_card"] = row["card"]["character_card"]
            characters[identity]["voice"] = row["card"].get("voice", {})
    write(output / "characters.json", {"characters": characters, "assignments": assignments})
    (output / "voice_lines.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in lines), encoding="utf-8")
    receipts = []
    if synthesize:
        from gpt_sovits import synthesize as speak
        tts_path = Path(config["tts_config"])
        if not tts_path.is_absolute():
            tts_path = ROOT / tts_path
        tts = read(tts_path)
        receipts = [speak(tts, line, output / "voices") for line in lines]
    write(output / "voice_receipts.json", receipts)
    audio_by_line = {row["line_id"]: row for row in receipts if row.get("status") in {"generated", "cached"}}
    cards = []
    for line in lines:
        character = characters[line["character_id"]]
        portrait = character["portraits"].get(line["emotion"], character["portraits"].get("neutral"))
        image = '<img src="' + html.escape(portrait, quote=True) + '">' if portrait else ''
        choices = ''.join('<option value="' + html.escape(filename, quote=True) + '">' + html.escape(emotion) + '</option>'
                          for emotion, filename in character["portraits"].items())
        picker = '<select onchange="this.closest(\'article\').querySelector(\'img\').src=this.value">' + choices + '</select>' if choices else ''
        receipt = audio_by_line.get(line["line_id"])
        audio = '<audio controls src="' + html.escape(Path(receipt["path"]).relative_to(output.resolve()).as_posix(), quote=True) + '"></audio>' if receipt else ''
        cards.append('<article>' + image + '<div><small>' + html.escape(line["event_id"] + " · " + line["visibility"]) +
                     '</small><h2>' + html.escape(character["name"]) + '</h2>' + picker + '<p>' + html.escape(line["text"]) + '</p>' + audio + '</div></article>')
    (output / "cast.html").write_text('<!doctype html><meta charset="utf-8"><title>IRON 角色台词预览</title><style>body{background:#101728;color:#eee;font:20px system-ui;max-width:1100px;margin:auto}article{display:flex;border-bottom:1px solid #456;padding:20px;gap:24px}img{width:260px;object-fit:contain}p{white-space:pre-wrap}small{color:#abc}</style><h1>观众专用 · 角色与台词</h1>' + ''.join(cards), encoding="utf-8")
    return {"lines": len(lines), "characters": len(characters),
            "voices": sum(row.get("status") in {"generated", "cached"} for row in receipts),
            "voice_statuses": {status: sum(row.get("status") == status for row in receipts)
                               for status in sorted({row.get("status") for row in receipts})},
            "editing_seconds": plan["editorial_seconds"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--config", default="configs/production.example.json")
    parser.add_argument("--synthesize", action="store_true")
    args = parser.parse_args()
    result = export_production(args.episode, read(args.config), synthesize=args.synthesize)
    print(json.dumps(result, ensure_ascii=False))
    if args.synthesize and result["voices"] != result["lines"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
