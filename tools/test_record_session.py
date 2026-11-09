import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from record_session import record


class RecordingSessionTests(unittest.TestCase):
    def test_recording_wraps_turns_and_stops_on_campaign_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as folder:
                calls = []
                class Client:
                    def call(self, operation, data=None):
                        calls.append(operation)
                        if operation == "GetProfileList": return {"currentProfileName": "IRON_VERIFY"}
                        if operation == "GetSceneCollectionList": return {"currentSceneCollectionName": "IRON_VERIFY"}
                        if operation == "GetRecordStatus": return {"outputActive": "StartRecord" in calls}
                        if operation == "GetSceneList": return {"currentProgramSceneName": "GAME"}
                        if operation == "GetVideoSettings": return {"baseWidth": 1280, "baseHeight": 720, "outputWidth": 1280, "outputHeight": 720}
                        if operation == "StopRecord": return {"outputPath": "recorded.mkv"}
                        return {}
                    def close(self): pass
                def campaign(*args, **kwargs):
                    self.assertIn("StartRecord", calls)
                    kwargs["before_turn"](1)
                    if fail: raise RuntimeError("MODEL_FAILED")
                    return {"status": "completed"}
                with patch("record_session.ROOT", Path(folder)), patch("record_session.OBS", return_value=Client()), \
                        patch("record_session.preview_loop"), patch("record_session.run_campaign", side_effect=campaign):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "MODEL_FAILED"):
                            record("match", "config", 1, Path(folder), "obs", through_turn=50)
                    else:
                        record("match", "config", 1, Path(folder), "obs", through_turn=50)
                self.assertEqual(calls.count("StartRecord"), 1)
                self.assertEqual(calls.count("StopRecord"), 1)
                saved = json.loads((Path(folder) / "runs/campaigns/match/recording.json").read_text(encoding="utf-8"))
                self.assertFalse(saved["active"])
                self.assertEqual(saved["output_path"], "recorded.mkv")
                self.assertEqual(saved.get("error"), "MODEL_FAILED" if fail else None)

    def test_existing_recording_is_neither_started_nor_stopped(self):
        with tempfile.TemporaryDirectory() as folder:
            class Client:
                calls = []
                def call(self, operation, data=None):
                    self.calls.append(operation)
                    return {"GetProfileList": {"currentProfileName": "IRON_VERIFY"},
                            "GetSceneCollectionList": {"currentSceneCollectionName": "IRON_VERIFY"},
                            "GetRecordStatus": {"outputActive": True}}[operation]
                def close(self): pass
            client = Client()
            with patch("record_session.ROOT", Path(folder)), patch("record_session.OBS", return_value=client):
                with self.assertRaisesRegex(RuntimeError, "already recording"):
                    record("match", "config", 1, Path(folder), "obs")
            self.assertNotIn("StartRecord", client.calls)
            self.assertNotIn("StopRecord", client.calls)


if __name__ == "__main__":
    unittest.main()
