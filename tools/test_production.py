import json
from pathlib import Path
import tempfile
import unittest

from production import editing_plan, export_production, make_lines


class ProductionTests(unittest.TestCase):
    def test_speech_is_verbatim_and_orders_are_not_speech(self):
        episode = {"events": [{"id": "1", "kind": "public", "country": "GER", "visibility": "public", "text": "原话：别急。"},
                               {"id": "2", "kind": "decision", "country": "GER", "visibility": "private", "text": "秘密攻击"}]}
        lines = make_lines(episode, {"GER": "gpt"})
        self.assertEqual([row["text"] for row in lines], ["原话：别急。"])
        self.assertEqual(lines[0]["visibility"], "public")
        self.assertNotIn("秘密攻击", json.dumps(lines, ensure_ascii=False))

    def test_plan_is_bounded_and_does_not_invent_recording_time(self):
        episode = {"events": [{"id": str(i), "kind": "outcome", "visibility": "public", "text": "发展" * 100} for i in range(30)]}
        plan = editing_plan(episode, target_seconds=30)
        self.assertLessEqual(plan["editorial_seconds"], 30)
        self.assertTrue(all(row["recording_in"] is None for row in plan["cuts"]))
        self.assertTrue(plan["omitted_evidence_ids"])

    def test_export_can_be_used_without_voice_references(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "episode.json").write_text(json.dumps({"events": [{"id": "e1", "kind": "private", "country": "AUS", "visibility": "private", "text": "你好"}]}), encoding="utf-8")
            value = export_production(output, {"assignments": {"AUS": "deepseek"}})
            self.assertEqual(value["lines"], 1)
            self.assertTrue((output / "cast.html").exists())
            self.assertEqual(json.loads((output / "voice_lines.jsonl").read_text(encoding="utf-8"))["visibility"], "private")


if __name__ == "__main__":
    unittest.main()
