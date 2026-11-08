"""Check multicountry admission and equal rotation before touching the game."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from live_compat import ai_receipt_from_reads, close_profile_panel, parse_chinese_date, read_profile_title, reread_ai_receipt
from m0_probe import ai_receipt_state, countries_from, game_hours, order_for


class MulticountryTests(unittest.TestCase):
    def test_only_exact_air_wing_detail_title_in_local_modal_region_preempts_regular_title(self):
        session, fallback = MagicMock(), MagicMock(return_value=(None, None))
        image = object()
        session.ocr.recognize.return_value = SimpleNamespace(lines=[SimpleNamespace(text="联 队 详 情")])
        self.assertEqual(read_profile_title(session, fallback, img=image), ("air_wing_details", "联 队 详 情"))
        session.ocr.recognize.assert_called_once_with(image, region=[360, 120, 560, 45], scale=2)
        fallback.assert_not_called()
        for title in ("未知联队详情", "联队", "详情", "外交"):
            session.ocr.recognize.return_value = SimpleNamespace(lines=[SimpleNamespace(text=title)])
            self.assertEqual(read_profile_title(session, fallback, img=image), (None, None))
        fallback.assert_called_with(session, img=image, attempts=2)
        session.input.key.assert_not_called()

    def test_diplomacy_close_uses_escape_for_modal_and_panel_without_blind_click(self):
        session, fallback, pause = MagicMock(), MagicMock(), MagicMock(return_value=(False, None))
        with patch("time.sleep"):
            self.assertEqual(close_profile_panel(session, "diplomacy", fallback, pause), "diplomacy")
            self.assertEqual(close_profile_panel(session, "diplomacy", fallback, pause), "diplomacy")
        self.assertEqual([call.args for call in session.input.key.call_args_list], [("esc",), ("esc",)])
        session.input.click.assert_not_called()
        fallback.assert_not_called()
        self.assertEqual(close_profile_panel(session, None, fallback, pause), fallback.return_value)
        fallback.assert_called_once_with(session)

    def test_ai_receipt_rereads_only_latest_row_and_uses_fractional_scale(self):
        ocr = MagicMock()
        def recognize(image, **options):
            self.assertEqual(options["region"][0], 18)
            self.assertGreaterEqual(options["region"][1], 475)
            self.assertLessEqual(options["region"][1] + options["region"][3], 503)
            text = "Disabled Al for: SWI" if options["scale"] == 3.5 else "Disabled Al for: SVM"
            return SimpleNamespace(lines=[SimpleNamespace(text=text)])
        ocr.recognize.side_effect = recognize
        line = SimpleNamespace(text="Disabled Al for: SvN'1", box=[4, 309, 95, 9])
        tags = {"GER", "FRA", "ITA", "AUS", "SWI"}
        self.assertEqual(reread_ai_receipt(ocr, object(), line, (19, 176), tags), "Disabled AI for: SWI")
        self.assertEqual(ocr.recognize.call_count, 16)

    def test_ai_receipt_reread_conflicting_valid_countries_fail_closed(self):
        ocr = MagicMock()
        ocr.recognize.side_effect = [SimpleNamespace(lines=[SimpleNamespace(text="Disabled AI for: " + tag)])
                                     for tag in ["SWI", "GER"] * 8]
        line = SimpleNamespace(text="Disabled Al for: SVM", box=[4, 309, 95, 9])
        self.assertIsNone(reread_ai_receipt(ocr, object(), line, (19, 176), {"GER", "SWI"}))

    def test_ai_country_consensus_uses_all_configured_tags_and_rejects_conflicts(self):
        tags = {"GER", "FRA", "ITA", "AUS", "SWI"}
        self.assertEqual(ai_receipt_from_reads(["Disabled Al for: SVM", "Disabled Al for: SWI"], tags), "Disabled AI for: SWI")
        self.assertIsNone(ai_receipt_from_reads(["Disabled Al for: SVM"], tags))
        self.assertIsNone(ai_receipt_from_reads(["Disabled AI for: GER", "Disabled AI for: FRA"], tags))
        self.assertIsNone(ai_receipt_from_reads(["Enabled AI for: SWI", "Disabled AI for: SWI"], tags))

    def test_ai_receipt_reads_last_complete_state_for_exact_country(self):
        self.assertEqual(ai_receipt_state(["Enabled Al for: GER", "Disabled Al for: GER"], "GER"), "disabled")
        self.assertEqual(ai_receipt_state(["Disabled AI for: GER", "Enabled AI for: GER"], "GER"), "enabled")
        for lines in (["Disabled AI for: GERX"], ["Disabled AI for: ITA"], ["Disabled AI fo 匚 GER"], ["not Disabled AI for: GER"]):
            self.assertIsNone(ai_receipt_state(lines, "GER"))
        self.assertIsNone(ai_receipt_state(["Disabled AI for: GER", "Enabled AI for: GERX"], "GER"))
        self.assertIsNone(ai_receipt_state(["Disabled AI for: GER", "Disabled AI for: SWI"], "GER"))

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
