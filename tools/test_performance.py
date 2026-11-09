import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from performance import post_seal
from campaign import load_config
from model_trial import decide
from m0_probe import write_json
from verify_campaign import Audit
from verify_native_campaign import audit_performance
from showrunner import settings
from story_channels import assign_public_message_ids
from test_native_observation import observe
from test_showrunner import CONFIG


class PerformanceTests(unittest.TestCase):
    def test_table_host_uses_round_one_and_players_can_answer_before_sealing(self):
        tags = ["AUS", "GER", "FRA"]
        observations = {tag: observe(tag) for tag in tags}
        show = settings({"table_host": True, "strict_public_context": True, "performance": True,
                         "intents": False, "profiles": {tag: {"scenario_goal": "SECRET_" + tag} for tag in tags}}, tags)
        production_show = load_config("configs/campaign.tyrol.json")["show"]
        for key in ("host_skills", "host_readings"):
            show[key] = production_show[key]
        show["public_host_cues"] = {str(observations["GER"]["turn_id"]): "HOST_CUE_TODAY", "99": "HOST_CUE_LATER"}
        config = {"countries": tags, "show": show, "performance": {"enabled": True, "interviews_per_turn": 0}}
        calls = {}

        def fake(directory, label, model, messages, phase, country=None, require_settings=False):
            payload = json.loads(messages[1]["content"])
            if phase == "public_host":
                self.assertTrue(all(name in messages[0]["content"] for name in show["host_skills"]))
                self.assertIn("HOST_CUE_TODAY", payload["task"])
                self.assertNotIn("HOST_CUE_LATER", str(messages))
                self.assertNotIn("SECRET_", str(payload))
                self.assertEqual([r["round"] for r in payload["public_diplomacy"]], [1] * len(tags))
                value = {"commentary": [{"ref": payload["public_diplomacy"][0]["id"], "text": "这话有人敢认吗？"}]}
            elif phase == "referee":
                value = {"reviews": [{"country": row["country"], "approved": True, "request_sha256": row["request_sha256"]}
                                     for row in payload["countries"]]}
            else:
                self.assertEqual(payload["own_profile"]["scenario_goal"], "SECRET_" + country)
                self.assertFalse(any("SECRET_" + other in str(payload) for other in tags if other != country))
                if phase == "decision":
                    self.assertEqual(payload["table_host_messages"][0]["message"], "这话有人敢认吗？")
                    value = {"identity": payload["own_observation"]["identity"], "actions": [], "reason": "保持部署"}
                else:
                    if label.startswith("diplomacy_2_"):
                        self.assertEqual(payload["public_host_messages"][0]["message"], "这话有人敢认吗？")
                    value = {"country": country, "message": "听见了。"}
            if phase != "public_host":
                self.assertNotIn("HOST_CUE_", str(messages))
                self.assertFalse(any(name in str(messages) for name in show["host_skills"]))
            calls[label] = {"messages": messages, "response": value}
            return calls[label]

        with tempfile.TemporaryDirectory() as folder:
            trial = Path(folder)
            for tag in tags:
                write_json(trial / "observations" / (tag + ".json"), observations[tag])
            with patch("model_trial.recorded_complete", side_effect=fake), patch("performance.recorded_complete", side_effect=fake) as host:
                decide(trial, CONFIG, tags, communication_rounds=2, show_config=show)
                post_seal(trial, observations, CONFIG, show, config["performance"], phase="live")
                self.assertEqual(host.call_count, 1)
            audit = Audit(trial)
            audit_performance(audit, trial, config, observations, calls, "turn")
            self.assertFalse([row for row in audit.checks if row["status"] == "failed"])

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
