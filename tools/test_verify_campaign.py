"""Synthetic campaign audits; no live input, HTTP or API credentials."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent_contract import country_fingerprint
from campaign import config_digest
from episode import export_episode
from model_trial import decide, digest
from showrunner import settings
from test_showrunner import observed, intent, CONFIG, TAGS
from verify_campaign import verify_campaign


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def receipt(tags, date):
    return {"date": date, "receipts": [{"country": tag, "player": tag, "date": date} for tag in tags]}


def fixture(root, retry=False):
    campaign = root / "runs/campaigns/case"
    trial, turn = root / "runs/case_t001", root / "runs/turns/case_t001_exec"
    base_date, final_date = "1936.1.1.0", "1936.1.2.0"
    baseline = {"date": base_date, "states": {}, "countries": {tag: {"focus": {}, "focus_tree": "test", "research_slot": 1,
        "politics": {"political_power": 100, "ruling_party": "neutrality"}, "technology": {"slots": {}, "technologies": {}},
        "production": {"military_lines": [], "general_lines": {}}, "units": {"division": []}} for tag in TAGS}}
    source_path = trial / "capture/iron_case_t001_before.json"
    put(source_path, baseline)
    source = {"kind": "parsed_text_save", "path": str(source_path), "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest()}
    for tag in TAGS:
        value = observed(tag)
        value.update(match_id="case", date=base_date, source=source, state_fingerprint=country_fingerprint(baseline, tag), military_production=[])
        value.pop("observation_id")
        value["observation_id"] = digest(value)
        put(trial / "observations" / (tag + ".json"), value)
    show = settings({"enabled": True, "step_hours": 24, "action_limit": 1, "referee_model": CONFIG,
                     "models": {"ITA": {**CONFIG, "model": "italian-model", "base_url": "https://other.invalid/v1"}}}, TAGS)
    config = {"countries": TAGS, "step_hours": 24, "turn_limit": 1, "communication_rounds": 1,
              "action_limit": 1, "show": show, "model": CONFIG, "commentary_model": CONFIG,
              "summarize_lessons": False, "model_commentary": False}
    epoch = datetime(2026, 11, 8, tzinfo=timezone.utc)
    lock, counter = threading.Lock(), []

    def api(model, messages):
        payload = json.loads(messages[1]["content"])
        with lock:
            counter.append(True)
            number = len(counter)
        if "reviews" in payload.get("rules", ""):
            value = {"reviews": [{"country": item["country"], "request_sha256": item["request_sha256"],
                                  "approved": True, "reason": "接口正确", "concerns": []} for item in payload["countries"]]}
        else:
            identity = payload["own_observation"]["identity"]
            tag, task = identity["country"], payload["task"]
            if "看到本轮其他国家发言前" in task:
                value = intent(tag)
            elif "可主动私下联系" in task:
                value = {"country": tag, "to": None, "message": "", "commitments": []}
            elif "全部外交已结束" in task:
                actions = [{"id": "1-GER-1", "type": "research.start", "args": {"slot": 1, "technology_id": "radio"}}] if retry and tag == "GER" else []
                value = {**identity, "actions": actions, "reason": "保持计划，公开消息可以引用其他国家。"}
            else:
                value = {"country": tag, "message": "保持沟通。", "commitments": []}
        return value, {"requested_model": model["model"], "returned_model": model["model"], "response_id": str(number),
                       "started_at": (epoch + timedelta(seconds=number + 20)).isoformat(),
                       "ended_at": (epoch + timedelta(seconds=number + 20.5)).isoformat(),
                       "finish_reason": "stop", "usage": {"total_tokens": 1}}

    with patch("model_trial.complete", side_effect=api):
        decide(trial, CONFIG, TAGS, communication_rounds=1, private_diplomacy=True, show_config=show)
    for path in (trial / "model_calls").glob("*.json"):
        call = json.loads(path.read_text(encoding="utf-8"))
        call["recorded_at"] = call["metadata"]["ended_at"]
        put(path, call)
    requests = {tag: json.loads((trial / "requests" / (tag + ".json")).read_text(encoding="utf-8")) for tag in TAGS}
    final = copy.deepcopy(baseline)
    final["date"] = final_date
    actions = []
    if retry:
        action = requests["GER"]["actions"][0]
        unchanged = copy.deepcopy(baseline)
        changed = copy.deepcopy(baseline)
        changed["countries"]["GER"]["technology"]["slots"] = {"radio": {"progress": 0}}
        final["countries"]["GER"]["technology"]["slots"] = {"radio": {"progress": .1}}
        for index, state, status in ((1, unchanged, "not_applied"), (2, changed, "applied")):
            name = "case_action_" + str(index)
            put(turn / (name + ".json"), state)
            actions.append({"country": "GER", "action": action, "status": status,
                            "started_at": (epoch + timedelta(seconds=100 + index)).isoformat(),
                            "actor_receipt": receipt(["GER"], base_date),
                            "save": {"name": "hoi4live_" + name, "parsed_date": base_date},
                            "verification": {"verified": status == "applied", "checks": {"same_date": True, "peers_unchanged": True, "target_active": status == "applied"}}})
    put(turn / "case_before.json", baseline)
    put(turn / "case_final.json", final)
    result = {"status": "completed_deterministic_turn", "countries": TAGS, "match_id": "case", "turn_id": 1,
              "requests": requests, "actions": actions, "initial_switch": receipt(TAGS, base_date),
              "before_save": {"name": "hoi4live_case_before", "parsed_date": base_date},
              "before_clock": receipt(TAGS, base_date), "after_clock": receipt(TAGS, final_date),
              "clock_steps": [{"hours": 24, "advanced_hours": 24, "receipt": receipt(["GER"], final_date)}],
              "elapsed_game_hours": 24, "final_save": {"name": "hoi4live_case_final", "parsed_date": final_date}}
    image = root / "fixture.png"
    image.write_bytes(b"synthetic image for asset-hash testing")
    result["before_map"] = str(image)
    put(turn / "result.json", result)
    marker = {"turn": 1, "phase": "execute.start", "recorded_at": (epoch + timedelta(seconds=100)).isoformat(),
              "epoch": (epoch + timedelta(seconds=100)).timestamp()}
    campaign.mkdir(parents=True, exist_ok=True)
    (campaign / "production.jsonl").write_text(json.dumps(marker) + "\n", encoding="utf-8")
    exported = export_episode(trial, turn, campaign / "episodes/turn_001", production_markers=[marker])
    manifest = {"name": "case", "status": "completed", "config": config, "config_sha256": config_digest(config),
                "turns": [{"index": 1, "trial": "runs/case_t001", "turn": "runs/turns/case_t001_exec", "status": "completed", "export": exported}]}
    put(campaign / "manifest.json", manifest)
    return campaign, trial, turn


class VerifyCampaignTests(unittest.TestCase):
    def test_completed_zero_action_round_is_valid(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            fixture(root)
            report = verify_campaign("case", root)
            self.assertEqual(report["status"], "passed", [row for row in report["checks"] if row["status"] != "passed"])
            self.assertTrue((root / "runs/campaigns/case/verification.json").exists())

    def test_not_applied_retry_is_not_counted_as_two_successes(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            _, _, turn = fixture(root, retry=True)
            report = verify_campaign("case", root)
            self.assertEqual(report["status"], "passed", [row for row in report["checks"] if row["status"] != "passed"])
            result = json.loads((turn / "result.json").read_text(encoding="utf-8"))
            result["actions"].append(copy.deepcopy(result["actions"][-1]))
            put(turn / "result.json", result)
            report = verify_campaign("case", root)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any(row["status"] == "failed" and row["check"].endswith("all_original_actions_applied_once") for row in report["checks"]))

    def test_legacy_missing_api_time_is_unknown_not_invented(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            _, trial, _ = fixture(root)
            call_path = trial / "model_calls/intent_GER.json"
            call = json.loads(call_path.read_text(encoding="utf-8"))
            call["metadata"].pop("started_at")
            call["metadata"].pop("ended_at")
            put(call_path, call)
            report = verify_campaign("case", root)
            self.assertEqual(report["status"], "incomplete")
            self.assertGreater(report["counts"].get("unknown", 0), 0)

    def test_public_same_round_leak_and_foreign_persistent_memory_are_detected(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            campaign, trial, _ = fixture(root)
            file = trial / "model_calls/diplomacy_GER.json"
            call = json.loads(file.read_text(encoding="utf-8"))
            payload = json.loads(call["messages"][1]["content"])
            payload["public_diplomacy"] = [{"country": "ITA", "message": "same-round leak", "round": 1}]
            call["messages"][1]["content"] = json.dumps(payload)
            put(file, call)
            put(campaign / "knowledge/GER.json", [{"country": "ITA", "lessons": []}])
            report = verify_campaign("case", root)
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any(row["status"] == "failed" and "synchronous:diplomacy_GER" in row["check"] for row in report["checks"]))
            self.assertTrue(any(row["status"] == "failed" and row["check"] == "knowledge:GER" for row in report["checks"]))

    def test_asset_corruption_and_unfinished_campaign_do_not_pass(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            campaign, _, _ = fixture(root)
            asset = next((campaign / "episodes/turn_001/assets").glob("*"))
            asset.write_bytes(b"corrupted")
            report = verify_campaign("case", root)
            self.assertEqual(report["status"], "failed")
            manifest = json.loads((campaign / "manifest.json").read_text(encoding="utf-8"))
            manifest["status"] = "running"
            manifest["turns"] = []
            put(campaign / "manifest.json", manifest)
            report = verify_campaign("case", root)
            self.assertEqual(report["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
