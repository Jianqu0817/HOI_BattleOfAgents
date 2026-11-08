"""TTS client contract: missing voices, real WAV validation, cache, frozen provenance."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from gpt_sovits import synthesize, voice_state


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 160)
    return output.getvalue()


class TTSClientTests(unittest.TestCase):
    def config(self, directory):
        for name in ("gpt", "sovits", "reference"):
            (directory / name).write_bytes(name.encode())
        return {"base_url": "http://127.0.0.1:19880", "backend_revision": "test-pin", "voices": {
            "qwen": {"gpt_weights": str(directory / "gpt"), "sovits_weights": str(directory / "sovits"),
                     "ref_audio_path": str(directory / "reference"), "prompt_text": "参考台词"}}}

    def test_missing_reference_does_not_call_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            config["voices"]["qwen"]["ref_audio_path"] = ""
            with patch("gpt_sovits._request") as request:
                receipt = synthesize(config, {"character_id": "qwen", "text": "你好"}, tmp)
            self.assertEqual(receipt["status"], "missing_reference")
            request.assert_not_called()

    def test_generated_cache_preserves_line_and_invalidates_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            config = self.config(directory)
            line = {"character_id": "qwen", "text": "原始台词。", "event_id": "event-7",
                    "visibility": "audience_private", "line_id": "line-9"}
            with patch("gpt_sovits._request", side_effect=[b'{}', b'{}', wav()]) as request:
                result = synthesize(config, line, directory / "audio")
            self.assertEqual(result["status"], "generated")
            self.assertEqual(result["visibility"], line["visibility"])
            self.assertEqual(json.loads(json.dumps(request.call_args.args[3]))["text"], line["text"])
            with patch("gpt_sovits._request") as request:
                cached = synthesize(config, line, directory / "audio")
            self.assertEqual(cached["status"], "cached")
            request.assert_not_called()
            (directory / "reference").write_bytes(b"new reference")
            with patch("gpt_sovits._request", side_effect=[b'{}', b'{}', wav()]):
                changed = synthesize(config, line, directory / "audio")
            self.assertNotEqual(changed["cache_key"], result["cache_key"])

    def test_error_json_is_not_saved_as_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch("gpt_sovits._request", side_effect=[b'{}', b'{}', b'{"message":"failed"}']):
                result = synthesize(self.config(directory), {"character_id": "qwen", "text": "你好"}, directory / "audio")
            self.assertEqual(result["status"], "failed")
            self.assertFalse(list((directory / "audio").glob("*.wav")))


if __name__ == "__main__":
    unittest.main()
