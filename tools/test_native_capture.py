"""Ownership layout and capture recovery tests without a running game."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from native_capture import BUILD, GAME_SHA256, _save_date, capture, read_world, read_trajectories


class Memory:
    def __init__(self, _pid=123):
        self.bytes = {}

    def put(self, address, fmt, *values):
        for offset, byte in enumerate(struct.pack("<" + fmt, *values)):
            self.bytes[address + offset] = byte

    def read(self, address, size):
        return bytes(self.bytes.get(address + offset, 0) for offset in range(size))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class FakeGame:
    pid, creation_time = 123, 7

    def __init__(self):
        self.current, self.fail = "AUS", None
        self.switches = []

    def status(self, **_):
        return {"synthetic": False, "build": BUILD, "sha256": GAME_SHA256,
                "image_base": "0x10000000"}

    def country_status(self, tag, **_):
        return {"state": {"tag": tag, "country_index": {"AUS": 1, "ITA": 2}[tag],
                           "played_index": {"AUS": 1, "ITA": 2}[self.current],
                           "paused": True, "ai_enabled": False,
                           "date_raw": 60762795, "date": "1936-05-24T03:00:00"}}

    def control(self, expected, target, **_):
        assert self.current == expected
        self.current = target
        self.switches.append((expected, target))
        return {"ok": True}

    def units(self, tag, **_):
        return {"country": tag, "units": []}

    air = units

    def navy(self, tag, **_):
        if self.fail == tag:
            raise RuntimeError("injected read failure")
        return self.units(tag)

    def war_goals(self, tag, **_):
        return {"country": tag, "goals": []}

    def war_status(self, tag, other, **_):
        return {"at_war": True, "actor_index": {"AUS": 1, "ITA": 2}[tag],
                "recipient_index": {"AUS": 1, "ITA": 2}[other], "goals": []}


class NativeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.game = FakeGame()
        self.memory = memory = Memory()
        base, state, idler, table = 0x10000000, 0x20000000, 0x21000000, 0x22000000
        memory.put(base + 0x33DD6B0, "Q", state)
        memory.put(base + 0x33DDB10, "Q", idler)
        memory.put(state + 0x468, "i", 60762795)
        memory.put(idler + 0x6C1, "B", 1)
        memory.put(state + 0x310, "Qii", table, 3, 3)
        memory.put(table, "QQQ", 0, 0x23000000, 0x24000000)
        for index, country, data in ((1, 0x23000000, 0x25000000), (2, 0x24000000, 0x26000000)):
            memory.put(country, "Qi", base + 0x2864730, index)
            memory.put(country + 0x460, "Qii", data, 1, 1)
            memory.put(data, "Q", 0x27000000)
        # An occupied state may appear in another country's state list; its own
        # owner/controller fields are authoritative, and shared pointers dedupe.
        memory.put(0x27000000, "Q", base + 0x29B4D58)
        memory.put(0x27000018, "Qii", 0x28000000, 1, 1)
        memory.put(0x27000058, "i", 153)
        memory.put(0x270000C8, "ii", 1, 2)
        memory.put(0x28000000, "Q", 0x29000000)
        memory.put(0x29000000, "Q", base + 0x29EE358)
        memory.put(0x290000A4, "i", 673)
        memory.put(0x290000C0, "Q", 0x27000000)
        memory.put(0x29000188, "i", 44)
        identity = patch("native_capture.process_creation_time", return_value=7)
        identity.start()
        self.addCleanup(identity.stop)
        from native_events import unavailable
        events = patch("native_events.read_pending", return_value=unavailable("fake capture"))
        events.start()
        self.addCleanup(events.stop)

    def test_read_trajectories_resolves_owned_units_without_player_switch(self):
        memory, country, array, army, path = self.memory, 0x23000000, 0x30000000, 0x31000000, 0x32000000
        memory.put(country + 0x290, "Qii", array, 1, 1)
        memory.put(array, "Q", army)
        memory.put(army, "Q", 0x10000000 + 0x29D6D98)
        memory.put(army + 0x18, "Q", (7 << 32) | 51)
        memory.put(army + 0x1F0, "Q", 0x29000000)
        memory.put(army + 0x200, "Qii", path, 1, 1)
        memory.put(path, "i", 9398)
        memory.put(country + 0xF88, "Q", 0x33000000)
        memory.put(0x33000000, "Q", 0x10000000 + 0x29DE5E8)
        controls = {tag: self.game.country_status(tag)["state"] for tag in ("AUS", "ITA")}
        plans = [{"tag": "AUS", "actions": [{"id": "move7", "kind": "division.move", "unit_id": 7},
                                                {"id": "missing", "kind": "division.move", "unit_id": 8}]}]
        result = read_trajectories(self.game, self.game.status(), controls, plans, reader_factory=lambda _: memory)
        self.assertEqual(result["actions"]["move7"]["observed"]["path"], [9398])
        self.assertEqual(result["actions"]["move7"]["observed"]["province"], 673)
        self.assertFalse(result["actions"]["missing"]["available"])
        self.assertEqual(self.game.switches, [])

    def world(self):
        return read_world(self.game, self.game.status(),
                          {tag: self.game.country_status(tag)["state"] for tag in ("AUS", "ITA")},
                          reader_factory=lambda _: self.memory)

    def test_occupied_state_owner_province_count_and_unknown_controller(self):
        state = self.world()
        self.assertEqual(state["states"], {"153": {"owner": "AUS", "controller": "ITA"}})
        self.assertEqual(state["provinces"], {"673": {"controller": "IDX_44"}})
        self.assertEqual(state["public_wars"], [])

    def test_bad_array_count_rejected_before_pointer_reads(self):
        self.memory.put(0x27000024, "i", 20001)
        with self.assertRaisesRegex(RuntimeError, "invalid native array"):
            self.world()

    def test_unpaused_world_rejected(self):
        self.memory.put(0x210006C1, "B", 0)
        with self.assertRaisesRegex(RuntimeError, "world changed or unpaused"):
            self.world()

    def test_clock_change_during_world_read_discards_snapshot(self):
        original = self.memory.read
        date_reads = 0

        def advancing(address, size):
            nonlocal date_reads
            if address == 0x20000468:
                date_reads += 1
                if date_reads == 2:
                    return struct.pack("<i", 60762796)
            return original(address, size)

        with patch.object(self.memory, "read", side_effect=advancing):
            with self.assertRaisesRegex(RuntimeError, "world changed or unpaused"):
                self.world()

    def test_restarted_process_rejected(self):
        with patch("native_capture.process_creation_time", return_value=8):
            with self.assertRaisesRegex(RuntimeError, "restarted"):
                self.world()

    def test_wrong_build_rejected(self):
        status = self.game.status()
        status["sha256"] = "changed"
        with self.assertRaisesRegex(RuntimeError, "pinned native game build"):
            read_world(self.game, status, {})

    def test_capture_wars_and_manifest_and_original_player(self):
        world = self.world()
        with tempfile.TemporaryDirectory() as directory, patch("native_capture.read_world", return_value=world):
            result = capture(self.game, ["AUS", "ITA"], directory, "match", 1, map_context={})
            self.assertEqual(self.game.current, "AUS")
            self.assertEqual(self.game.switches, [("AUS", "ITA"), ("ITA", "AUS")])
            self.assertEqual(result["state"]["public_wars"], [{"first": "AUS", "second": "ITA"}])
            self.assertEqual(result["observations"]["AUS"]["army"]["wars"][0]["enemy"], "ITA")
            self.assertEqual(result["observations"]["AUS"]["date"], "1936.5.24.4")
            manifest = json.loads((Path(directory) / "capture.json").read_text())
            self.assertEqual(manifest["date_raw"], 60762795)
            self.assertIn("observations.json", manifest["files"])

    def test_read_failure_restores_original_player(self):
        self.game.fail = "ITA"
        with tempfile.TemporaryDirectory() as directory, patch("native_capture.read_world", return_value=self.world()):
            with self.assertRaisesRegex(RuntimeError, "injected read failure"):
                capture(self.game, ["AUS", "ITA"], directory, "match", 1, map_context={})
        self.assertEqual(self.game.current, "AUS")

    def test_save_date_retains_one_based_midnight_hour(self):
        self.assertEqual(_save_date({"date": "1936-05-24T00:00:00"}), "1936.5.24.1")
        self.assertEqual(_save_date({"date": "1936-05-24T23:00:00"}), "1936.5.24.24")


if __name__ == "__main__":
    unittest.main()
