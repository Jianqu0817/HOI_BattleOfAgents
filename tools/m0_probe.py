"""Bounded 3–5-country control probe using engine log receipts and text saves."""
import json
from pathlib import Path
import re
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


class Probe:
    """Retired backend. Historical probe scripts fail before acquiring desktop input."""

    def __init__(self, directory, tags):
        raise RuntimeError("旧桌面控制已移除。请使用 tools/native_campaign.py。")


if __name__ == "__main__":
    raise SystemExit("旧桌面控制已移除。请使用 tools/native_campaign.py。")
