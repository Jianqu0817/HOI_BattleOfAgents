import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from performance import post_seal
from showrunner import settings
from story_channels import assign_public_message_ids
from test_native_observation import observe
from test_showrunner import CONFIG


class PerformanceTests(unittest.TestCase):
    def test_live_host_then_deferred_interview_preserves_public_record(self):
        tags = ["AUS", "GER", "FRA"]
        observed = {tag: {**observe(), "country": tag} for tag in tags}
        public = assign_public_message_ids([{"country": "GER", "message": "公开质问", "commitments": []}], "match", 1)
        with tempfile.TemporaryDirectory() as folder:
            trial = Path(folder)
            (trial / "private_decisions").mkdir()
            for tag in tags:
                (trial / "private_decisions" / (tag + ".json")).write_text(json.dumps({"country": tag, "actions": [], "reason": "SECRET_" + tag}), encoding="utf-8")
            (trial / "public_diplomacy.json").write_text(json.dumps(public), encoding="utf-8")
            before = {tag: (trial / "private_decisions" / (tag + ".json")).read_bytes() for tag in tags}
            host = {"response": {"commentary": [{"ref": public[0]["id"], "text": "这话谁来回应？"}]}}
            interview = {"response": {"country": "AUS", "answer": "我有意见。"}}
            with patch("performance.recorded_complete", return_value=host) as model:
                live = post_seal(trial, observed, CONFIG, settings({}, tags), {"enabled": True}, phase="live")
                self.assertEqual(model.call_count, 1)
                self.assertEqual(model.call_args.args[1], "public_host")
                self.assertNotIn("SECRET_", json.dumps(model.call_args.args[3]))
            self.assertEqual(live["interviews"], [])
            with patch("performance.recorded_complete", return_value=interview) as model:
                audience = post_seal(trial, observed, CONFIG, settings({}, tags), {"enabled": True}, phase="audience")
                self.assertEqual(model.call_count, 1)
                self.assertEqual(model.call_args.args[1], "audience_interview_AUS")
                prompt = json.dumps(model.call_args.args[3])
                self.assertIn("SECRET_AUS", prompt)
                self.assertNotIn("SECRET_GER", prompt)
            self.assertEqual(audience["host"], live["host"])
            self.assertEqual(audience["interviews"][0]["visibility"], "audience_only")
            self.assertFalse(audience["writeback"])
            self.assertEqual(before, {tag: (trial / "private_decisions" / (tag + ".json")).read_bytes() for tag in tags})

    def test_interviews_are_separate_and_seal_unchanged(self):
        tags = ["AUS", "GER", "FRA"]
        observed = {tag: {**observe(), "country": tag} for tag in tags}
        public = assign_public_message_ids([{"country": "GER", "message": "公开质问", "commitments": []}], "match", 1)
        with tempfile.TemporaryDirectory() as folder:
            trial = Path(folder)
            (trial / "private_decisions").mkdir()
            for tag in tags:
                (trial / "private_decisions" / (tag + ".json")).write_text(json.dumps({"country": tag, "actions": [], "reason": "SECRET_" + tag}), encoding="utf-8")
            (trial / "public_diplomacy.json").write_text(json.dumps(public), encoding="utf-8")
            before = {tag: (trial / "private_decisions" / (tag + ".json")).read_bytes() for tag in tags}
            def complete(directory, label, model, messages, *args, **kwargs):
                body = json.dumps(messages)
                if label == "public_host":
                    self.assertNotIn("SECRET_", body)
                    return {"response": {"commentary": [{"ref": public[0]["id"], "text": "这话谁来回应？"}]}}
                self.assertIn("SECRET_AUS", body)
                self.assertNotIn("SECRET_GER", body)
                return {"response": {"country": "AUS", "answer": "我有意见。"}}
            with patch("performance.recorded_complete", side_effect=complete) as model:
                result = post_seal(trial, observed, CONFIG, settings({}, tags), {"enabled": True})
            self.assertEqual(model.call_count, 2)
            self.assertFalse(result["writeback"])
            self.assertEqual(result["host"][0]["visibility"], "public")
            self.assertEqual(result["interviews"][0]["visibility"], "audience_only")
            self.assertEqual(before, {tag: (trial / "private_decisions" / (tag + ".json")).read_bytes() for tag in tags})


if __name__ == "__main__":
    unittest.main()
