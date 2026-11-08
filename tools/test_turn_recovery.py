"""Saved outcomes determine whether an interrupted action can run again."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from run_turn import check_army_activity_baseline, resume_checkpoint
from army_actions import army_activity
from agent_contract import country_fingerprint
from verify_campaign import Audit, audit_execution


class RecoveryTests(unittest.TestCase):
    def test_army_activity_baseline_is_separate_and_legacy_observations_remain_valid(self):
        state = {"countries": {"GER": {"theatres": {"theatre": {"orders_group": {"id": {"id": 7}, "training": True}}}}}}
        snapshots = {"GER": {"army_activity": army_activity(state["countries"]["GER"])}}
        after = copy.deepcopy(state)
        after["countries"]["GER"]["theatres"]["theatre"]["orders_group"]["training"] = False
        self.assertTrue(check_army_activity_baseline(state, snapshots)["verified"])
        with self.assertRaises(ValueError):
            check_army_activity_baseline(after, snapshots)
        self.assertTrue(check_army_activity_baseline(after, snapshots, after)["verified"])
        self.assertIsNone(check_army_activity_baseline(after, {"GER": {}}))

    def test_training_only_change_is_not_misclassified_as_safe_to_replay(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            result = self.fixture(directory, "unchanged")
            for filename in ("before", "first", "after"):
                path = directory / (filename + ".json")
                state = json.loads(path.read_text(encoding="utf-8"))
                state["countries"]["ITA"]["theatres"] = {"theatre": {"orders_group": {"id": {"id": 7}, "training": filename != "after"}}}
                path.write_text(json.dumps(state), encoding="utf-8")
            first = json.loads((directory / "first.json").read_text(encoding="utf-8"))
            after = json.loads((directory / "after.json").read_text(encoding="utf-8"))
            self.assertEqual(country_fingerprint(first, "ITA"), country_fingerprint(after, "ITA"))
            with self.assertRaisesRegex(ValueError, "changed gameplay"):
                resume_checkpoint(result, directory)
            self.assertEqual(result["actions"][-1]["status"], "unknown")
            result["actions"][-1]["status"] = "not_applied"
            result["before_save"]["parsed_date"] = first["date"]
            requests = {tag: {"actions": [entry["action"] for entry in result["actions"] if entry["country"] == tag]}
                        for tag in result["countries"]}
            audit = Audit(directory)
            audit_execution(audit, directory, result, requests, {tag: {"date": first["date"]} for tag in requests}, 24, "activity")
            check = next(row for row in audit.checks if row["check"] == "activity:attempt1:not_applied_army_activity_unchanged")
            self.assertEqual(check["status"], "failed")

    def test_interruption_before_first_save_rechecks_original_observation(self):
        self.assertEqual(resume_checkpoint({"status": "interrupted", "actions": []}, Path("unused")), (None, []))
        with self.assertRaises(ValueError):
            resume_checkpoint({"status": "interrupted", "actions": [{"status": "unknown"}]}, Path("unused"))

    def fixture(self, directory, outcome):
        country = {"focus": {"completed": []}, "technology": {"slots": {}, "technologies": {}},
                   "production": {}, "units": {}, "politics": {"political_power": 100}}
        before = {"date": "1936.3.15.11", "countries": {tag: copy.deepcopy(country) for tag in ("GER", "ITA", "JAP")}, "states": {}}
        first = copy.deepcopy(before)
        first["countries"]["GER"]["focus"]["current"] = "GER_test"
        after = copy.deepcopy(first)
        if outcome == "applied":
            after["countries"]["ITA"]["focus"]["current"] = "ITA_test"
        elif outcome == "unexplained":
            after["countries"]["ITA"]["politics"]["political_power"] -= 10
        for name, state in (("before", before), ("first", first), ("after", after)):
            (directory / (name + ".json")).write_text(json.dumps(state), encoding="utf-8")
        action = lambda tag: {"id": tag + "-1", "type": "focus.select", "args": {"focus_id": tag + "_test"}}
        save = lambda name: {"name": "hoi4live_" + name, "path": name + ".hoi4"}
        return {"status": "needs_inspection", "countries": ["GER", "ITA", "JAP"], "before_save": save("before"),
                "actions": [{"country": "GER", "action": action("GER"), "status": "applied", "save": save("first")},
                            {"country": "ITA", "action": action("ITA"), "status": "unknown", "save": save("after")}]}

    def test_only_unchanged_failed_action_can_retry(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            result = self.fixture(directory, "unchanged")
            checkpoint, resolutions = resume_checkpoint(result, directory)
            self.assertEqual([entry["status"] for entry in result["actions"]], ["applied", "not_applied"])
            self.assertEqual(checkpoint["countries"]["GER"]["focus"]["current"], "GER_test")
            self.assertEqual(resolutions[0]["resolved_status"], "not_applied")

    def test_success_saved_before_journal_commit_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            result = self.fixture(directory, "applied")
            resume_checkpoint(result, directory)
            self.assertEqual([entry["status"] for entry in result["actions"]], ["applied", "applied"])

    def test_unexplained_changes_or_missing_after_save_stop_recovery(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            result = self.fixture(directory, "unexplained")
            with self.assertRaises(ValueError):
                resume_checkpoint(result, directory)
            result = self.fixture(directory, "unchanged")
            del result["actions"][-1]["save"]
            with self.assertRaises(ValueError):
                resume_checkpoint(result, directory)


if __name__ == "__main__":
    unittest.main()
