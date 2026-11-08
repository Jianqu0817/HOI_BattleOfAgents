"""Chinese research-panel observations used by ordinary player actions."""
from types import SimpleNamespace
import unittest

from live_compat import chinese_research_slots, completion_notice_title, army_card_count


def line(text, y):
    return SimpleNamespace(text=text, box=[30, y, 160, 18])


class ChineseResearchTests(unittest.TestCase):
    def test_army_fraction_is_not_read_as_hundreds_of_divisions(self):
        self.assertEqual(army_card_count("2 ／ 72"), (2, 72))
        self.assertEqual(army_card_count("0 10 / 24"), (10, 24))
        for text in ("272", "202", "6/5", "73/72", "2/72 3/72"):
            self.assertIsNone(army_card_count(text))

    def test_only_completion_notifications_can_be_acknowledged(self):
        self.assertTrue(completion_notice_title("国 策 完 成"))
        self.assertTrue(completion_notice_title("研究完成"))
        for title in ("是否切换国策", "国策完成后是否宣战", "是否解散部队", "外交事件"):
            self.assertFalse(completion_notice_title(title))

    def test_busy_days_and_fourth_idle_slot(self):
        observed = chinese_research_slots([
            line("研究速度 +8%", 278), line("基 础 机 床", 318), line("123 日", 355),
            line("选择一项科技", 418), line("选择 一项 科技", 518), line("选择一项科技", 618)])
        self.assertEqual([row["index"] for row in observed], [1, 2, 3, 4])
        self.assertEqual(observed[0]["name"], "基础机床")
        self.assertEqual(observed[0]["days"], 123)
        self.assertEqual([row["state"] for row in observed], ["busy", "idle", "idle", "idle"])

    def test_unreadable_days_do_not_invent_busy_slot(self):
        self.assertEqual(chinese_research_slots([line("基础机床", 318), line("? 日", 355)]), [])


if __name__ == "__main__":
    unittest.main()
