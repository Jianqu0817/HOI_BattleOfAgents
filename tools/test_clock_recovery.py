"""Recover exact elapsed time without replaying a whole day or choosing event effects."""
import unittest

from recover_clock import clock_position
from notifications import passive_option


class ClockRecoveryTests(unittest.TestCase):
    def test_unrecorded_day_is_counted_before_resuming(self):
        self.assertEqual(clock_position("1936.3.31.11", "1936.4.6.11", 360, 120), (144, 216))
        self.assertEqual(clock_position("1936.3.31.11", "1936.4.6.1", 360, 120), (134, 226))

    def test_old_or_overshot_clock_is_rejected(self):
        for current in ("1936.4.4.11", "1936.4.16.11"):
            with self.assertRaises(ValueError):
                clock_position("1936.3.31.11", current, 360, 120)

    def test_event_option_with_an_effect_is_not_automatic(self):
        self.assertTrue(passive_option({"name": "news.63.a", "trigger": {"NOT": {"original_tag": "SPR"}}}))
        self.assertFalse(passive_option({"name": "event.a", "add_political_power": 100}))
        self.assertFalse(passive_option({"name": "event.a", "hidden_effect": {"set_country_flag": "x"}}))
