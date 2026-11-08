"""Bounded 3–5-country control probe using engine log receipts and text saves."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def ai_receipt_state(lines, country):
    receipts = [line for line in lines if re.match(r"\s*(Enabled|Disabled)\s+A[Il1]\s+for", line, re.I)]
    if not receipts:
        return None
    match = re.fullmatch(r"\s*(Enabled|Disabled)\s+A[Il1]\s+for\s*:\s*" + re.escape(country) + r"\s*", receipts[-1], re.I)
    return match[1].lower() if match else None


def countries_from(config):
    tags = config["countries"]
    if not isinstance(tags, list) or not 3 <= len(tags) <= 5:
        raise ValueError("Configure 3–5 countries")
    if len(set(tags)) != len(tags) or any(not re.fullmatch(r"[A-Z][A-Z0-9]{2}", t) for t in tags):
        raise ValueError("Country tags must be unique three-character IDs")
    return tags


def order_for(tags, turn):
    offset = turn % len(tags)
    return tags[offset:] + tags[:offset]


def game_hours(value):
    # HOI4 skips leap days: its 1936-02-28 + 24 hours is 1936-03-01.
    year, month, day, hour = map(int, value.split("."))
    month_days = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if not 1 <= month <= 12 or not 1 <= day <= month_days[month - 1] or not 0 <= hour < 24:
        raise ValueError("Invalid HOI4 date")
    return ((year * 365 + sum(month_days[:month - 1]) + day - 1) * 24) + hour


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def settled_pause_state(session, timeout=3):
    """Wait for the paused icon's dark animation frame to become readable."""
    from hoi4_live.perception import topbar
    deadline = time.monotonic() + timeout
    while True:
        state, evidence = topbar.pause_state(session.grab())
        if state != "unknown" or time.monotonic() >= deadline:
            return state, evidence
        time.sleep(.25)


