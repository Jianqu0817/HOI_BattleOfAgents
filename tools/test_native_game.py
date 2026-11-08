"""Native intent journal prevents replay and rejects failed game receipts."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from native_game import NativeGame
from native_rpc import NativeRpcError


class NativeGameTests(unittest.TestCase):
    def setUp(self):
        identity = patch("native_game.process_creation_time", return_value=123456)
        self.identity = identity.start()
        self.addCleanup(identity.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.game = NativeGame(123, Path(self.temp.name))

    @patch("native_game.request")
    def test_receipt_is_not_dispatched_again(self, rpc):
        rpc.return_value = {"ok": True, "result": {"ok": True, "queue_accepted": True}}
        self.game.move("AUS", 1001, 688, 3684, request_id="move1")
        self.game.move("AUS", 1001, 688, 3684, request_id="move1")
        self.assertEqual(rpc.call_count, 1)
        with self.assertRaises(ValueError):
            self.game.move("AUS", 1001, 688, 6708, request_id="move1")

    @patch("native_game.request")
    def test_unknown_keeps_original_request_id(self, rpc):
        rpc.side_effect = NativeRpcError("timeout", request_id="move1", outcome="unknown")
        with self.assertRaises(NativeRpcError):
            self.game.move("AUS", 1001, 688, 3684, request_id="move1")
        record = json.loads((Path(self.temp.name) / "move1.json").read_text())
        self.assertEqual(record["status"], "pending")
        rpc.side_effect = None
        rpc.return_value = {"ok": True, "result": {"ok": True}}
        self.game.move("AUS", 1001, 688, 3684, request_id="move1")
        self.assertEqual(rpc.call_args.kwargs["request_id"], "move1")

    @patch("native_game.request")
    def test_nested_failure_is_not_success(self, rpc):
        rpc.return_value = {"ok": True, "result": {"ok": False, "error": "not owned"}}
        with self.assertRaises(RuntimeError):
            self.game.inspect("AUS", 1001, request_id="inspect1")
        with self.assertRaises(RuntimeError):
            self.game.inspect("AUS", 1001, request_id="inspect1")
        self.assertEqual(rpc.call_count, 1)

    @patch("native_game.request")
    def test_no_raw_pointers_or_protocol_injection(self, rpc):
        for country, unit in (("AUS\npost", 1001), ("AUS", True), ("AUS", -1)):
            with self.assertRaises(ValueError):
                self.game.inspect(country, unit, request_id="bad")
        rpc.assert_not_called()

    @patch("native_game.request")
    def test_restarted_process_never_receives_pending_action(self, rpc):
        self.identity.return_value = 999999
        with self.assertRaises(RuntimeError):
            self.game.move("AUS", 1001, 688, 3684, request_id="move1")
        rpc.assert_not_called()

    @patch("native_game.request")
    def test_military_schemas_reject_invalid_native_arguments(self, rpc):
        with self.assertRaises(ValueError):
            self.game.declare_war("AUS", "ITA", "0x123\npost", request_id="bad")
        with self.assertRaises(ValueError):
            self.game.navy_mission("GER", 1517, 0, 8, request_id="bad")
        with self.assertRaises(ValueError):
            self.game.navy_regions("GER", 1517, [], [9, 9], request_id="bad")
        with self.assertRaises(ValueError):
            self.game.air_mission("AUS", 93, 0, 3, True, request_id="bad")
        rpc.assert_not_called()

    @patch("native_game.request")
    def test_military_methods_use_semantic_ids(self, rpc):
        rpc.return_value = {"ok": True, "result": {"ok": True}}
        self.game.navy_regions("GER", 1517, [], [9], request_id="region")
        self.assertEqual(rpc.call_args.args[1:3], ("native_air_navy_v2", "navy_regions_post GER 1517 - 9"))
        self.game.declare_war("GER", "FRA", "0x10e300001269", request_id="war", construct_only=True)
        self.assertEqual(rpc.call_args.args[1:3], ("native_diplomacy_v2", "build_declare GER FRA 0x10e300001269"))


if __name__ == "__main__":
    unittest.main()
