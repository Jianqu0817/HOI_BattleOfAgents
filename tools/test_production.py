import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from production import editing_plan, export_production, main, make_lines
import test_gpt_sovits as tts_fixtures


class ProductionTests(unittest.TestCase):
    def test_export_synthesizes_with_multiple_auxiliary_references(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            tts = tts_fixtures.TTSClientTests().config(output)
            auxiliaries = [output / f"aux-{i}.wav" for i in range(2)]
            for auxiliary in auxiliaries:
                auxiliary.write_bytes(b"reference fixture")
            paths = [str(path.resolve()) for path in auxiliaries]
            tts["voices"]["qwen"]["aux_ref_audio_paths"] = paths
            tts_path = output / "tts.json"
            tts_path.write_text(json.dumps(tts), encoding="utf-8")
            (output / "episode.json").write_text(json.dumps({"events": [{
                "id": "e1", "kind": "private", "country": "SWI",
                "visibility": "private", "text": "保持原来的私密台词。"}]}), encoding="utf-8")
            with patch("gpt_sovits._request", side_effect=[b'{}', b'{}', tts_fixtures.wav()]) as request:
                result = export_production(output, {"assignments": {"SWI": "qwen"},
                                                    "tts_config": str(tts_path)}, synthesize=True)
            self.assertEqual(result["voices"], 1)
            self.assertEqual(request.call_args.args[3]["aux_ref_audio_paths"], paths)
            receipts = json.loads((output / "voice_receipts.json").read_text(encoding="utf-8"))
            self.assertEqual(receipts[0]["visibility"], "private")
            self.assertEqual([row["path"] for row in receipts[0]["sources"]["aux_ref_audio_paths"]], paths)
            self.assertIn("<audio", (output / "cast.html").read_text(encoding="utf-8"))

    def test_cli_reports_incomplete_synthesis_as_failure(self):
        args = ["production.py", "--episode", "unused", "--synthesize"]
        with patch("sys.argv", args), patch("production.read", return_value={}), patch(
                "production.export_production", return_value={"lines": 1, "voices": 0,
                                                              "voice_statuses": {"failed": 1}}), patch("builtins.print"):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 1)

    def test_cli_accepts_complete_or_cached_synthesis(self):
        args = ["production.py", "--episode", "unused", "--synthesize"]
        with patch("sys.argv", args), patch("production.read", return_value={}), patch(
                "production.export_production", return_value={"lines": 1, "voices": 1,
                                                              "voice_statuses": {"cached": 1}}), patch("builtins.print"):
            main()

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
