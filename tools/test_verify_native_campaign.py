"""Tamper checks for sealed decisions, exact raw time, and private route projection."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from model_trial import digest
from native_observation import _digest
from showrunner import check_history
from story_channels import interview_messages, public_context
from verify_campaign import Audit
from verify_native_campaign import (audit_annotation, audit_clock, audit_commentary_normalization,
                                    audit_performance, audit_public_prompt, audit_sealed, projected_history, response_ids_status)


class NativeCampaignAuditTests(unittest.TestCase):
    def test_placeholder_ids_remain_unknown_and_real_duplicates_fail(self):
        self.assertIsNone(response_ids_status(["real-1", "chatcmpl-0", "chatcmpl-0"]))
        self.assertIsNone(response_ids_status([None, "real-1"]))
        self.assertFalse(response_ids_status(["real-1", "real-1"]))
        self.assertTrue(response_ids_status(["real-1", "real-2"]))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        observation = {"match_id": "fixture", "turn_id": 1, "country": "AUS",
                       "date": "1936.5.24.4", "date_raw": 60762795,
                       "execution_backend": "native", "action_options": {}}
        observation["observation_id"] = _digest(observation)
        self.observations = {"AUS": observation}
        self.request = {key: observation[key] for key in ("match_id", "turn_id", "country", "date", "observation_id")}
        self.request.update(actions=[], reason="Keep current orders")
        self.decisions = {"status": "approved", "request_hashes": {"AUS": digest(self.request)},
                          "referee": {"reviews": [{"country": "AUS", "approved": True,
                                                     "request_sha256": digest(self.request)}]}}
        self.result = {"requests": {"AUS": copy.deepcopy(self.request)}}
        for folder in ("private_decisions", "requests"):
            self.write(folder + "/AUS.json", self.request)
        self.write("model_calls/decision_AUS.json", {"response": self.request})

    def write(self, path, value):
        target = self.path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value), encoding="utf-8")

    def sealed_audit(self):
        audit = Audit(self.path)
        audit_sealed(audit, self.path, self.observations, self.decisions, self.result, "turn_1", 2)
        return audit

    def test_deferred_interviews_are_pending_not_missing_live_content(self):
        self.write("performance.json", {"schema": "iron.performance.v1", "host": [], "interviews": [], "writeback": False})
        self.write("public_diplomacy.json", [])
        config = {"countries": ["AUS"], "audience_timing": "postmatch",
                  "performance": {"enabled": True, "public_host": False, "interviews_per_turn": 1}}
        audit = Audit(self.path)
        audit_performance(audit, self.path, config, self.observations, {}, "deferred")
        self.assertFalse(any(row["status"] == "failed" for row in audit.checks))
        self.assertTrue(any(row["check"].endswith("audience_interviews_pending") and row["status"] == "unknown"
                            for row in audit.checks))
        config["audience_timing"] = "inline"
        audit = Audit(self.path)
        audit_performance(audit, self.path, config, self.observations, {}, "inline")
        self.assertTrue(any(row["status"] == "failed" for row in audit.checks))

    def test_original_sealed_decision_accepted(self):
        audit = self.sealed_audit()
        self.assertTrue(audit.checks)
        self.assertTrue(all(row["status"] == "passed" for row in audit.checks))

    def test_match_identity_confirmation_preserves_original_orders_and_reason(self):
        from model_trial import match_identity_repair_messages
        raw = {**self.request, 'match_id': 'mistyped'}
        repair_label = 'decision_AUS_match_identity_repair1'
        self.write('model_calls/decision_AUS.json', {'response': raw, 'metadata': {'requested_model': 'same-model'},
                                                  'match_identity_repair': repair_label})
        confirmed = {'phase': 'decision_identity_repair', 'country': 'AUS', 'response': self.request,
                     'messages': match_identity_repair_messages(raw, self.observations['AUS']),
                     'metadata': {'requested_model': 'same-model'}}
        self.write('model_calls/' + repair_label + '.json', confirmed)
        self.assertTrue(all(row['status'] == 'passed' for row in self.sealed_audit().checks))
        confirmed['response'] = {**self.request, 'reason': 'Changed'}
        self.write('model_calls/' + repair_label + '.json', confirmed)
        self.assertTrue(any(row['status'] == 'failed' for row in self.sealed_audit().checks))

    def test_changed_sealed_reason_rejected_even_with_valid_identity(self):
        changed = {**self.request, "reason": "Changed after referee approval"}
        self.write("private_decisions/AUS.json", changed)
        audit = self.sealed_audit()
        self.assertTrue(any(row["status"] == "failed" and "unchanged_model_referee_execution" in row["check"] for row in audit.checks))

    def test_changed_executed_action_list_rejected(self):
        self.result["requests"]["AUS"]["actions"] = [{"id": "invented", "type": "division.move", "args": {}}]
        audit = self.sealed_audit()
        self.assertTrue(any(row["status"] == "failed" for row in audit.checks))

    def test_one_extra_native_hour_rejected(self):
        audit = Audit(self.path)
        initial, hours, index = 60762795, 360, 5
        expected = initial + hours * index
        native = {"step_hours": hours, "date_raw": expected}
        capture, entry = {"date_raw": expected}, {"final_date_raw": expected}
        audit_clock(audit, initial, hours, index, native, capture, entry, "original")
        self.assertEqual(audit.checks[-1]["status"], "passed")
        native["date_raw"] += 1
        audit_clock(audit, initial, hours, index, native, capture, entry, "tampered")
        self.assertEqual(audit.checks[-1]["status"], "failed")

    def test_adaptive_clock_uses_previous_actual_endpoint(self):
        audit = Audit(self.path)
        native = {"step_hours": 24, "requested_step_hours": 24, "remaining_budget_hours": 0, "date_raw": 1100}
        audit_clock(audit, 1000, 24, 3, native, {"date_raw": 1100}, {"final_date_raw": 1100}, "adaptive", start_raw=1076)
        self.assertTrue(all(row["status"] == "passed" for row in audit.checks))

    def test_safe_public_boundary_can_finish_before_budget(self):
        audit = Audit(self.path)
        state = {"date_raw": 1006, "paused": True, "played_index": 3, "country_index": 3}
        native = {"step_hours": 6, "requested_step_hours": 24, "remaining_budget_hours": 18,
                  "date_raw": 1006, "early_end": {"reason": "public_state_changed", "remaining_budget_hours": 18,
                    "details": {"war_changed": True}, "state": state, "clock": {"paused": True, "remaining_hours": 0}}}
        audit_clock(audit, 1000, 24, 1, native, {"date_raw": 1006}, {"final_date_raw": 1006}, "safe", start_raw=1000)
        self.assertTrue(all(row["status"] == "passed" for row in audit.checks))
        native["early_end"]["details"]["controller_changed"] = True
        audit_clock(audit, 1000, 24, 1, native, {"date_raw": 1006}, {"final_date_raw": 1006}, "unsafe", start_raw=1000)
        self.assertEqual(audit.checks[-1]["status"], "failed")

    def test_unrecorded_short_clock_and_pending_choice_cannot_complete(self):
        for early in (None, {"reason": "pending_decision"}):
            audit = Audit(self.path)
            native = {"step_hours": 6, "date_raw": 1006, "early_end": early}
            audit_clock(audit, 1000, 24, 1, native, {"date_raw": 1006}, {"final_date_raw": 1006}, "unsafe", start_raw=1000)
            self.assertTrue(any(row["status"] == "failed" for row in audit.checks))

    def public_messages(self):
        observation = self.observations["AUS"]
        messages = [{"role": "system", "content": "public"}, {"role": "user", "content": json.dumps({
            "task": "speak", "own_observation": {"identity": {key: observation[key] for key in
                 ("match_id", "turn_id", "country", "date", "observation_id")}},
            "public_diplomacy": [], "own_memory": {"secret": "own plan"}})}]
        return public_context(messages, observation)

    def test_final_public_prompt_rejects_private_memory_after_projection(self):
        observation = self.observations["AUS"]
        messages = self.public_messages()
        audit = Audit(self.path)
        audit_public_prompt(audit, messages, observation, "safe")
        self.assertTrue(all(row["status"] == "passed" for row in audit.checks))
        payload = json.loads(messages[1]["content"])
        payload["own_observation"]["own_memory"] = {"records": [{"private_letters": ["secret"]}]}
        messages[1]["content"] = json.dumps(payload)
        audit_public_prompt(audit, messages, observation, "leak")
        self.assertTrue(any(row["status"] == "failed" and "final_public_allowlist" in row["check"] for row in audit.checks))

    def test_public_host_cannot_cite_a_sealed_decision(self):
        messages = self.public_messages()
        payload = json.loads(messages[1]["content"])
        payload["public_host_messages"] = [{"id": "host-1", "visibility": "public", "source_kind": "host_commentary",
            "message": "I know the secret orders", "refs": ["sealed-AUS"]}]
        messages[1]["content"] = json.dumps(payload)
        audit = Audit(self.path)
        audit_public_prompt(audit, messages, self.observations["AUS"], "host", host=False)
        self.assertEqual(audit.checks[0]["status"], "failed")

    def test_public_commitment_memory_survives_final_prompt_reprojection(self):
        messages = self.public_messages()
        payload = json.loads(messages[1]["content"])
        payload["public_commitment_memory"] = [{"country": "AUS", "to": None, "text": "公开承诺", "visibility": "public"}]
        messages[1]["content"] = json.dumps(payload)
        audit = Audit(self.path)
        audit_public_prompt(audit, messages, self.observations["AUS"], "public")
        self.assertTrue(all(row["status"] == "passed" for row in audit.checks))

    def test_private_commitment_cannot_hide_under_public_memory_alias(self):
        messages = self.public_messages()
        payload = json.loads(messages[1]["content"])
        payload["public_commitment_memory"] = [{"country": "GER", "to": "ITA", "text": "私密承诺", "visibility": "private"}]
        messages[1]["content"] = json.dumps(payload)
        audit = Audit(self.path)
        audit_public_prompt(audit, messages, self.observations["AUS"], "leak")
        self.assertEqual(audit.checks[0]["status"], "failed")

    def test_interview_cannot_receive_another_countrys_seal(self):
        observation = {**self.observations["AUS"], "divisions": [], "army": {"wars": []},
                       "naval_air": {"air_wings": [], "task_forces": []}, "unavailable": {}}
        private, intent = {"sent": [], "received": []}, {"country": "AUS"}
        self.write("private_diplomacy/AUS.json", private)
        self.write("private_intents/AUS.json", intent)
        self.write("public_diplomacy.json", [])
        config = {"countries": ["AUS"], "performance": {"enabled": True, "public_host": False}}
        interview = {"country": "AUS", "answer": "My own answer", "question": "How do you feel?", "visibility": "audience_only"}
        self.write("performance.json", {"schema": "iron.performance.v1", "host": [], "interviews": [interview], "writeback": False})
        call = {"messages": interview_messages(observation, self.request, interview["question"], public=[], private=private, intent=intent),
                "response": {"country": "AUS", "answer": interview["answer"]}}
        audit = Audit(self.path)
        audit_performance(audit, self.path, config, {"AUS": observation}, {"audience_interview_AUS": call}, "safe")
        self.assertTrue(all(row["status"] == "passed" for row in audit.checks))
        payload = json.loads(call["messages"][1]["content"])
        payload["materials"]["locked_decision"] = {"country": "GER", "reason": "Secret enemy orders"}
        call["messages"][1]["content"] = json.dumps(payload)
        audit_performance(audit, self.path, config, {"AUS": observation}, {"audience_interview_AUS": call}, "leak")
        self.assertTrue(any(row["status"] == "failed" and "interview_exact_own_scope" in row["check"] for row in audit.checks))

    def commentary_fixture(self):
        from episode import normalize_commentary_quotes
        base = {"events": [{"id": "T1-E001", "text": "公开发言，接下来继续。"}]}
        raw = {"title": "回顾", "facts": [{"evidence_id": "T1-E001", "quote": "公开发言。"}],
               "analysis": [], "jokes": [], "highlights": []}
        normalized, changes = normalize_commentary_quotes(raw, base)
        call = {"audience": "audience_full", "response": raw,
                "normalized_response": normalized, "normalizations": changes}
        commentary = {"audience": "audience_full", "content": copy.deepcopy(normalized), "normalizations": copy.deepcopy(changes)}
        return base, call, commentary

    def test_commentary_terminal_period_repair_recomputed_from_raw(self):
        base, call, commentary = self.commentary_fixture()
        audit = Audit(self.path)
        audit_commentary_normalization(audit, call, commentary, base, "commentary")
        self.assertTrue(all(row["status"] == "passed" for row in audit.checks))
        self.assertEqual(call["response"]["facts"][0]["quote"], "公开发言。")
        self.assertEqual(commentary["content"]["facts"][0]["quote"], "公开发言")

    def test_commentary_tampered_normalized_value_or_receipt_rejected(self):
        for target in ("normalized_response", "normalizations"):
            base, call, commentary = self.commentary_fixture()
            if target == "normalized_response":
                call[target] = {**call[target], "facts": []}
            else:
                call[target] = []
                commentary[target] = []
            audit = Audit(self.path)
            audit_commentary_normalization(audit, call, commentary, base, "tampered")
            self.assertEqual(audit.checks[-1]["status"], "failed")

    def test_projected_private_history_rejects_unaddressed_inbox(self):
        own = {"own_decision": {"country": "AUS"}, "own_intent": {"country": "AUS"},
               "own_private_diplomacy": {"sent": [], "received": [{"country": "GER", "to": "ITA", "message": "secret"}]}}
        with self.assertRaisesRegex(ValueError, "unaddressed private diplomacy"):
            check_history(projected_history([own]), "AUS")

    def test_annotation_repair_cannot_change_public_speech(self):
        original = {"country": "AUS", "message": "Original exact words", "commitments": []}
        changed = {**original, "message": "Rewritten diplomatic offer"}
        record = {"response": original, "normalized_response": changed, "annotation_repair": "repair"}
        calls = {"repair": {"response": changed, "messages": [{}, {"content": "{}"}]}}
        audit = Audit(self.path)
        audit_annotation(audit, record, calls, "public")
        self.assertEqual(audit.checks[-1]["status"], "failed")

    def annotation_fixture(self):
        from communication_annotations import project_commitment_quotes
        raw = {"country": "SWI", "extra_model_note": "not a typed channel field", "replies": [
            {"to": "ITA", "message": "收到，我知道了。", "commitments": [
                {"text": "我会保持中立", "to": "SWI", "due_turn": 8}]}]}
        typed = {key: value for key, value in raw.items() if key != "extra_model_note"}
        projected, changes = project_commitment_quotes(typed)
        return {"phase": "reply", "response": raw, "normalized_response": projected,
                "messages": [{}, {"content": json.dumps({"own_observation": {"identity": {"turn_id": 8}}})}],
                "normalizations": ["extra_model_note"],
                "annotation_projection": {"method": "literal_commitment_projection.v1", "changes": changes}}

    def test_foreign_promise_annotation_omission_preserves_literal_reply(self):
        record = self.annotation_fixture()
        audit = Audit(self.path)
        audit_annotation(audit, record, {}, "reply")
        self.assertEqual(audit.checks[-1]["status"], "passed")
        self.assertEqual(record["response"]["replies"][0]["commitments"][0]["text"], "我会保持中立")
        self.assertEqual(record["normalized_response"]["replies"][0]["commitments"], [])

    def test_annotation_projection_forged_receipt_or_normalization_rejected(self):
        for field in ("changes", "normalizations", "method"):
            record = self.annotation_fixture()
            if field == "normalizations":
                record[field].append("replies")
            else:
                record["annotation_projection"][field] = [] if field == "changes" else "forged"
            audit = Audit(self.path)
            audit_annotation(audit, record, {}, "forged")
            self.assertEqual(audit.checks[-1]["status"], "failed")

    def test_annotation_projection_cannot_change_speech_recipient_or_aside(self):
        for key in ("message", "to"):
            record = self.annotation_fixture()
            record["normalized_response"]["replies"][0][key] = "forged"
            audit = Audit(self.path)
            audit_annotation(audit, record, {}, "changed")
            self.assertEqual(audit.checks[-1]["status"], "failed")

    def test_projection_handles_null_note_without_rewriting_public_asides(self):
        from communication_annotations import project_commitment_quotes
        raw = {"country": "SWI", "message": "我保持中立，明白。", "commitments_note": None,
               "public_asides": [{"about": "ITA", "stance": "质问", "ref": "speech-1", "text": "你呢？"}],
               "commitments": [{"text": "我保持中立。", "to": None, "due_turn": 8}]}
        expected, changes = project_commitment_quotes({key: value for key, value in raw.items() if key != "commitments_note"})
        record = {"phase": "diplomacy", "response": raw, "normalized_response": expected,
                  "messages": [{}, {"content": json.dumps({"own_observation": {"identity": {"turn_id": 8}}})}],
                  "normalizations": ["null commitments_note"],
                  "annotation_projection": {"method": "literal_commitment_projection.v1", "changes": changes}}
        audit = Audit(self.path)
        audit_annotation(audit, record, {}, "public")
        self.assertEqual(audit.checks[-1]["status"], "passed")
        record["normalized_response"]["public_asides"][0]["text"] = "另一个问题"
        audit_annotation(audit, record, {}, "changed")
        self.assertEqual(audit.checks[-1]["status"], "failed")

    def delivery_fixture(self, *, annotations=False):
        from communication_annotations import project_commitment_quotes, project_reply_routes
        raw = {"country": "ITA", "replies": [
            {"to": "AUS", "message": "我支持你，明白。", "commitments": [{"text": "我支持你。", "to": "AUS", "due_turn": 17}]},
            {"to": "SWI", "message": "未获准送达的原文"},
            {"to": "AUS", "message": "重复回复原文"}]}
        if not annotations:
            raw["replies"][0].pop("commitments")
        received = [{"country": "AUS", "to": "ITA", "message": "来信"}]
        routed, rejected = project_reply_routes(raw, "ITA", received)
        record = {"phase": "reply", "country": "ITA", "response": raw, "normalized_response": routed,
                  "messages": [{}, {"content": json.dumps({"own_observation": {"identity": {"turn_id": 17}},
                                                          "own_private_diplomacy": {"received": received}})}],
                  "delivery_projection": {"method": "current_sender_delivery.v1", "rejected": rejected}}
        if annotations:
            projected, changes = project_commitment_quotes(routed, turn_id=17)
            record["normalized_response"] = projected
            record["annotation_projection"] = {"method": "literal_commitment_projection.v1", "changes": changes}
        return record

    def test_reply_delivery_projection_keeps_first_sender_and_retains_rejections(self):
        record = self.delivery_fixture()
        audit = Audit(self.path)
        audit_annotation(audit, record, {}, "delivery")
        self.assertTrue(all(row["status"] == "passed" for row in audit.checks))
        self.assertEqual(len(record["response"]["replies"]), 3)
        self.assertEqual(len(record["normalized_response"]["replies"]), 1)
        self.assertEqual([row["reason"] for row in record["delivery_projection"]["rejected"]],
                         ["not_a_current_sender", "duplicate_reply"])

    def test_country_whitespace_receipt_composes_with_reply_delivery(self):
        record = self.delivery_fixture()
        record['response']['country'] += '\n'
        record['identity_whitespace_trimmed'] = True
        payload = json.loads(record['messages'][1]['content'])
        payload['own_observation']['identity']['country'] = 'ITA'
        record['messages'][1]['content'] = json.dumps(payload)
        audit = Audit(self.path)
        audit_annotation(audit, record, {}, 'trimmed')
        self.assertTrue(all(row['status'] == 'passed' for row in audit.checks), audit.checks)
        record['response']['country'] = 'AUS\n'
        audit_annotation(audit, record, {}, 'tampered')
        self.assertTrue(any(row['status'] == 'failed' for row in audit.checks))

    def test_country_whitespace_receipt_composes_with_rejected_public_asides(self):
        aside = {'about': 'GER', 'stance': '追问', 'ref': 'missing', 'text': '旁注'}
        raw = {'country': 'AUS\n', 'message': '原话', 'public_asides': [aside]}
        payload = {'own_observation': {'identity': {'country': 'AUS', 'turn_id': 7}},
                   'participants': ['AUS', 'GER'], 'public_diplomacy': [], 'public_board': [],
                   'public_host_messages': [], 'public_history': []}
        record = {'phase': 'diplomacy', 'country': 'AUS', 'response': raw,
                  'normalized_response': {**raw, 'country': 'AUS', 'public_asides': []},
                  'identity_whitespace_trimmed': True, 'messages': [{}, {'content': json.dumps(payload)}],
                  'aside_projection': {'rejected': [{'aside': aside, 'reason': 'Public aside must reference a supplied public source ID'}]}}
        audit = Audit(self.path)
        audit_annotation(audit, record, {}, 'aside')
        self.assertTrue(all(row['status'] == 'passed' for row in audit.checks), audit.checks)
        record['aside_projection']['rejected'][0]['reason'] = 'wrong'
        audit_annotation(audit, record, {}, 'tampered')
        self.assertTrue(any(row['status'] == 'failed' for row in audit.checks))

    def test_reply_route_projection_precedes_literal_commitment_projection(self):
        record = self.delivery_fixture(annotations=True)
        audit = Audit(self.path)
        audit_annotation(audit, record, {}, "chain")
        self.assertTrue(all(row["status"] == "passed" for row in audit.checks))
        self.assertEqual(record["normalized_response"]["replies"][0]["commitments"][0]["text"], "我支持你")

    def test_reply_delivery_receipt_or_retained_speech_tampering_rejected(self):
        for target in ("receipt", "message", "recipient", "inbox"):
            record = self.delivery_fixture()
            if target == "receipt":
                record["delivery_projection"]["rejected"][0]["country"] = "GER"
            elif target == "message":
                record["normalized_response"]["replies"][0]["message"] = "改写"
            elif target == "recipient":
                record["normalized_response"]["replies"][0]["to"] = "SWI"
            else:
                payload = json.loads(record["messages"][1]["content"])
                payload["own_private_diplomacy"]["received"] = []
                record["messages"][1]["content"] = json.dumps(payload)
            audit = Audit(self.path)
            audit_annotation(audit, record, {}, "tampered")
            self.assertTrue(any(row["status"] == "failed" for row in audit.checks))


if __name__ == "__main__":
    unittest.main()
