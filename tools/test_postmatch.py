import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_trial import digest
from native_campaign import complete_audience


class PostmatchTests(unittest.TestCase):
    def test_completed_turn_only_no_game_or_player_memory_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / "runs/campaigns/match"
            trial = directory / "trials/turn_001"
            def write(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")
            request = {"country": "AUS", "actions": [], "reason": "SEALED"}
            write(trial / "requests/AUS.json", request)
            write(trial / "private_decisions/AUS.json", request)
            write(trial / "decision_result.json", {"status": "approved", "referee": {"reviews": [
                {"country": "AUS", "approved": True, "request_sha256": digest(request)}]}})
            write(trial / "observations/AUS.json", {"country": "AUS", "turn_id": 1})
            write(trial / "show_profiles.json", {"profiles": {"AUS": {"persona": "FROZEN"}}})
            write(directory / "own_memory/AUS.json", [{"turn_id": 1}])
            write(directory / "manifest.json", {"schema": "iron.native-campaign.v1", "config": {
                "countries": ["AUS"], "show": {}, "model": {}, "commentary_model": {}, "performance": {"enabled": True}},
                "turns": [{"index": 1, "status": "completed"}, {"index": 2, "status": "deciding"}]})
            protected = [trial / "observations/AUS.json", trial / "private_decisions/AUS.json", directory / "own_memory/AUS.json"]
            before = [path.read_bytes() for path in protected]
            with patch("native_campaign.ROOT", root), patch("native_campaign.NativeGame") as game, \
                    patch("native_campaign.post_seal", return_value={}) as performance, \
                    patch("native_campaign.generate_commentary", return_value={"kind": "model_commentary"}) as commentary, \
                    patch("native_campaign.export_episode") as export:
                result = complete_audience("match")
            game.assert_not_called()
            self.assertEqual(performance.call_count, 1)
            self.assertEqual(performance.call_args.kwargs["phase"], "audience")
            self.assertEqual(performance.call_args.args[3]["profiles"]["AUS"]["persona"], "FROZEN")
            commentary.assert_called_once()
            self.assertEqual(export.call_args.args[2], directory / "episodes_postmatch/turn_001")
            self.assertIn("audience_completed_at", result["turns"][0])
            self.assertNotIn("audience_completed_at", result["turns"][1])
            self.assertEqual(before, [path.read_bytes() for path in protected])


if __name__ == "__main__":
    unittest.main()
