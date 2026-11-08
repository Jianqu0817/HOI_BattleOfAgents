"""Check multicountry admission and equal rotation before touching the game."""
import unittest

from live_compat import parse_chinese_date
from m0_probe import countries_from, game_hours, order_for


class MulticountryTests(unittest.TestCase):
    def test_three_to_five_countries_get_each_execution_position(self):
        all_tags = ["GER", "ITA", "JAP", "ENG", "SOV"]
        for size in (3, 4, 5):
            tags = countries_from({"countries": all_tags[:size]})
            rounds = [order_for(tags, turn) for turn in range(size)]
            for position in range(size):
                self.assertEqual({row[position] for row in rounds}, set(tags))
            self.assertEqual(tags, all_tags[:size])

    def test_rejects_duplicate_or_invalid_country_configuration(self):
        for tags in (["GER", "ITA"], ["GER"] * 3, ["GER", "ITA", "tag GER"], list("ABCDEF")):
            with self.assertRaises(ValueError):
                countries_from({"countries": tags})

    def test_chinese_clock_supports_full_width_punctuation(self):
        value = parse_chinese_date("11 ： 00 ， 28 2 月 ， 1936")
        self.assertEqual(value["hoi4"], "1936.2.28.11")
        self.assertIsNone(parse_chinese_date("11：00，30 2月，1936"))
        self.assertIsNone(parse_chinese_date("11：00，28 2月，193穆"))

    def test_engine_receipt_dates_cross_month_boundaries(self):
        before = game_hours("1936.02.28.11")
        self.assertEqual(before + 24, game_hours("1936.03.01.11"))
        self.assertEqual(game_hours("1936.12.31.11") + 24, game_hours("1937.01.01.11"))
        with self.assertRaises(ValueError):
            game_hours("1936.02.29.11")


if __name__ == "__main__":
    unittest.main()
