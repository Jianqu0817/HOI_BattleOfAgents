"""Keep private state separate from public messages and verify visible action limits."""
import copy
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from model_trial import country_messages, validate_trial_request, normalize_request, validate_private, private_context, factual_notes, own_history


class ModelTrialTests(unittest.TestCase):
    def setUp(self):
        self.observed = {"match_id": "trial", "turn_id": 1, "country": "GER", "date": "1936.3.16.11", "observation_id": "own-snapshot",
                         "political_power": 210, "government": "fascism", "focus": {"current": "PRIVATE_GER_PLAN"},
                         "research": {"slot_count": 4, "visible_slots": [{"index": 1, "state": "busy"}, {"index": 3, "state": "idle"}],
                                      "screen_evidence": "LOCAL_SCREENSHOT_PATH"},
                         "military_production": [{"id": 1, "display_line": 1, "equipment_name": "Kar98k", "requested_factories": 9}],
                         "construction": [], "divisions": [], "owned_states": [{"id": 64}], "unavailable": {},
                         "candidates": {"research": [{"id": "radio"}], "focus": [], "construction_buildings": ["industrial_complex"]}}
        self.request = {key: self.observed[key] for key in ("match_id", "turn_id", "country", "date", "observation_id")}
        self.request["actions"] = [{"id": "1-GER-1", "type": "research.start", "args": {"slot": 3, "technology_id": "radio"}}]

    def test_private_context_contains_own_state_and_public_messages_only(self):
        public = [{"country": "ITA", "message": "PUBLIC_ITALIAN_CLAIM"}]
        messages = country_messages(self.observed, "decision", public)
        prompt = json.loads(messages[1]["content"])
        self.assertEqual(prompt["public_diplomacy"], public)
        self.assertEqual(prompt["own_observation"]["current_focus"]["current"], "PRIVATE_GER_PLAN")
        self.assertNotIn("LOCAL_SCREENSHOT_PATH", messages[1]["content"])
        self.assertNotIn("private_decisions", prompt)

    def test_private_message_reaches_only_sender_and_recipient(self):
        messages = [{"country": "ITA", "to": "JAP", "message": "ITA_JAP_SECRET"},
                    {"country": "SOV", "to": "GER", "message": "SOV_GER_SECRET"}]
        own = private_context("GER", messages)
        prompt = country_messages(self.observed, "decision", [], own)[1]["content"]
        self.assertIn("SOV_GER_SECRET", prompt)
        self.assertNotIn("ITA_JAP_SECRET", prompt)
        self.assertEqual(private_context("ENG", messages), {"sent": [], "received": []})

    def test_forged_sender_self_mail_and_unknown_recipient_are_rejected(self):
        tags = ["GER", "ITA", "JAP", "ENG", "SOV"]
        for message in ({"country": "ITA", "to": "JAP", "message": "forged"},
                        {"country": "GER", "to": "GER", "message": "self"},
                        {"country": "GER", "to": "USA", "message": "outside match"}):
            with self.assertRaises(ValueError):
                validate_private(message, "GER", tags)
        self.assertEqual(validate_private({"country": "GER", "to": None, "message": ""}, "GER", tags)["to"], None)

    def test_second_public_round_receives_first_round_and_requests_a_response(self):
        first = [{"country": "JAP", "message": "PUBLIC_PROPOSAL", "round": 1}]
        message = country_messages(self.observed, "diplomacy", first, public_round=2)[1]["content"]
        self.assertIn("PUBLIC_PROPOSAL", message)
        self.assertIn("第二轮公开沟通", message)
        self.assertIn("不能当成零或已满", country_messages(self.observed, "decision", [])[0]["content"])

    def test_public_quote_survives_an_unlisted_aside_reference(self):
        from model_trial import call_country
        from showrunner import settings
        tags = ["GER", "ITA"]
        show = settings({"performance": True, "strict_public_context": True}, tags)
        aside = {"about": "ITA", "stance": "追问", "ref": "unlisted", "text": "待核对"}
        raw = {"country": "GER", "message": "先合照，条件留到桌上谈。", "public_asides": [aside]}
        with tempfile.TemporaryDirectory() as temp, patch("model_trial.recorded_complete", return_value={"response": raw}):
            directory = Path(temp)
            result = call_country(directory, {}, self.observed, "diplomacy", [], tags=tags, show_config=show)
            self.assertEqual(result, {**raw, "public_asides": []})
            receipt = json.loads((directory / "model_calls/diplomacy_GER.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["response"], raw)
            self.assertEqual(receipt["aside_projection"]["rejected"][0]["aside"], aside)

    def test_unsupported_claim_is_flagged_without_rewriting_actions(self):
        request = copy.deepcopy(self.request)
        request["reason"] = "建造队列已满，当前选择的科技可立即完成。"
        before = copy.deepcopy(request)
        self.assertEqual(len(factual_notes(request, self.observed)), 2)
        self.assertEqual(request, before)
        request["reason"] = "建造容量未知，继续现有计划。"
        self.assertEqual(factual_notes(request, self.observed), [])

    def test_history_links_own_execution_and_excludes_other_referee_feedback(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            (root / "observations").mkdir()
            (root / "private_decisions").mkdir()
            decision = {**self.request, "reason": "本国计划"}
            (root / "observations/GER.json").write_text(json.dumps(self.observed), encoding="utf-8")
            (root / "private_decisions/GER.json").write_text(json.dumps(decision), encoding="utf-8")
            (root / "public_diplomacy.json").write_text("[]", encoding="utf-8")
            (root / "decision_result.json").write_text(json.dumps({"referee": {"reviews": [
                {"country": "GER", "reason": "OWN_FEEDBACK"}, {"country": "ITA", "reason": "ITALIAN_PRIVATE_FEEDBACK"}]}}), encoding="utf-8")
            turn = {"match_id": "trial", "turn_id": 1, "requests": {"GER": decision},
                    "final_save": {"parsed_date": "1936.3.31.11"}, "actions": [
                        {"country": "GER", "status": "applied", "action": decision["actions"][0]}]}
            turn["public_notices"] = [
                {"title": "PUBLIC_NEWS", "text": "PUBLIC_TEXT", "proof": {"major": "yes", "event": "news.1", "source": "LOCAL_PATH"}},
                {"title": "PRIVATE_EVENT", "text": "SECRET_TEXT", "proof": {"major": "no", "event": "country.1"}}]
            history = own_history(root, turn, "GER")
            self.assertEqual(history[-1]["private_decision"], decision)
            self.assertNotIn("ITALIAN_PRIVATE_FEEDBACK", json.dumps(history))
            self.assertEqual(len(history[-1]["public_events"]), 1)
            self.assertNotIn("SECRET_TEXT", json.dumps(history))
            self.assertNotIn("LOCAL_PATH", json.dumps(history))
            self.observed["recent_history"] = [{"private_decision": {"country": "ITA"}}]
            (root / "observations/GER.json").write_text(json.dumps(self.observed), encoding="utf-8")
            with self.assertRaises(ValueError):
                own_history(root, turn, "GER")

    def test_busy_slot_is_rejected_even_when_slot_number_is_in_range(self):
        self.assertEqual(len(validate_trial_request(self.request, self.observed)), 1)
        self.request["actions"][0]["args"]["slot"] = 1
        with self.assertRaises(ValueError):
            validate_trial_request(self.request, self.observed)

    def test_trial_rejects_second_action_before_execution(self):
        second = copy.deepcopy(self.request["actions"][0])
        second["id"] = "1-GER-2"
        second["type"] = "construction.queue"
        second["args"] = {"state_id": 64, "building_id": "industrial_complex", "count": 1}
        self.request["actions"].append(second)
        with self.assertRaises(ValueError):
            validate_trial_request(self.request, self.observed)

    def test_identity_envelope_preserves_the_original_decision(self):
        value = {"identity": {key: self.request[key] for key in ("match_id", "turn_id", "country", "date", "observation_id")},
                 "actions": self.request["actions"], "reason": "original private reason"}
        result = normalize_request(value)
        self.assertEqual(result["actions"], value["actions"])
        self.assertEqual(result["reason"], value["reason"])
        self.assertEqual(result["observation_id"], value["identity"]["observation_id"])
        self.assertEqual(len(validate_trial_request(result, self.observed)), 1)
        value["country"] = "ITA"
        with self.assertRaises(ValueError):
            normalize_request(value)
