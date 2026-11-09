import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from observer import campaign_dir, handler_for, snapshot, summary


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "match"
        self.trial = self.directory / "trials/turn_001"
        self.write(self.directory / "manifest.json", {"name": "match", "status": "ready_for_next_turn", "config": {
            "countries": ["GER"], "turn_limit": 50, "model": {"model": "MODEL", "api_key": "NEVER_EXPOSE_KEY"}},
            "turns": [{"index": 1, "status": "completed", "elapsed_seconds": 30, "native_seconds": 4}]})
        self.write(self.trial / "public_diplomacy.json", [{"country": "GER", "round": 1, "message": "PUBLIC"}])
        self.write(self.trial / "private_messages.json", [{"country": "GER", "to": "ITA", "message": "PRIVATE_LETTER"}])
        self.write(self.trial / "private_intents/GER.json", {"country": "GER", "plan": "PRIVATE_PLAN"})
        self.write(self.trial / "private_decisions/GER.json", {"country": "GER", "actions": [], "reason": "SEALED_REASON"})
        self.write(self.trial / "model_calls/intent_GER.json", {"messages": ["RAW_SYSTEM_SECRET"], "response": {}})

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_public_projection_omits_secrets_and_audience_is_explicit(self):
        public = json.dumps(snapshot(self.directory))
        self.assertIn("PUBLIC", public)
        for forbidden in ("PRIVATE_LETTER", "PRIVATE_PLAN", "SEALED_REASON", "NEVER_EXPOSE_KEY", "RAW_SYSTEM_SECRET"):
            self.assertNotIn(forbidden, public)
        audience = json.dumps(snapshot(self.directory, audience=True))
        for private in ("PRIVATE_LETTER", "PRIVATE_PLAN", "SEALED_REASON"):
            self.assertIn(private, audience)
        self.assertNotIn("NEVER_EXPOSE_KEY", audience)
        self.assertNotIn("RAW_SYSTEM_SECRET", audience)

    def test_partial_public_updates_do_not_mutate_cached_phase_file(self):
        self.write(self.trial / "public_diplomacy.json", [])
        self.write(self.trial / "model_calls/diplomacy_GER.json", {"response": {"country": "GER", "message": "FIRST"}})
        self.assertEqual(len(snapshot(self.directory)["turns"][0]["public"]), 1)
        self.write(self.trial / "model_calls/diplomacy_2_GER.json", {"response": {"country": "GER", "message": "SECOND"}})
        self.assertEqual(len(snapshot(self.directory)["turns"][0]["public"]), 2)

    def test_http_notes_summary_and_no_arbitrary_file_reads(self):
        before = (self.trial / "private_decisions/GER.json").read_bytes()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.directory))
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/api/state") as response:
                self.assertEqual(json.load(response)["completed"], 1)
            body = json.dumps({"turn": 1, "label": "名场面", "text": "人工观察"}).encode()
            with urlopen(Request(base + "/api/notes", data=body, headers={"Content-Type": "application/json"})) as response:
                self.assertTrue(json.load(response)["saved"])
            with urlopen(base + "/api/summary") as response:
                self.assertIn("人工观察", response.read().decode())
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/../secrets/key.txt")
            self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
        self.assertEqual(before, (self.trial / "private_decisions/GER.json").read_bytes())

    def test_rejects_path_names_and_summary_uses_observations_only(self):
        with self.assertRaises(ValueError):
            campaign_dir("../secrets")
        self.assertNotIn("SEALED_REASON", summary(snapshot(self.directory)))


if __name__ == "__main__":
    unittest.main()
