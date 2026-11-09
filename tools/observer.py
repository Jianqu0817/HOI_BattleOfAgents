"""Local human observer. Reads campaign evidence; never supplies player context."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from threading import Lock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"GER": "德国", "FRA": "法国", "ITA": "意大利", "AUS": "奥地利", "SWI": "瑞士"}
CAST = {"GER": ("gpt", "白龙"), "FRA": ("claude", "克莱德"), "ITA": ("gemini", "基米"),
        "AUS": ("deepseek", "大肥鱼"), "SWI": ("qwen", "千问")}
NOTE_LOCK = Lock()


@lru_cache(maxsize=768)
def _cached_json(filename, modified, size):
    return json.loads(Path(filename).read_text(encoding="utf-8-sig"))


def read(path, default=None):
    try:
        stat = path.stat()
        return _cached_json(str(path), stat.st_mtime_ns, stat.st_size)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def campaign_dir(name, root=ROOT):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
        raise ValueError("invalid campaign name")
    return root / "runs" / "campaigns" / name


def notes(directory):
    path = directory / "observer_notes.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def public_event(event, turn):
    return {"turn": turn, "id": event.get("id"), "kind": event.get("kind"),
            "facts": event.get("facts", {}), "date": event.get("observed_date")}


def snapshot(directory, *, audience=False):
    manifest = read(directory / "manifest.json", None) or read(directory / "preparation.json", {})
    config = manifest.get("config", {})
    entries = manifest.get("turns", [])
    completed = [row for row in entries if row.get("status") == "completed"]
    current = entries[-1] if entries else {}
    index = current.get("index", 1)
    rows, events = [], []
    for entry in entries:
        number = entry["index"]
        trial = directory / "trials" / f"turn_{number:03d}"
        result = read(directory / "turns" / f"turn_{number:03d}" / "result.json", {})
        public = list(read(trial / "public_diplomacy.json", []))
        # Completed phase files are preferred; partial dialogue is projected explicitly.
        if not public:
            for round_id in (1, 2, 3):
                prefix = "diplomacy" if round_id == 1 else f"diplomacy_{round_id}"
                for tag in config.get("countries", []):
                    call = read(trial / "model_calls" / f"{prefix}_{tag}.json", {})
                    value = call.get("normalized_response", call.get("response", {}))
                    if isinstance(value, dict) and isinstance(value.get("message"), str):
                        public.append({"country": tag, "round": round_id, "message": value["message"]})
        speeches = [{key: value.get(key) for key in ("country", "round", "message")} for value in public]
        performance = read(trial / "performance.json", {})
        lines = performance.get("host") if "host" in performance else read(trial / "table_host.json", [])
        host = [{"message": row.get("message"), "refs": row.get("refs", [])} for row in lines]
        board = read(directory / "turns" / f"turn_{number:03d}" / "public_events.json", [])
        changes = [public_event(value, number) for value in board if value.get("visibility") == "public"]
        events.extend(changes)
        review = read(trial / "decision_result.json", {})
        elapsed = entry.get("elapsed_seconds")
        if entry.get("started_at") and entry.get("completed_at"):
            elapsed = round((datetime.fromisoformat(entry["completed_at"]) - datetime.fromisoformat(entry["started_at"])).total_seconds(), 3)
        row = {"index": number, "status": entry.get("status"), "date": entry.get("final_date"),
               "started_at": entry.get("started_at"), "completed_at": entry.get("completed_at"),
               "elapsed_seconds": elapsed, "model_seconds": entry.get("model_seconds"),
               "native_seconds": entry.get("native_seconds"), "action_counts": entry.get("action_counts", {}),
               "public": speeches, "host": host, "events": changes,
               "referee_status": review.get("status", "pending"),
               "game_hours": result.get("elapsed_game_hours"), "audience_ready": bool(entry.get("audience_completed_at"))}
        if audience:
            row["private"] = read(trial / "private_messages.json", [])
            row["intent"] = {tag: read(trial / "private_intents" / (tag + ".json"), {}) for tag in config.get("countries", [])}
            row["decisions"] = {tag: read(trial / "private_decisions" / (tag + ".json"), {}) for tag in config.get("countries", [])}
            row["referee"] = review.get("referee", {})
            row["actions"] = result.get("actions", [])
            row["interviews"] = performance.get("interviews", [])
            commentary = read(trial / "audience_commentary" / "commentary.json", {})
            row["commentary"] = commentary.get("content")
        rows.append(row)
    trial = directory / "trials" / f"turn_{index:03d}"
    calls = [p.stem for p in (trial / "model_calls").glob("*.json")]
    phases = {label: sum(bool(re.fullmatch(pattern, value)) for value in calls) for label, pattern in (
        ("意图", r"intent_[A-Z]{3}"), ("公开讨论1", r"diplomacy_[A-Z]{3}"),
        ("公开讨论2", r"diplomacy_2_[A-Z]{3}"), ("私信", r"private_[A-Z]{3}"),
        ("回信", r"reply_[A-Z]{3}"), ("密封决策", r"decision_[A-Z]{3}"), ("裁判", r"referee"))}
    countries = []
    for tag in config.get("countries", []):
        profile = config.get("show", {}).get("profiles", {}).get(tag, {})
        brand, fallback = CAST.get(tag, ("", tag))
        model = config.get("show", {}).get("models", {}).get(tag, config.get("model", {})).get("model")
        for switch in manifest.get("model_switches", []):
            if switch.get("first_affected_turn", 10**9) <= index and switch.get("spec", {}).get("target") != "officials" and switch.get("spec", {}).get("country") in (None, tag):
                model = switch.get("spec", {}).get("model", model)
        countries.append({"tag": tag, "country": NAMES.get(tag, tag), "name": profile.get("name", fallback),
                          "brand": brand, "model": model})
    timings = [row["elapsed_seconds"] for row in rows if row["status"] == "completed"
               and isinstance(row.get("elapsed_seconds"), (int, float))][-5:]
    return {"name": directory.name, "view": "audience" if audience else "public",
            "scenario_hours": config.get("scenario", {}).get("hours"),
            "played_hours": sum(row.get("game_hours") or 0 for row in rows if row["status"] == "completed"),
            "updated_at": datetime.now(timezone.utc).isoformat(), "started_at": manifest.get("started_at"),
            "status": manifest.get("status", "preparing"), "error": manifest.get("error"),
            "completed": len(completed), "limit": config.get("turn_limit", 50), "current": index,
            "date": completed[-1].get("final_date") if completed else manifest.get("initial_date"),
            "countries": countries, "phases": phases, "turns": rows, "events": events,
            "scoreboard": manifest.get("scoreboard", {}), "usage": manifest.get("usage", {}),
            "audience_timing": config.get("audience_timing", "inline"),
            "average_seconds": round(sum(timings) / len(timings), 1) if timings else None,
            "recording": read(directory / "recording.json", {}), "notes": notes(directory)}


def summary(data):
    lines = [f"# {data['name']} · 观察战报", "", f"进度：{data['completed']}/{data['limit']} 回合；游戏日期：{data.get('date') or '待采集'}。",
             "", "以下为公开可观察事件与人工笔记，未将秘密命令当作公开事实。", "", "## 局势变化"]
    if not data["events"]:
        lines.append("尚无已记录的公开局势变化。")
    for event in data["events"]:
        lines.append(f"- 回合 {event['turn']} · {event['kind']}：{json.dumps(event['facts'], ensure_ascii=False)}")
    lines.extend(["", "## 人工观察笔记"])
    for row in data["notes"]:
        lines.append(f"- 回合 {row['turn']} · {row['label']}：{row['text']}")
    return "\n".join(lines) + "\n"


def handler_for(directory):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, body, content_type="application/json; charset=utf-8", status=200):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlsplit(self.path)
            if route.path == "/":
                self.send((ROOT / "tools/observer.html").read_bytes(), "text/html; charset=utf-8")
            elif route.path == "/api/state":
                self.send(snapshot(directory, audience=parse_qs(route.query).get("view") == ["audience"]))
            elif route.path == "/api/summary":
                self.send(summary(snapshot(directory)), "text/markdown; charset=utf-8")
            elif route.path == "/preview.png" and (directory / "recording_preview.png").is_file():
                self.send((directory / "recording_preview.png").read_bytes(), "image/png")
            else:
                self.send({"error": "not found"}, status=404)

        def do_POST(self):
            if self.path != "/api/notes":
                self.send({"error": "not found"}, status=404)
                return
            try:
                if self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
                    raise ValueError("notes must originate from this observer")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 12000:
                    raise ValueError("invalid note size")
                value = json.loads(self.rfile.read(length))
                if type(value.get("turn")) is not int or not 0 <= value["turn"] <= 100:
                    raise ValueError("invalid turn")
                if not isinstance(value.get("text"), str) or not 0 < len(value["text"].strip()) <= 2000:
                    raise ValueError("note must contain 1–2000 characters")
                row = {"turn": value["turn"], "text": value["text"].strip(), "label": str(value.get("label", "观察"))[:40],
                       "visibility": "operator_only", "created_at": datetime.now(timezone.utc).isoformat()}
                directory.mkdir(parents=True, exist_ok=True)
                with NOTE_LOCK, (directory / "observer_notes.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                self.send({"saved": True, "note": row})
            except (ValueError, json.JSONDecodeError) as error:
                self.send({"error": str(error)}, status=400)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    directory = campaign_dir(args.name)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(directory))
    print(f"IRON observer: http://127.0.0.1:{args.port} | {directory.name}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
