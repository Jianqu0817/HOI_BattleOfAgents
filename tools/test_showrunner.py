"""Exercise recorded diplomacy without network requests or live game input."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from model_trial import (call_country, country_messages, decide, private_context,
                         recorded_complete, validate_trial_request, recover_observation_baseline,
                         check_observation_checkpoint, check_partial_observation, digest)
from agent_contract import country_fingerprint
from showrunner import (check_commitments, check_history, check_intent, check_message,
                        check_replies, extend_prompt, ledger, settings, visible_promises)


TAGS = ["GER", "ITA", "ENG"]
CONFIG = {"base_url": "https://example.invalid/v1", "model": "default-model", "api_key_env": "TEST_IRON_KEY",
          "enable_thinking": False, "temperature": .3, "max_tokens": 1800, "timeout_seconds": 90}


def observed(tag):
    return {"match_id": "recording", "turn_id": 1, "country": tag, "date": "1936.4.30.11", "observation_id": tag + "-snapshot",
            "political_power": 100, "government": "neutrality", "focus": {},
            "research": {"slot_count": 1, "slots": {}, "visible_slots": [{"index": 1, "state": "idle"}]},
            "military_production": [{"id": 1, "display_line": 1, "equipment_name": "Rifle", "requested_factories": 9}],
            "construction": [], "divisions": [], "owned_states": [{"id": 64}], "unavailable": {},
            "candidates": {"research": [{"id": "radio"}], "focus": [], "construction_buildings": ["industrial_complex"]}}


def intent(tag):
    return {"country": tag, "goal": "SECRET_GOAL_" + tag, "plan": "SECRET_PLAN_" + tag,
            "concealment": "SECRET_CONCEALMENT_" + tag,
            "predictions": [{"subject": "next turn", "prediction": "continue production", "confidence": .6}]}


class ShowrunnerTests(unittest.TestCase):
    def test_quiet_interlude_reuses_second_public_round_and_only_public_material(self):
        self.show["quiet_interludes"] = True
        o = observed("ITA")
        public = [{"country": "GER", "message": "公开话题"}]
        first = extend_prompt(country_messages(o, "diplomacy", []), o, "diplomacy", self.show, TAGS)
        second = extend_prompt(country_messages(o, "diplomacy", public, public_round=2), o, "diplomacy", self.show, TAGS)
        self.assertNotIn("闺蜜式", json.loads(first[1]["content"])["task"])
        task = json.loads(second[1]["content"])["task"]
        self.assertIn("闺蜜式", task)
        self.assertIn("不自动公开私信或密封命令", task)
        o["public_board"] = [{"kind": "war_started"}]
        urgent = extend_prompt(country_messages(o, "diplomacy", public), o, "diplomacy", self.show, TAGS)
        self.assertNotIn("闺蜜式", json.loads(urgent[1]["content"])["task"])

    def test_commitment_note_is_preserved_without_becoming_an_action(self):
        message = {"country": "GER", "to": "ENG", "message": "保持边境稳定。", "commitments": [],
                   "commitments_note": "外交表态不会自动执行游戏操作"}
        original = copy.deepcopy(message)
        self.assertEqual(check_message(message, "GER", TAGS, 1, private=True), original)
        self.assertNotIn("actions", message)
        for extra in ({"run_command": "raw"}, {"commitments_note": {"action": "raw"}}):
            with self.assertRaises(ValueError):
                check_message({**message, **extra}, "GER", TAGS, 1, private=True)

    def setUp(self):
        self.show = settings({"step_hours": 24, "action_limit": 2,
                              "common_rules": "共同规则", "guide": "共同操作说明",
                              "profiles": {tag: {"name": tag, "persona": "PERSONA_" + tag, "experience": "EXPERIENCE_" + tag} for tag in TAGS},
                              "models": {"ENG": {"model": "english-player-model"}},
                              "referee_model": {"model": "referee-model"}}, TAGS)

    def test_only_own_intent_profile_and_knowledge_are_injected(self):
        o = observed("ITA")
        message = extend_prompt(country_messages(o, "decision", []), o, "decision", self.show, TAGS, intent("ITA"))
        content = message[1]["content"]
        self.assertIn("SECRET_PLAN_ITA", content)
        self.assertIn("EXPERIENCE_ITA", content)
        self.assertNotIn("SECRET_PLAN_GER", content)
        self.assertNotIn("EXPERIENCE_GER", content)
        self.assertNotIn("PERSONA_ENG", content)
        self.assertEqual(json.loads(content)["turn_hours"], 24)
        self.assertIn("最多2个动作", content)
        with self.assertRaises(ValueError):
            extend_prompt(country_messages(o, "decision", []), o, "decision", self.show, TAGS, intent("GER"))

    def test_reply_cannot_contact_a_stranger_or_duplicate_a_sender(self):
        inbox = [{"country": "GER", "to": "ITA", "message": "hello"}]
        good = {"country": "ITA", "replies": [{"to": "GER", "message": "reply"}]}
        check_replies(good, "ITA", inbox, TAGS, 1)
        for replies in ([{"to": "ENG", "message": "secret"}], good["replies"] * 2):
            with self.assertRaises(ValueError):
                check_replies({"country": "ITA", "replies": replies}, "ITA", inbox, TAGS, 1)

    def test_empty_reply_envelope_commitments_are_preserved(self):
        inbox = [{"country": "GER", "to": "ITA", "message": "hello"}]
        value = {"country": "ITA", "replies": [{"to": "GER", "message": "reply", "commitments": []}],
                 "commitments": []}
        original = copy.deepcopy(value)
        self.assertEqual(check_replies(value, "ITA", inbox, TAGS, 1), original)
        for extra in ({"commitments": [{}]}, {"commitments": None}, {"actions": []}):
            with self.assertRaises(ValueError):
                check_replies({**value, **extra}, "ITA", inbox, TAGS, 1)

    def test_commitment_requires_a_literal_quote_and_visible_recipient(self):
        promise = {"text": "我会维持生产。", "to": "ITA", "due_turn": 2, "condition": "局势没有变化"}
        message = {"country": "GER", "to": "ITA", "message": "我会维持生产。", "commitments": [promise]}
        check_message(message, "GER", TAGS, 1, private=True)
        for change in ({"text": "不存在的原话"}, {"to": "ENG"}, {"due_turn": True}, {"due_turn": 20}):
            broken = {**message, "commitments": [{**promise, **change}]}
            with self.assertRaises(ValueError):
                check_commitments(broken, TAGS, 1, recipient="ITA", private=True)
        entries = ledger([], [message], 1)["entries"]
        self.assertEqual(entries[0]["status"], "unverified")
        self.assertEqual(entries[0]["source"], {"file": "private_messages.json", "json_pointer": "/0/commitments/0"})
        self.assertEqual(visible_promises(entries, "ENG"), [])
        self.assertEqual(len(visible_promises(entries, "ITA")), 1)

    def test_tainted_private_history_is_rejected(self):
        base = {"private_decision": {"country": "ITA"}}
        for extra in ({"private_intent": intent("GER")},
                      {"private_diplomacy": {"sent": [{"country": "GER", "to": "ENG"}]}},
                      {"private_diplomacy": {"received": [{"country": "GER", "to": "ENG"}]}},
                      {"private_replies": {"country": "GER", "replies": []}},
                      {"private_lessons": {"country": "GER", "lessons": []}},
                      {"referee_feedback": [{"country": "GER"}]}):
            with self.assertRaises(ValueError):
                check_history([{**base, **extra}], "ITA")

    def test_observation_recovery_uses_exact_original_save_and_freezes_completed_records(self):
        baseline = {"date": "1936.4.30.11", "states": {}, "countries": {tag: {"focus": {}, "production": {}, "units": {}} for tag in TAGS}}
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path) / "trial"
            capture = directory / "capture"
            capture.mkdir(parents=True)
            raw = Path(path) / "user/save games/hoi4live_iron_trial_before.hoi4"
            raw.parent.mkdir(parents=True)
            raw.write_text("exact original fixture", encoding="utf-8")
            newer = raw.parent / "hoi4live_ignored_newest.hoi4"
            newer.write_text("must not parse this", encoding="utf-8")
            record = {"tool": "hoi4_save", "args": {"name": "iron_trial_before"}, "error": False,
                      "result": {"name": raw.stem, "path": str(raw), "date": baseline["date"]}}
            (capture / "calls.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            with patch("parse_save.load_save", return_value=baseline) as parse:
                save, parsed, state = recover_observation_baseline(directory, TAGS, raw.parent.parent)
                parse.assert_called_once_with(str(raw), set(TAGS))
            self.assertEqual(state, baseline)
            self.assertEqual(save["path"], str(raw))
            self.assertEqual(parsed.name, "iron_trial_before.json")
            with patch("parse_save.load_save", side_effect=AssertionError("already parsed")):
                recover_observation_baseline(directory, TAGS, raw.parent.parent)
            check_observation_checkpoint(baseline, copy.deepcopy(baseline), TAGS)
            changed = copy.deepcopy(baseline)
            changed["countries"]["ITA"]["production"] = {"changed": True}
            with self.assertRaisesRegex(ValueError, "Game changed"):
                check_observation_checkpoint(baseline, changed, TAGS)
            source = {"sha256": "frozen-source"}
            o = observed("GER")
            o.update(match_id="trial", source=source, state_fingerprint=country_fingerprint(baseline, "GER"))
            o.pop("observation_id")
            o["observation_id"] = digest(o)
            snapshot = json.dumps(o)
            check_partial_observation(o, baseline, "GER", source, "trial", 1)
            self.assertEqual(json.dumps(o), snapshot)
            with self.assertRaises(ValueError):
                check_partial_observation({**o, "political_power": 999}, baseline, "GER", source, "trial", 1)

    def test_action_limit_is_configurable_and_keeps_target_checks(self):
        o = observed("GER")
        request = {key: o[key] for key in ("match_id", "turn_id", "country", "date", "observation_id")}
        request["actions"] = [{"id": "research", "type": "research.start", "args": {"slot": 1, "technology_id": "radio"}},
                              {"id": "production", "type": "production.assign", "args": {"line_id": 1, "factories": 10}}]
        self.assertEqual(len(validate_trial_request(request, o, action_limit=2)), 2)
        with self.assertRaises(ValueError):
            validate_trial_request(request, o)
        for limit in (0, 7, True):
            with self.assertRaises(ValueError):
                settings({"action_limit": limit}, TAGS)

    def test_cache_reuses_response_and_rejects_changed_provider(self):
        messages = [{"role": "user", "content": "same"}]
        metadata = {"requested_model": CONFIG["model"], "returned_model": CONFIG["model"], "response_id": "one"}
        with tempfile.TemporaryDirectory() as path, patch("model_trial.complete", return_value=({"answer": 1}, metadata)) as mocked:
            directory = Path(path)
            first = recorded_complete(directory, "sample", CONFIG, messages, require_settings=True)
            second = recorded_complete(directory, "sample", CONFIG, messages, require_settings=True)
            self.assertEqual(first, second)
            self.assertEqual(mocked.call_count, 1)
            with self.assertRaises(ValueError):
                recorded_complete(directory, "sample", {**CONFIG, "base_url": "https://other.invalid/v1"}, messages, require_settings=True)
            with self.assertRaises(ValueError):
                recorded_complete(directory, "sample", {**CONFIG, "temperature": .9}, messages, require_settings=True)

    def test_full_show_routes_models_replies_and_resumes_without_resampling(self):
        recorded, lock = [], threading.Lock()
        referee_attempts = []

        def fake_complete(config, messages):
            payload = json.loads(messages[1]["content"])
            is_judge = "reviews" in payload.get("rules", "")
            with lock:
                recorded.append((config["model"], copy.deepcopy(payload)))
                response_id = str(len(recorded))
            if is_judge:
                referee_attempts.append(True)
                if len(referee_attempts) == 1:
                    raise RuntimeError("simulated temporary transport failure")
                value = {"reviews": [{"country": item["country"], "request_sha256": item["request_sha256"],
                                       "approved": True, "reason": "接口有效", "concerns": []} for item in payload["countries"]]}
                value['reviews'][0]['request_sha256'] = 'mistyped-echo'
            else:
                tag = payload["own_observation"]["identity"]["country"]
                target = TAGS[(TAGS.index(tag) + 1) % len(TAGS)]
                task = payload["task"]
                if "看到本轮其他国家发言前" in task:
                    self.assertEqual(payload["public_diplomacy"], [])
                    value = intent(tag)
                elif "回复本轮收到" in task:
                    value = {"country": tag, "replies": [{"to": row["country"], "message": "收到，保持沟通。", "commitments": []}
                                                         for row in payload["own_private_diplomacy"]["received"]]}
                elif "全部外交已结束" in task:
                    value = {**payload["own_observation"]["identity"], "actions": [], "reason": "保持命令"}
                elif "可主动私下联系" in task:
                    value = {"country": tag, "to": target, "message": "PRIVATE_MESSAGE_" + tag, "commitments": []}
                else:
                    value = {"country": tag, "message": "我会维持生产。", "commitments": [
                        {"text": "我会维持生产。", "to": target, "due_turn": 1, "condition": ""}]}
            return value, {"requested_model": config["model"], "returned_model": config["model"], "response_id": response_id,
                           "usage": {"total_tokens": 1}, "finish_reason": "stop"}

        with tempfile.TemporaryDirectory() as path, patch("model_trial.complete", side_effect=fake_complete):
            directory = Path(path)
            (directory / "observations").mkdir()
            for tag in TAGS:
                (directory / "observations" / (tag + ".json")).write_text(json.dumps(observed(tag)), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "transport failure"):
                decide(directory, CONFIG, TAGS, communication_rounds=2, private_diplomacy=True, show_config=self.show)
            before = {p.name: p.read_bytes() for p in (directory / "private_decisions").glob("*.json")}
            calls_before_resume = len(recorded)
            with self.assertRaisesRegex(ValueError, "Cannot change"):
                decide(directory, CONFIG, TAGS, resume=True, communication_rounds=1, private_diplomacy=True, show_config=self.show)
            # A process exit may leave a live phase name rather than an exception marker.
            result_path = directory / "decision_result.json"
            interrupted = json.loads(result_path.read_text(encoding="utf-8"))
            interrupted["status"] = "private_decisions"
            result_path.write_text(json.dumps(interrupted), encoding="utf-8")
            decide(directory, CONFIG, TAGS, resume=True, communication_rounds=2, private_diplomacy=True, show_config=self.show)
            referee_record = json.loads((directory / 'model_calls/referee.json').read_text('utf8'))
            self.assertEqual(referee_record['response']['reviews'][0]['request_sha256'], 'mistyped-echo')
            self.assertNotEqual(referee_record['normalized_response'], referee_record['response'])
            self.assertEqual(len(recorded), calls_before_resume + 1)
            self.assertEqual(before, {p.name: p.read_bytes() for p in (directory / "private_decisions").glob("*.json")})
            result = json.loads((directory / "decision_result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "approved")
            self.assertEqual(result["step_days"], 1)
            self.assertEqual(len(list((directory / "model_calls").glob("*.json"))), 19)
            for tag in TAGS:
                call = json.loads((directory / "model_calls" / ("decision_" + tag + ".json")).read_text(encoding="utf-8"))
                payload = json.loads(call["messages"][1]["content"])
                self.assertEqual(payload["own_pre_diplomacy_intent"], intent(tag))
                for other in set(TAGS) - {tag}:
                    self.assertNotIn("SECRET_PLAN_" + other, call["messages"][1]["content"])
                    self.assertNotIn("PERSONA_" + other, call["messages"][1]["content"])
                mail = payload["own_private_diplomacy"]
                self.assertTrue(all(row["country"] == tag for row in mail["sent"]))
                self.assertTrue(all(row["to"] == tag for row in mail["received"]))
                self.assertEqual(sum(row.get("kind") == "reply" for row in mail["sent"]), 1)
                self.assertEqual(sum(row.get("kind") == "reply" for row in mail["received"]), 1)
                self.assertEqual(call["metadata"]["requested_model"], "english-player-model" if tag == "ENG" else "default-model")
            judge_call = json.loads((directory / "model_calls/referee.json").read_text(encoding="utf-8"))
            self.assertEqual(judge_call["metadata"]["requested_model"], "referee-model")
            self.assertNotIn("SECRET_PLAN", json.dumps(judge_call))
            public = json.loads((directory / "commitments.json").read_text(encoding="utf-8"))
            self.assertEqual(len(public["entries"]), 6)


if __name__ == "__main__":
    unittest.main()
