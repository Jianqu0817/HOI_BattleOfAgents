import unittest
from unittest.mock import patch

from native_capture import BUILD, GAME_SHA256
from native_events import read_pending, select_choice, unavailable
from test_native_capture import Memory


class NativeEventsTests(unittest.TestCase):
    def test_unknown_provider_does_not_claim_empty_queue(self):
        result = unavailable()
        self.assertIsNone(result["pending"])
        self.assertFalse(result["capture_complete"])
        with self.assertRaises(NotImplementedError):
            select_choice("decision", 0)

    def test_registered_event_has_country_options_and_explicit_coverage(self):
        class Game:
            pid, creation_time = 123, 7
        memory = Memory()
        base, state, idler, manager = 0x10000000, 0x20000000, 0x21000000, 0x22000000
        array, window, event, options, option = 0x23000000, 0x24000000, 0x25000000, 0x26000000, 0x27000000
        memory.put(base + 0x33DD6B0, "Q", state)
        memory.put(base + 0x33DDB10, "Q", idler)
        memory.put(state + 0x468, "i", 60764595)
        memory.put(idler + 0x6C1, "B", 1)
        memory.put(idler + 0x4F8, "Q", manager)
        memory.put(manager, "Q", base + 0x29C7580)
        memory.put(manager + 0xD48, "Qii", array, 1, 1)
        memory.put(array, "Q", window + 0x28)
        memory.put(window, "Q", base + 0x2A20830)
        memory.put(window + 0x28, "Q", base + 0x2A20858)
        memory.put(window + 0x60, "i", 11)
        memory.put(window + 0x68, "Q", event)
        memory.put(event, "QQ", base + 0x2A16008, 12345)
        for address, value in ((event + 0x20, b"test.1"), (event + 0x40, b"aux"), (option + 0x178, b"test.1.a")):
            memory.put(address, "16sQQ", value, len(value), 15)
        memory.put(event + 0x368, "Qii", options, 1, 1)
        memory.put(options, "Q", option)
        memory.put(option, "Q", base + 0x2A15F68)
        memory.put(option + 0x1F0, "i", 0)
        main = {"synthetic": False, "build": BUILD, "sha256": GAME_SHA256, "image_base": hex(base)}
        controls = {"AUS": {"paused": True, "date_raw": 60764595, "country_index": 11}}
        with patch("native_events.process_creation_time", return_value=7):
            result = read_pending(Game(), main, controls, reader_factory=lambda _: memory)
        self.assertFalse(result["capture_complete"])
        self.assertEqual(result["availability"], "partial")
        self.assertEqual(result["pending"][0]["country"], "AUS")
        self.assertEqual(result["pending"][0]["options"][0]["native_key"], "test.1.a")
        self.assertFalse(result["pending"][0]["options"][0]["actionable"])
        self.assertEqual(result["choice_api"]["availability"], "unavailable")


if __name__ == "__main__":
    unittest.main()