class Probe:
    def __init__(self, directory, tags):
        sys.path.insert(0, str(ROOT / ".deps" / "hoi4-live-tools"))
        os.environ["HOI4_LIVE_CONFIG"] = str(ROOT / "configs" / "live.local.json")
        from live_compat import install
        install()
        from hoi4_live import config, tools
        from hoi4_live.session import Session
        self.session = Session(config.get())
        self.session._iron_control_tags = tuple(tags)
        self.session.image_mode = "none"
        self.directory = directory
        self.tags = tags
        self.report_path = directory / "report.json"
        self.report = json.loads(self.report_path.read_text(encoding="utf-8")) if self.report_path.exists() else {
            "countries": tags, "disabled_ai": [], "turns": [], "status": "new"}
        if self.report["countries"] != tags:
            raise ValueError("Run country list differs from the saved report")
        self.game_log = Path(self.session.cfg.logs_dir) / "game.log"

    def record(self):
        write_json(self.report_path, self.report)

    def call(self, name, args):
        from hoi4_live.tools.dispatch import call
        if name == "hoi4_status" and self.session.window.find():
            x, y, width, height = self.session.window.client()
            _, (left, top, right, bottom) = self.session.window.monitor()
            if width <= 0 or height <= 0 or x < left or y < top or x + width > right or y + height > bottom:
                self.session.window.ensure_foreground()
                self.session.window.pin_client()
        value = call(self.session, name, args)
        entry = {"tool": name, "args": args, "result": value.get("structuredContent"), "error": value.get("isError", False)}
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if value.get("isError"):
            detail = value.get("structuredContent") or [item.get("text", "") for item in value.get("content", []) if item.get("type") == "text"]
            raise RuntimeError(json.dumps(detail, ensure_ascii=False))
        return value.get("structuredContent", {})

    def command(self, commands):
        return self.call("hoi4_console", {"commands": commands, "read_output": True, "keep_open": True})

    def receipts(self, tags, label):
        marker = "IRON_M0_" + uuid.uuid4().hex[:10]
        offset = self.game_log.stat().st_size
        commands = []
        for tag in tags:
            commands.extend([f"tag {tag}", f'eval_effect FROM = {{ log = "{marker} expected={tag} player=[This.GetTag]" }}'])
        self.command(commands)
        with self.game_log.open("rb") as stream:
            stream.seek(offset)
            text = stream.read().decode("utf-8", "replace")
        rows = []
        for tag in tags:
            match = re.search(r"\[(\d{4}\.\d{2}\.\d{2}\.\d{2})\]: " + re.escape(marker) + " expected=" + tag + r" player=([A-Z0-9]{3})", text)
            if not match or match[2] != tag:
                raise RuntimeError(f"Missing or mismatched live country receipt for {tag}")
            rows.append({"country": tag, "player": match[2], "date": match[1]})
        if len({row["date"] for row in rows}) != 1:
            raise RuntimeError("Game time advanced during country switching")
        return {"label": label, "marker": marker, "receipts": rows, "date": rows[0]["date"]}

    def save(self, name):
        result = self.call("hoi4_save", {"name": name, "overwrite": False})
        # Inspect this exact newly written file, never a guessed newest save.
        from parse_save import load_save
        state = load_save(result["path"], set(self.tags))
        if set(state.get("countries", {})) != set(self.tags):
            raise RuntimeError("A configured country is missing from the saved game")
        write_json(self.directory / (name + ".json"), state)
        return {**result, "parsed_date": state["date"], "country_count": len(state["countries"])}

    def prepare(self, known_disabled):
        if self.report["status"] != "new":
            raise RuntimeError("Prepare already started; do not replay AI toggles")
        if not set(known_disabled) <= set(self.tags):
            raise ValueError("Known disabled tags must be configured countries")
        self.report["disabled_ai"] = list(known_disabled)
        self.report["status"] = "preparing"
        self.record()
        for tag in self.tags:
            if tag in known_disabled:
                continue
            self.report["pending_ai_toggle"] = tag
            self.record()
            result = self.command([f"ai {tag}"])
            response = " ".join(result["results"][0].get("response", []))
            if ai_receipt_state(result["results"][0].get("response", []), tag) != "disabled":
                raise RuntimeError(f"AI toggle outcome uncertain for {tag}: {response}")
            self.report["disabled_ai"].append(tag)
            self.report.pop("pending_ai_toggle", None)
            self.record()
        self.report["initial_switch"] = self.receipts(self.tags, "all_countries_paused")
        self.report["baseline"] = self.save("iron_" + self.directory.name + "_before")
        self.report["status"] = "prepared"
        self.record()
        print("PREPARED", self.report["initial_switch"]["date"], ",".join(self.tags), flush=True)

    def advance(self, turns):
        from hoi4_live.game import console, timectl
        from hoi4_live.perception import topbar
        if self.report["status"] != "prepared":
            raise RuntimeError("This run is not prepared; inspect its report before proceeding")
        self.report["status"] = "advancing"
        self.record()
        console.close_console(self.session)
        timectl.set_speed(self.session, 4)
        for turn in range(turns):
            before = self.receipts(order_for(self.tags, turn), f"turn_{turn}_before")
            target = game_hours(before["date"]) + 24
            self.command(["pause_in_hours 24"])
            deadline = time.monotonic() + 60
            while True:
                state, evidence = topbar.pause_state(self.session.grab())
                if state == "paused":
                    break
                if time.monotonic() > deadline:
                    if state == "running":
                        console.close_console(self.session)
                        self.session.input.key("space")
                    raise RuntimeError(f"Timed out waiting for engine pause: {state} {evidence}")
                time.sleep(0.4)
            after = self.receipts([self.tags[0]], f"turn_{turn}_after")
            if game_hours(after["date"]) != target:
                raise RuntimeError(f"Expected +24 game hours from {before['date']}, got {after['date']}")
            self.report["turns"].append({"turn": turn, "before": before, "after": after, "elapsed_hours": 24})
            self.record()
            print(f"TURN {turn + 1}/{turns}: {after['date']} all {len(self.tags)} actors verified", flush=True)
        self.report["final"] = self.save("iron_" + self.directory.name + "_after")
        self.report["final_switch"] = self.receipts(self.tags + [self.tags[0]], "final_countries_paused")
        console.close_console(self.session)
        self.call("hoi4_screenshot", {})
        self.report["status"] = "completed_control_probe"
        self.record()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "advance"])
    parser.add_argument("--config", default="configs/match.example.json")
    parser.add_argument("--run", default="runs/m0_five")
    parser.add_argument("--known-disabled", nargs="*", default=[])
    parser.add_argument("--turns", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.turns <= 10:
        parser.error("M0 is bounded to 1–10 turns")
    tags = countries_from(json.loads((ROOT / args.config).read_text(encoding="utf-8")))
    probe = Probe(ROOT / args.run, tags)
    try:
        with probe.session.lock.hold("IRON M0 multicountry validation"):
            if args.phase == "prepare":
                probe.prepare(args.known_disabled)
            else:
                probe.advance(args.turns)
    except Exception as error:
        probe.report["error"] = str(error)
        probe.record()
        raise
    finally:
        probe.session.shutdown()


if __name__ == "__main__":
    main()
