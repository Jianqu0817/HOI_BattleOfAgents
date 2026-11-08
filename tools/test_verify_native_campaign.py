"""Tamper checks for sealed decisions, exact raw time, and private route projection."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from model_trial import digest
from native_observation import _digest
from showrunner import check_history
from verify_campaign import Audit
from verify_native_campaign import audit_annotation, audit_clock, audit_sealed, projected_history


class NativeCampaignAuditTests(unittest.TestCase):
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

    def test_original_sealed_decision_accepted(self):
        audit = self.sealed_audit()
        self.assertTrue(audit.checks)
        self.assertTrue(all(row["status"] == "passed" for row in audit.checks))

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


if __name__ == "__main__":
    unittest.main()
