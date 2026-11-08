import copy
import json
from pathlib import Path
import tempfile
import unittest

from campaign import execution_mode, load_config, name_ok, config_digest, build_index, remember_commitments, turn_show_config


class CampaignTests(unittest.TestCase):
    def test_persistent_commitments_route_only_to_actual_participants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trial = root / "trial"
            trial.mkdir()
            public = {"id": "1-public", "country": "GER", "to": None, "visibility": "public", "text": "PUBLIC"}
            private = {"id": "1-private", "country": "ITA", "to": "JAP", "visibility": "private", "text": "SECRET"}
            (trial / "commitments.json").write_text(json.dumps({"entries": [public, private]}), encoding="utf-8")
            config = {"countries": ["GER", "ITA", "JAP"], "show": {"profiles": {}}}
            remember_commitments(config, trial, root)
            remember_commitments(config, trial, root)
            show = turn_show_config(config, root)
            self.assertEqual(len(show["commitment_memory"]["GER"]), 1)
            self.assertEqual(len(show["commitment_memory"]["JAP"]), 2)
            (root / "diplomacy/GER.json").write_text(json.dumps([{**private, "source_trial": "trial"}]), encoding="utf-8")
            with self.assertRaises(ValueError):
                turn_show_config(config, root)

    def test_existing_results_select_recovery_without_replaying(self):
        self.assertEqual(execution_mode({"status": "completed_deterministic_turn"}), "done")
        self.assertEqual(execution_mode({"status": "interrupted", "before_clock": {}}), "clock")
        self.assertEqual(execution_mode({"status": "needs_inspection", "actions": []}), "actions")
        with self.assertRaises(ValueError):
            execution_mode({"status": "advancing", "final_save": {"name": "already_saved"}})

    def test_match_names_cannot_escape_runs(self):
        for value in ("../outside", "x/y", "x y", "", "x" * 61):
            with self.assertRaises(ValueError):
                name_ok(value)
        self.assertEqual(name_ok("episode_01"), "episode_01")

    def test_frozen_config_hash_detects_persona_changes(self):
        one = {"show": {"profiles": {"GER": {"persona": "keep promises"}}}}
        two = copy.deepcopy(one)
        self.assertEqual(config_digest(one), config_digest(two))
        two["show"]["profiles"]["GER"]["persona"] = "new profile"
        self.assertNotEqual(config_digest(one), config_digest(two))

    def test_index_contains_only_completed_turns_and_escapes_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_index(root, {"name": "<script>", "turns": [{"index": 1, "status": "completed", "final_date": "1936.1.2.0"}, {"index": 2, "status": "interrupted"}]})
            html = (root / "index.html").read_text(encoding="utf-8")
            self.assertIn("&lt;script&gt;", html)
            self.assertIn("turn_001/index.html", html)
            self.assertNotIn("turn_002/index.html", html)

    def test_invalid_time_and_player_count_stop_before_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = {"countries": ["GER", "ITA", "ENG"], "step_hours": True, "turn_limit": 1, "action_limit": 1, "communication_rounds": 2}
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
