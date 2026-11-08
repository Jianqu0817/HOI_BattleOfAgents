"""Own-country lesson visibility, bounded claims and resumable per-country calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lessons import country_evidence, generate_lessons, validate_lessons


class LessonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trial, self.turn = self.root / "trial", self.root / "turn"
        self.tags = ["GER", "ITA", "JAP"]
        self.requests = {}
        for tag in self.tags:
            identity = {"country": tag, "turn_id": 1, "match_id": "test", "date": "1936.1.1.12", "observation_id": tag}
            request = {**identity, "reason": tag + "_PRIVATE_PLAN", "actions": []}
            self.requests[tag] = request
            self.put(self.trial / "private_decisions" / (tag + ".json"), request)
            self.put(self.trial / "observations" / (tag + ".json"), {**identity, "political_power": 10, "divisions": [], "owned_states": [], "research": {}})
            self.put(self.trial / "private_intents" / (tag + ".json"), {"country": tag, "goal": tag + "_SECRET_INTENT", "plan": "计划", "concealment": "底线", "predictions": []})
        self.put(self.trial / "public_diplomacy.json", [{"country": "JAP", "message": "PUBLIC_JAPANESE_MESSAGE"}])
        self.put(self.trial / "audience_commentary/commentary.json", {"text": "AUDIENCE_ONLY_SECRET"})
        shared = {"country": "ITA", "to": "GER", "message": "ITA_GER_SHARED"}
        other = {"country": "ITA", "to": "JAP", "message": "ITA_JAP_SECRET"}
        self.put(self.trial / "private_diplomacy/GER.json", {"sent": [], "received": [shared]})
        self.put(self.trial / "private_diplomacy/ITA.json", {"sent": [shared, other], "received": []})
        self.put(self.trial / "private_diplomacy/JAP.json", {"sent": [], "received": [other]})
        self.put(self.turn / "result.json", {"match_id": "test", "turn_id": 1, "countries": self.tags,
                 "status": "completed_deterministic_turn", "requests": self.requests, "actions": [],
                 "before_save": {"parsed_date": "1936.1.1.12"}, "elapsed_game_hours": 360,
                 "final_save": {"name": "hoi4live_final", "parsed_date": "1936.1.16.12"}})
        state = {"date": "1936.1.16.12", "states": {}, "countries": {tag: {"politics": {"political_power": 20}, "focus": {}, "technology": {}, "units": {}} for tag in self.tags}}
        self.put(self.turn / "final.json", state)
        self.config = {"base_url": "https://example.invalid/v1", "model": "fixture", "temperature": .3, "max_tokens": 1800, "enable_thinking": False}

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def response(self, context):
        return {"country": context["country"], "turn_id": context["turn_id"], "lessons": [{"claim": "仅凭观察未提供的数据，不能认定资源充足。", "evidence_ids": [context["evidence"][0]["id"]], "when_to_apply": "下次观察仍缺少容量字段时", "confidence": .7, "category": "observation"}]}

    def test_only_own_secrets_public_messages_and_addressed_mail_are_visible(self):
        payload = json.dumps(country_evidence(self.trial, self.turn, "GER"))
        for text in ("GER_PRIVATE_PLAN", "GER_SECRET_INTENT", "ITA_GER_SHARED", "PUBLIC_JAPANESE_MESSAGE"):
            self.assertIn(text, payload)
        for text in ("ITA_PRIVATE_PLAN", "JAP_PRIVATE_PLAN", "ITA_SECRET_INTENT", "JAP_SECRET_INTENT", "ITA_JAP_SECRET", "AUDIENCE_ONLY_SECRET"):
            self.assertNotIn(text, payload)

    def test_execution_debug_error_does_not_leak_enemy_tooltip_data(self):
        path = self.turn / "result.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        result["actions"] = [{"country": "GER", "action": {"type": "army.move", "args": {}, "id": "test"},
                              "status": "unknown", "ui_error": "Tooltip failed: ENEMY_SECRET_INTERNAL_METRICS"}]
        self.put(path, result)
        value = json.dumps(country_evidence(self.trial, self.turn, "GER"))
        self.assertIn("execution_adapter_error", value)
        self.assertNotIn("ENEMY_SECRET", value)

    def test_tainted_inbox_and_changed_request_are_rejected(self):
        self.put(self.trial / "private_diplomacy/GER.json", {"sent": [], "received": [{"country": "ITA", "to": "JAP", "message": "OTHER_SECRET"}]})
        with self.assertRaises(ValueError):
            country_evidence(self.trial, self.turn, "GER")
        self.put(self.trial / "private_diplomacy/GER.json", {"sent": [], "received": []})
        altered = {**self.requests["GER"], "reason": "改变提交理由"}
        self.put(self.trial / "private_decisions/GER.json", altered)
        with self.assertRaises(ValueError):
            country_evidence(self.trial, self.turn, "GER")

    def test_lessons_require_valid_own_evidence_application_condition_and_confidence(self):
        context = country_evidence(self.trial, self.turn, "GER")
        value = self.response(context)
        validate_lessons(value, context)
        for key, wrong in (("evidence_ids", ["T1-JAP-outcome-1"]), ("when_to_apply", ""), ("confidence", True), ("category", "omniscient")):
            bad = copy.deepcopy(value)
            bad["lessons"][0][key] = wrong
            with self.assertRaises(ValueError):
                validate_lessons(bad, context)

    def test_successful_countries_are_cached_and_only_failed_country_retries(self):
        counts = {tag: 0 for tag in self.tags}
        fail_japan = True

        def fake(config, messages):
            context = json.loads(messages[1]["content"])
            tag = context["country"]
            counts[tag] += 1
            if tag == "JAP" and fail_japan:
                raise RuntimeError("fixture failure")
            return self.response(context), {"requested_model": config["model"]}

        with patch("lessons.complete", side_effect=fake):
            with self.assertRaises(RuntimeError):
                generate_lessons(self.trial, self.turn, self.config)
            self.assertTrue((self.trial / "private_lessons/GER.json").exists())
            fail_japan = False
            values = generate_lessons(self.trial, self.turn, self.config)
        self.assertEqual(counts, {"GER": 1, "ITA": 1, "JAP": 2})
        self.assertEqual(set(values), set(self.tags))
        self.assertFalse(list((self.trial / "private_lessons").rglob("*.tmp")))
        self.assertFalse((self.trial / "private_lessons/failures.json").exists())

    def test_changed_provider_is_not_allowed_to_reuse_old_model_response(self):
        def fake(config, messages):
            return self.response(json.loads(messages[1]["content"])), {"requested_model": config["model"]}

        with patch("lessons.complete", side_effect=fake) as call:
            generate_lessons(self.trial, self.turn, self.config)
            with self.assertRaises(RuntimeError):
                generate_lessons(self.trial, self.turn, {**self.config, "base_url": "https://different.invalid/v1"})
            self.assertEqual(call.call_count, 3)


if __name__ == "__main__":
    unittest.main()
