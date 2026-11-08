"""Package completed episodes and an editable, silent evidence-card rough cut.

Offline only: no model calls, game commands, or speech generation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

from production import load_registry, make_lines, read, ROOT, write


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def excerpt(text, limit=140):
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[:limit] + "…（节选）"


def card(event, character, portrait, target):
    image = Image.new("RGB", (1280, 720), "#111a2a")
    draw = ImageDraw.Draw(image)
    regular = "C:/Windows/Fonts/msyh.ttc"
    bold = "C:/Windows/Fonts/msyhbd.ttc"
    draw.text((48, 35), "IRON · 历史对局复盘", font=ImageFont.truetype(bold, 34), fill="#eef4ff")
    label = {"public": "公开发言", "private": "私信", "intent": "事前意图摘要", "decision": "密封决策",
             "referee": "裁判审核", "execution": "执行回执", "outcome": "状态回读", "host": "公开主持",
             "interview": "私密采访", "commentary": "观众导演点评"}[event["kind"]]
    draw.text((48, 100), f'{event["id"]} · {label} · {character}', font=ImageFont.truetype(bold, 25), fill="#86d9ee")
    draw.text((48, 145), "公开" if event["visibility"] == "public" else "私密 / 观众专用 · 不进入公共频道",
              font=ImageFont.truetype(regular, 22), fill="#d9b987")
    font = ImageFont.truetype(regular, 29)
    text, lines, line = excerpt(event["text"]), [], ""
    for char in text:
        if draw.textbbox((0, 0), line + char, font=font)[2] > 790:
            lines.append(line)
            line = char
        else:
            line += char
    lines.append(line)
    for index, line in enumerate(lines):
        draw.text((48, 225 + index * 48), line, font=font, fill="#f0f3f8")
    if portrait and portrait.is_file():
        with Image.open(portrait) as original:
            actor = original.convert("RGBA")
        actor.thumbnail((350, 510))
        image.paste(actor, (900 + (350 - actor.width) // 2, 180 + 510 - actor.height), actor)
    draw.text((48, 657), "无配音布局样片 · 非历史实录 · 原文及证据见素材档案",
              font=ImageFont.truetype(regular, 21), fill="#aab8ce")
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)


def bundle(campaign, output, production, *, ffmpeg=None):
    campaign, output = Path(campaign).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = read(campaign / "manifest.json")
    entries = [row for row in manifest["turns"] if row["status"] == "completed"]
    if not entries:
        raise ValueError("No completed turns to package")
    registry = load_registry(ROOT / production["registry"])
    files, seen_assets, timeline, queue, frozen, kind_counts = [], {}, [], [], [], Counter()

    def copy(source, relative):
        source, target = Path(source), output / relative
        digest = sha(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append({"source": str(source), "file": Path(relative).as_posix(), "sha256": digest})
        return target

    def shared_asset(source):
        digest = sha(source)
        if digest not in seen_assets:
            relative = Path("shared/assets") / (digest + Path(source).suffix.lower())
            copy(source, relative)
            seen_assets[digest] = relative.as_posix()
        return seen_assets[digest]

    for entry in entries:
        folder = f'turn_{entry["index"]:03d}'
        source = campaign / "episodes" / folder
        target = output / "episodes" / folder
        target.mkdir(parents=True, exist_ok=True)
        episode = read(source / "episode.json")
        frozen.append(episode)
        replacements = {}
        for path in source.glob("characters/*/*.png"):
            replacements[path.relative_to(source).as_posix()] = "../../" + shared_asset(path)
        for path in source.iterdir():
            if path.is_file() and path.suffix in {".json", ".jsonl", ".csv", ".md", ".html", ".txt", ".srt"}:
                # Audio is deliberately excluded. Source evidence stays untouched.
                if path.name in {"cast.html", "characters.json"}:
                    text = path.read_text(encoding="utf-8")
                    for old, new in replacements.items():
                        text = text.replace(old, new)
                    (target / path.name).write_text(text, encoding="utf-8")
                else:
                    copy(path, Path("episodes") / folder / path.name)
        if (source / "assets").exists():
            for path in (source / "assets").rglob("*"):
                if path.is_file():
                    copy(path, Path("episodes") / folder / path.relative_to(source))
        evidence_map = {}
        for event in episode["events"]:
            kind_counts[event["kind"]] += 1
            timeline.append({"turn": entry["index"], "evidence_id": event["id"], "kind": event["kind"],
                             "country": event.get("country"), "visibility": event["visibility"],
                             "game_date": event.get("game_date"), "recorded_at_utc": event.get("recorded_at"),
                             "text": event["text"], "recording_in": None, "recording_out": None})
            source_file = event.get("source", {}).get("file")
            if source_file and source_file not in evidence_map:
                prefix, relative = source_file.split("/", 1)
                if prefix not in {"trial", "turn"}:
                    raise ValueError("Unknown event evidence scope: " + prefix)
                base = Path(entry[prefix]).resolve()
                original = (base / relative).resolve()
                if not original.is_relative_to(base):
                    raise ValueError("Evidence leaves its source directory")
                packed = Path("evidence") / folder / prefix / relative
                copy(original, packed)
                evidence_map[source_file] = packed.as_posix()
        write(target / "evidence_map.json", evidence_map)
        # All eligible dialogue is queued for later server selection, never synthesized here.
        for line in make_lines(episode, production["assignments"]):
            queue.append({**line, "turn": entry["index"], "status": "pending_server_synthesis",
                          "suggested_duration_seconds": round(max(2, len(line["text"]) / 5), 2),
                          "duration_is_estimate": True})
    current_cast = {}
    for identity, row in registry["characters"].items():
        current_cast[identity] = {"name": row["name"], "portrait": shared_asset(ROOT / row["portraits"]["neutral"])}
    write(output / "current_cast.json", current_cast)
    write(output / "campaign_summary.json", {"name": manifest["name"], "frozen_status": manifest["status"],
          "completed_turns": len(entries), "planned_turns": manifest["config"]["turn_limit"],
          "event_count": len(timeline), "event_kinds": dict(kind_counts), "usage_at_collection": manifest["usage"],
          "model_switches": manifest.get("model_switches", []), "audience": "audience_full"})
    copy(campaign / "manifest.json", "evidence/campaign_manifest.json")
    (output / "voice_queue_all.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in queue), encoding="utf-8")
    with (output / "evidence_timeline.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(timeline[0]))
        writer.writeheader()
        writer.writerows(timeline)

    # One latest-turn example per channel, keeping the evidence order.
    latest = frozen[-1]
    wanted = {"public", "intent", "private", "decision", "referee", "execution", "outcome", "host", "interview", "commentary"}
    selected = []
    for event in latest["events"]:
        if event["kind"] in wanted:
            selected.append(event)
            wanted.remove(event["kind"])
    cuts = []
    playlist = ET.Element("mlt", {"producer": "tractor"})
    ET.SubElement(playlist, "profile", {"description": "HD 720p25", "width": "1280", "height": "720",
                  "frame_rate_num": "25", "frame_rate_den": "1", "progressive": "1",
                  "sample_aspect_num": "1", "sample_aspect_den": "1", "display_aspect_num": "16", "display_aspect_den": "9", "colorspace": "709"})
    for index, event in enumerate(selected, 1):
        narrator = "host" if event["kind"] in {"host", "commentary", "referee"} else event.get("country")
        identity = production["assignments"].get(narrator)
        actor = current_cast.get(identity, {})
        portrait = output / actor["portrait"] if actor else None
        card(event, actor.get("name", "游戏事实记录"), portrait, output / f"frames/{index:02d}.png")
        cuts.append({"evidence_id": event["id"], "kind": event["kind"], "visibility": event["visibility"],
                     "editorial_in": (index - 1) * 8, "editorial_out": index * 8,
                     "recording_in": None, "recording_out": None, "text_excerpt": excerpt(event["text"])})
        producer = ET.SubElement(playlist, "producer", {"id": f"frame{index}", "in": "00:00:00.000", "out": "00:00:07.960"})
        ET.SubElement(producer, "property", {"name": "resource"}).text = f"frames/{index:02d}.png"
        ET.SubElement(producer, "property", {"name": "mlt_service"}).text = "qimage"
    track = ET.SubElement(playlist, "playlist", {"id": "main_bin"})
    ET.SubElement(track, "property", {"name": "xml_retain"}).text = "1"
    for index in range(1, len(selected) + 1):
        ET.SubElement(track, "entry", {"producer": f"frame{index}", "in": "00:00:00.000", "out": "00:00:07.960"})
    video_track = ET.SubElement(playlist, "playlist", {"id": "V1"})
    ET.SubElement(video_track, "property", {"name": "shotcut:name"}).text = "V1"
    for index in range(1, len(selected) + 1):
        ET.SubElement(video_track, "entry", {"producer": f"frame{index}", "in": "00:00:00.000", "out": "00:00:07.960"})
    end_ms = len(selected) * 8000 - 40
    timeline_out = f"{end_ms//3600000:02d}:{end_ms//60000%60:02d}:{end_ms//1000%60:02d}.{end_ms%1000:03d}"
    tractor = ET.SubElement(playlist, "tractor", {"id": "tractor", "shotcut": "1", "in": "00:00:00.000", "out": timeline_out})
    ET.SubElement(tractor, "property", {"name": "shotcut:name"}).text = "Timeline"
    ET.SubElement(tractor, "track", {"producer": "V1"})
    ET.ElementTree(playlist).write(output / "rough_cut.mlt", encoding="utf-8", xml_declaration=True)
    write(output / "rough_cut.json", {"method": "deterministic_channel_sample", "audience": "audience_full",
          "notice": "Static evidence cards, no historical gameplay footage or voice. Not director-authored editing.", "cuts": cuts})
    def srt_time(seconds):
        return f"{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d},000"
    (output / "rough_cut.srt").write_text("\n".join(
        f'{index}\n{srt_time(row["editorial_in"])} --> {srt_time(row["editorial_out"])}\n'
        f'[{row["evidence_id"]} · {row["visibility"]}]\n{row["text_excerpt"]}\n'
        for index, row in enumerate(cuts, 1)), encoding="utf-8")
    selected_ids = {row["evidence_id"] for row in cuts}
    (output / "voice_queue_selected.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
            for row in queue if row["event_id"] in selected_ids), encoding="utf-8")
    concat = "".join(f"file 'frames/{i:02d}.png'\nduration 8\n" for i in range(1, len(selected) + 1))
    concat += f"file 'frames/{len(selected):02d}.png'\n"
    (output / "frames.ffconcat").write_text(concat, encoding="utf-8")
    if ffmpeg:
        result = subprocess.run([str(ffmpeg), "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", "frames.ffconcat",
                                 "-t", str(len(selected) * 8), "-r", "25", "-c:v", "h264_mf", "-b:v", "4M",
                                 "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", "rough_cut.mp4"],
                                cwd=output, capture_output=True, text=True, encoding="utf-8", errors="replace")
        (output / "render.log").write_text(result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError("Render failed; inspect render.log")
    links = ''.join(f'<li><a href="episodes/turn_{row["index"]:03d}/index.html">第 {row["index"]} 回合 · 完整证据</a></li>' for row in entries)
    (output / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>IRON 对局与剪辑素材</title>'
        '<style>body{font:18px system-ui;background:#111a2a;color:#eee;max-width:1000px;margin:30px auto;padding:20px}a{color:#8cddee}video{width:100%}li{margin:10px}</style>'
        '<h1>IRON · 对局与剪辑素材</h1><p>观众全知素材包，含私信及密封决策。不得回流参赛者。</p>'
        f'<p>已完成 {len(entries)} / {manifest["config"]["turn_limit"]} 回合，{len(timeline)} 条事件。保留原始记录及证据映射。</p>'
        '<h2>无声布局样片</h2><p>历史事件卡复盘，使用当前角色外观。不是历史对局实录，也不是最终成片。</p>'
        '<video controls preload="metadata" src="rough_cut.mp4"></video><p><a href="rough_cut.mlt">可编辑 MLT 工程</a> · '
        '<a href="evidence_timeline.csv">全局事件表</a> · <a href="voice_queue_selected.jsonl">待服务器配音的选段</a></p>'
        '<h2>逐回合档案</h2><ul>' + links + '</ul>', encoding="utf-8")
    write(output / "source_manifest.json", {"created_at": datetime.now(timezone.utc).isoformat(), "files": files})
    return {"completed_turns": len(entries), "events": len(timeline), "voice_tasks": len(queue),
            "rough_cut_seconds": len(selected) * 8, "source_files": len(files), "output": str(output)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/production.example.json")
    parser.add_argument("--ffmpeg", type=Path)
    args = parser.parse_args()
    print(json.dumps(bundle(args.campaign, args.output, read(args.config), ffmpeg=args.ffmpeg), ensure_ascii=False))
