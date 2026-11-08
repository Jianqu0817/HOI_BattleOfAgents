"""Privacy boundaries are checked with canaries, without model or game calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_trial import brief, call_country, country_messages
from showrunner import check_message, extend_prompt, ledger, settings
from story_channels import (assign_public_message_ids, build_public_events, check_public_asides,
                            interview_messages, private_feedback, public_context, public_source_ids,
                            public_commentary_messages, check_public_commentary, compress_own_memory,
                            settle_commitments)
from test_native_observation import observe
from test_showrunner import CONFIG


TAGS = ["AUS", "GER", "FRA"]


class StoryChannelTests(unittest.TestCase):
    def fixture(self):
        observed = observe()
        observed["recent_history"] = [{"turn_id": 1,
            "private_decision": {"country": "AUS", "reason": "SEALED_REASON_CANARY", "actions": []},
            "private_intent": {"country": "AUS", "goal": "SEALED_INTENT_CANARY"},
            "private_diplomacy": {"sent": [], "received": [{"country": "GER", "to": "AUS", "message": "LETTER_CANARY"}]},
            "referee_feedback": [{"country": "AUS", "reason": "JUDGE_CANARY"}],
            "public_diplomacy": assign_public_message_ids([
                {"country": "GER", "message": "We have no secret plans."}], "game", 1)}]
        before = {"provinces": {"10": {"controller": "AUS"}}, "public_wars": []}
        after = {"provinces": {"10": {"controller": "GER", "secret": "WORLD_PRIVATE_CANARY"}},
                 "public_wars": [{"first": "GER", "second": "AUS", "reason": "WAR_REASON_CANARY"}]}
        observed["public_board"] = build_public_events(before, after, TAGS, {"before": 10, "after": 20})
        public = assign_public_message_ids([{"country": "FRA", "message": "I oppose this."}], "game", 2)
        show = settings({"strict_public_context": True, "performance": True,
                         "compact_native_context": True,
                         "profiles": {"AUS": {"name": "Austria", "persona": "谨慎", "experience": "PRIVATE_EXPERIENCE_CANARY",
                                                "experience_file": "PRIVATE_EXPERIENCE_FILE_CANARY"}},
                         "commitment_memory": {"AUS": [
                             {"country": "AUS", "to": "GER", "text": "PROMISE_CANARY", "visibility": "private"},
                             {"country": "FRA", "to": None, "text": "PUBLIC_PROMISE", "visibility": "public"}]}}, TAGS)
        intent = {"country": "AUS", "goal": "GOAL_CANARY", "plan": "PLAN_CANARY", "concealment": "", "predictions": []}
        private = {"sent": [], "received": [{"country": "GER", "to": "AUS", "message": "CURRENT_INBOX_CANARY"}]}
        return observed, public, show, intent, private

    def test_public_prompt_uses_allowlist_after_all_private_extensions(self):
        observed, public, show, intent, private = self.fixture()
        messages = extend_prompt(country_messages(observed, "diplomacy", public), observed,
                                 "diplomacy", show, TAGS, intent, private)
        projected = public_context(messages, observed)
        serialized = json.dumps(projected, ensure_ascii=False)
        self.assertNotIn("CANARY", serialized)
        self.assertIn("PUBLIC_PROMISE", serialized)
        self.assertIn("We have no secret plans.", serialized)
        self.assertIn("I oppose this.", serialized)
        self.assertIn("province_control_changed", serialized)
        payload = json.loads(projected[1]["content"])
        self.assertEqual(payload["own_profile"], {"name": "Austria", "persona": "谨慎"})
        self.assertNotIn("action_options", payload["own_observation"])
        self.assertNotIn("own_score_context", payload)
        self.assertEqual(len(public_source_ids(messages, observed)), 4)
        # Strategic context still contains the country's own secrets.
        strategic = extend_prompt(country_messages(observed, "decision", public), observed,
                                  "decision", show, TAGS, intent, private)
        self.assertIn("CURRENT_INBOX_CANARY", strategic[1]["content"])

    def test_native_call_preserves_valid_asides_and_blocks_private_refs(self):
        observed, public, show, intent, private = self.fixture()
        aside = {"about": "GER", "stance": "skeptical", "ref": observed["public_board"][0]["id"],
                 "text": "你说和平，边界怎么变了？"}
        response = {"country": "AUS", "message": "请解释。", "public_asides": [aside]}
        def fake_complete(config, messages):
            self.assertNotIn("CANARY", json.dumps(messages))
            return response, {"requested_model": CONFIG["model"]}
        with tempfile.TemporaryDirectory() as folder, patch("model_trial.complete", side_effect=fake_complete) as mocked:
            result = call_country(Path(folder), CONFIG, observed, "diplomacy", public,
                                  private, tags=TAGS, show_config=show, intent=intent)
            self.assertEqual(result["public_asides"], [aside])
            self.assertEqual(mocked.call_count, 1)
        with self.assertRaisesRegex(ValueError, "public source"):
            check_public_asides({"public_asides": [{**aside, "ref": "private-letter-id"}]}, TAGS, {aside["ref"]})
        with self.assertRaises(ValueError):
            check_message({"country": "AUS", "to": "GER", "message": "x", "public_asides": [aside]},
                          "AUS", TAGS, 1, private=True)

    def test_events_only_describe_observed_changes_and_ids_are_stable(self):
        observed, public, *_ = self.fixture()
        events = observed["public_board"]
        self.assertEqual(len(events), 2)
        self.assertTrue(all(row["visibility"] == "public" for row in events))
        self.assertNotIn("CANARY", json.dumps(events))
        self.assertNotIn("attacker", json.dumps(events))
        self.assertEqual(assign_public_message_ids(public, "game", 2), public)
        self.assertNotEqual(assign_public_message_ids(public, "other-game", 2)[0]["id"], public[0]["id"])
        self.assertEqual(build_public_events({}, {}, TAGS, {"before": 10, "after": 20}), [])

    def test_global_judge_prose_never_becomes_private_feedback(self):
        review = {"country": "AUS", "approved": True, "request_sha256": "sealed",
                  "reason": "OTHER_COUNTRY_SECRET", "concerns": ["OTHER_LETTER"]}
        feedback = private_feedback(review, "AUS")
        self.assertEqual(feedback, {"country": "AUS", "approved": True, "request_sha256": "sealed"})
        with self.assertRaises(ValueError):
            private_feedback(review, "GER")

    def test_interview_is_audience_only_and_own_visible_sources(self):
        observed, public, show, intent, private = self.fixture()
        decision = {"country": "AUS", "actions": [], "reason": "SEALED_REASON_CANARY"}
        original = copy.deepcopy((observed, decision, public, private, intent))
        messages = interview_messages(observed, decision, "你刚才为什么犹豫？", public=public, private=private, intent=intent)
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["visibility"], "audience_only")
        self.assertIn("SEALED_REASON_CANARY", messages[1]["content"])
        self.assertEqual(original, (observed, decision, public, private, intent))
        self.assertTrue(all(row["id"] for row in payload["sources"]))
        with self.assertRaises(ValueError):
            interview_messages(observed, decision, "question", private={"received": [{"country": "GER", "to": "FRA"}]})

    def test_retold_letter_is_new_claim_and_game_time_deadline_survives_ledger(self):
        value = {"country": "AUS", "to": "FRA", "message": "德国刚答应退兵。", "commitments": []}
        self.assertEqual(check_message(value, "AUS", TAGS, 1, private=True), value)
        self.assertNotIn("original_letter", value)
        promise = {"text": "我会等。", "to": "GER", "due_turn": 2, "due_date_raw": 200, "condition": ""}
        speech = {"country": "AUS", "message": "我会等。", "commitments": [promise]}
        check_message(speech, "AUS", TAGS, 1)
        self.assertEqual(ledger([speech], [], 1)["entries"][0]["due_date_raw"], 200)

    def test_public_host_cannot_see_audience_material_or_certify_private_refs(self):
        observed, public, *_ = self.fixture()
        messages = public_commentary_messages(observed, public)
        self.assertNotIn("CANARY", json.dumps(messages))
        sources = public_source_ids(messages, observed)
        value = {"commentary": [{"ref": public[0]["id"], "text": "法国在公开质疑。"}]}
        self.assertEqual(check_public_commentary(value, sources), value)
        with self.assertRaisesRegex(ValueError, "public source"):
            check_public_commentary({"commentary": [{"ref": "sealed-reason", "text": "leaked"}]}, sources)

    def test_own_memory_retains_source_ids_and_privacy_with_bounded_letters(self):
        observed, *_ = self.fixture()
        history = []
        for turn in range(1, 5):
            row = copy.deepcopy(observed["recent_history"][0])
            row["turn_id"] = turn
            row["private_diplomacy"]["received"][0]["id"] = "original-letter-" + str(turn)
            history.append(row)
        original = copy.deepcopy(history)
        memory = compress_own_memory(history, "AUS", max_turns=3, max_letters=2)
        self.assertEqual(memory["visibility"], "player_private")
        self.assertEqual([row["turn_id"] for row in memory["records"]], [2, 3, 4])
        letters = [letter for row in memory["records"] for letter in row.get("private_letters", [])]
        self.assertEqual({row["source_id"] for row in letters}, {"original-letter-3", "original-letter-4"})
        self.assertNotIn("JUDGE_CANARY", json.dumps(memory))
        self.assertIn("SEALED_REASON_CANARY", json.dumps(memory))
        self.assertEqual(history, original)
        history[0]["private_diplomacy"]["received"][0]["to"] = "FRA"
        with self.assertRaises(ValueError):
            compress_own_memory(history, "AUS")

    def test_host_comments_reference_only_actual_public_sources_and_are_reactable(self):
        observed, public, *_ = self.fixture()
        base = {"id": "host-verified", "visibility": "public", "source_kind": "host_commentary",
                "message": "法国正在公开追问。", "refs": [public[0]["id"]]}
        observed["public_host_messages"] = [base,
            {**base, "id": "host-private", "refs": ["private-letter-id"], "message": "PRIVATE_HOST_CANARY"},
            {**base, "id": "host-interview", "visibility": "audience_only", "message": "INTERVIEW_CANARY"},
            {**base, "id": "host-recursive", "refs": [base["id"]], "message": "RECURSIVE_CANARY"},
            {**base, "id": "host-wrong-kind", "source_kind": "audience_interview", "message": "WRONG_KIND_CANARY"}]
        messages = country_messages(observed, "diplomacy", public)
        projected = public_context(messages, observed)
        payload = json.loads(projected[1]["content"])
        self.assertEqual(payload["public_host_messages"], [base])
        self.assertNotIn("HOST_CANARY", projected[1]["content"])
        self.assertNotIn("INTERVIEW_CANARY", projected[1]["content"])
        self.assertIn(base["id"], public_source_ids(messages, observed))
        check_public_asides({"public_asides": [{"about": "FRA", "stance": "质疑", "ref": base["id"], "text": "主持人这话什么意思？"}]},
                            TAGS, public_source_ids(messages, observed))

    def test_commitment_settlement_is_public_predicate_only_and_time_bounded(self):
        observed, public, *_ = self.fixture()
        events = observed["public_board"]
        province = {"kind": "province_control", "province_id": 10, "controller": "GER"}
        war = {"kind": "war_relation", "countries": ["GER", "AUS"], "at_war": True}
        base = {"id": "promise", "country": "GER", "to": "AUS", "due_turn": 2, "due_date_raw": 20,
                "text": "承诺原话", "condition": "", "visibility": "public", "predicate": province}
        entries = [base,
                   {**base, "id": "opposite", "predicate": {**province, "controller": "AUS"}},
                   {**base, "id": "war", "predicate": war},
                   {**base, "id": "free-prose", "predicate": None},
                   {**base, "id": "unparsed-condition", "condition": "如果心情好"},
                   {**base, "id": "condition-false", "condition": "如果奥地利仍控制这里",
                    "condition_predicate": {**province, "controller": "AUS"}},
                   {**base, "id": "future", "due_date_raw": 30},
                   {**base, "id": "no-deadline-observation", "due_date_raw": 15}]
        originals = copy.deepcopy(entries)
        rows = settle_commitments(entries, events, 20, 2)
        self.assertEqual([row["status"] for row in rows], ["fulfilled", "observably_violated", "fulfilled",
                         "unverifiable", "unverifiable", "condition_unmet", "not_due", "unverifiable"])
        self.assertEqual(rows[0]["settlement"]["evidence_ids"], [events[0]["id"]])
        self.assertEqual(entries, originals)
        # A public lie and a sealed execution outcome cannot certify observability.
        false_evidence = [{**events[0], "source": {"kind": "sealed_order"}},
                          {**events[0], "source": {"kind": "player_claim"}},
                          {**events[0], "visibility": "audience_only"}]
        self.assertEqual(settle_commitments([base], false_evidence, 20, 2)[0]["status"], "unverifiable")
        peace = {**base, "predicate": {**war, "at_war": False}}
        self.assertEqual(settle_commitments([peace], [], 20, 2)[0]["status"], "unverifiable")
        legacy = {key: value for key, value in base.items() if key != "due_date_raw"}
        self.assertEqual(settle_commitments([legacy], events, 20, 2)[0]["status"], "fulfilled")
        self.assertEqual(settle_commitments([legacy], events, 20, 3)[0]["status"], "unverifiable")

    def test_private_brief_has_scoped_memory_and_events_but_public_phase_does_not(self):
        observed, public, *_ = self.fixture()
        observed["own_memory"] = compress_own_memory(observed["recent_history"], "AUS")
        own = {"country": "AUS", "native_key": "OWN_EVENT_RAW_KEY", "options": [
            {"native_key": "OWN_OPTION_RAW_KEY", "eligibility": "unavailable", "actionable": False}]}
        foreign = {"country": "GER", "native_key": "FOREIGN_EVENT_CANARY"}
        observed["event_status"] = {"availability": "partial", "capture_complete": False,
                                     "scope": "registered_event_windows", "all_pending_availability": "unavailable",
                                     "choice_api": {"availability": "unavailable"}, "pending": [own, foreign]}
        private = brief(observed)
        self.assertEqual(private["own_memory"]["country"], "AUS")
        self.assertEqual(private["own_pending_events"], [own])
        self.assertEqual(private["event_status"]["all_pending_availability"], "unavailable")
        self.assertNotIn("pending", private["event_status"])
        self.assertNotIn("FOREIGN_EVENT_CANARY", json.dumps(private))
        payload = json.loads(country_messages(observed, "diplomacy", public)[1]["content"])
        for field in ("own_memory", "event_status", "own_pending_events"):
            self.assertNotIn(field, payload["own_observation"])
        observed["own_pending_events"] = [foreign]
        with self.assertRaisesRegex(ValueError, "another country's event"):
            brief(observed)
        observed.pop("own_pending_events")
        observed["own_memory"]["country"] = "GER"
        with self.assertRaisesRegex(ValueError, "another scope"):
            brief(observed)


if __name__ == "__main__":
    unittest.main()
