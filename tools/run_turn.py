"""Offline recovery and verification of recorded UI turns."""
import json

from m0_probe import ROOT
from agent_contract import country_fingerprint, verify_action


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def saved_state(directory, save):
    return read(directory / (save["name"].removeprefix("hoi4live_") + ".json"))


def check_diplomacy_baseline(state, observed, checkpoint=None):
    from diplomacy_actions import diplomacy_fingerprint
    hashes = [value.get("diplomacy", {}).get("baseline_sha256") for value in observed.values()]
    if not any(hashes):
        return None  # Legacy observations did not expose declaration actions.
    if any(value is None for value in hashes) or len(set(hashes)) != 1:
        raise ValueError("Country observations disagree on the diplomatic baseline")
    expected = diplomacy_fingerprint(checkpoint) if checkpoint is not None else hashes[0]
    actual = diplomacy_fingerprint(state)
    if actual != expected:
        raise ValueError("War, goal or faction state changed after the frozen observation/checkpoint")
    return {"expected": expected, "actual": actual, "verified": True}


def check_naval_air_baseline(state, observed, checkpoint=None):
    from naval_air_actions import naval_air_fingerprint
    hashes = {tag: value.get("naval_air", {}).get("state_fingerprint") for tag, value in observed.items()}
    if not any(hashes.values()):
        return None
    if any(value is None for value in hashes.values()):
        raise ValueError("An observation lacks its naval/air baseline")
    expected = {tag: naval_air_fingerprint(checkpoint, tag) for tag in observed} if checkpoint is not None else hashes
    actual = {tag: naval_air_fingerprint(state, tag) for tag in observed}
    if actual != expected:
        raise ValueError("Naval/air state changed after the frozen observation/checkpoint")
    return {"expected": expected, "actual": actual, "verified": True}


def check_army_activity_baseline(state, observed, checkpoint=None):
    from army_actions import army_activity
    if not any("army_activity" in value for value in observed.values()):
        return None
    if any("army_activity" not in value for value in observed.values()):
        raise ValueError("An observation lacks its army activity baseline")
    expected = ({tag: army_activity(checkpoint["countries"][tag]) for tag in observed} if checkpoint is not None
                else {tag: value["army_activity"] for tag, value in observed.items()})
    actual = {tag: army_activity(state["countries"][tag]) for tag in observed}
    if actual != expected:
        raise ValueError("Army activity changed after the frozen observation/checkpoint")
    return {"expected": expected, "actual": actual, "verified": True}


def gameplay_unchanged(before, after, countries):
    from diplomacy_actions import diplomacy_fingerprint
    from naval_air_actions import naval_air_fingerprint
    from army_actions import army_activity
    return (before["date"] == after["date"]
            and all(country_fingerprint(before, tag) == country_fingerprint(after, tag)
                    and naval_air_fingerprint(before, tag) == naval_air_fingerprint(after, tag)
                    and army_activity(before["countries"][tag]) == army_activity(after["countries"][tag])
                    for tag in countries)
            and diplomacy_fingerprint(before) == diplomacy_fingerprint(after))


def research_tile_refused(entry):
    return (entry["action"]["type"] == "research.start"
            and entry.get("ui_error", "").startswith("Technology detail not confirmed:"))


def resume_checkpoint(result, directory):
    """Resolve saved action outcomes before resuming a paused execution phase."""
    if result["status"] not in ("needs_inspection", "interrupted") or "before_clock" in result:
        raise ValueError("Only interrupted pre-clock execution can resume")
    if "before_save" not in result:
        first_capture = next((attempt for attempt in result.get("resume_attempts", []) if attempt.get("save")), None)
        if first_capture and first_capture.get("number") == 1 and not first_capture.get("skipped_applied"):
            result.update(before_save=first_capture["save"], initial_switch=first_capture["actor_receipts"])
    if "before_save" not in result:
        if result.get("actions"):
            raise ValueError("Actions exist without an initial checkpoint")
        return None, []
    previous = saved_state(directory, result["before_save"])
    resolutions = []
    for entry in result["actions"]:
        if "save" not in entry:
            raise ValueError("An action lacks an after-save; inspect its live outcome before recovery")
        after = saved_state(directory, entry["save"])
        if entry["status"] == "unknown":
            verified = verify_action(entry["action"], previous, after, entry["country"])
            if verified["verified"]:
                entry["status"] = "applied"
                entry["verification"] = verified
                entry["effect_status"] = verified.get("effect_status", "full")
            elif gameplay_unchanged(previous, after, result["countries"]):
                entry["status"] = "not_applied"
                entry["effect_status"] = "none"
            else:
                raise ValueError("Unknown action changed gameplay without the expected effect; manual inspection required")
            resolutions.append({"country": entry["country"], "id": entry["action"]["id"], "resolved_status": entry["status"], "evidence": entry["save"]["path"]})
        elif entry["status"] not in ("applied", "not_applied"):
            raise ValueError("Unsupported action journal state")
        previous = after
    return previous, resolutions


if __name__ == "__main__":
    raise SystemExit("旧桌面对局入口已移除。请使用 tools/native_campaign.py。")
