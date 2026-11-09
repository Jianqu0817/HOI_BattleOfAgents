import copy
import unittest
from episode import normalize_commentary_quotes


class CommentaryQuoteTests(unittest.TestCase):
    def test_inline_analysis_verifies_explicit_ids_and_ranges(self):
        episode = {"events": [{"id": f"T6-E00{i}", "text": "已记录"} for i in range(1, 4)]}
        value = {"title": "本轮", "facts": [], "analysis": ["现场有三条记录（T6-E001到T6-E003）。"],
                 "jokes": [], "highlights": []}
        fixed, changes = normalize_commentary_quotes(value, episode, explain_only=True)
        self.assertEqual(fixed["analysis"], [{"text": "现场有三条记录。", "evidence_ids": ["T6-E001","T6-E002","T6-E003"]}])
        self.assertEqual(changes[0]["kind"], "inline_analysis_citations")
        episode["events"].pop(1)
        with self.assertRaisesRegex(ValueError, "evidence"):
            normalize_commentary_quotes(value, episode, explain_only=True)

    def test_terminal_period_repair_is_exact_and_raw_response_is_preserved(self):
        episode = {"events": [{"id": "e1", "text": "我方愿互不越界，并交换说明。"}]}
        value = {"title": "回顾", "facts": [{"evidence_id": "e1", "quote": "我方愿互不越界。"}],
                 "analysis": [], "jokes": [], "highlights": []}
        original = copy.deepcopy(value)
        fixed, changes = normalize_commentary_quotes(value, episode)
        self.assertEqual(fixed["facts"][0]["quote"], "我方愿互不越界")
        self.assertEqual(value, original)
        self.assertEqual(changes[0]["kind"], "trim_added_terminal_period")

    def test_paraphrase_is_omitted_and_invalid_source_is_not_hidden(self):
        episode = {"events": [{"id": "e1", "text": "这是公开声明。"}]}
        value = {"title": "回顾", "facts": [{"evidence_id": "e1", "quote": "已经结盟"}],
                 "analysis": [], "jokes": [], "highlights": []}
        fixed, changes = normalize_commentary_quotes(value, episode)
        self.assertEqual(fixed["facts"], [])
        self.assertEqual(changes[0]["kind"], "omit_nonverbatim_fact")
        value["facts"][0]["evidence_id"] = "missing"
        with self.assertRaises(ValueError):
            normalize_commentary_quotes(value, episode)

    def test_oversized_sections_are_capped_with_a_receipt_and_raw_output_is_preserved(self):
        episode = {"events": [{"id": f"e{i}", "text": f"证据{i}。"} for i in range(9)]}
        value = {"title": "回顾",
                 "facts": [{"evidence_id": f"e{i}", "quote": f"证据{i}"} for i in range(9)],
                 "analysis": [], "jokes": [], "highlights": []}
        original = copy.deepcopy(value)
        fixed, changes = normalize_commentary_quotes(value, episode)
        self.assertEqual(len(fixed["facts"]), 8)
        cap = [row for row in changes if row["kind"] == "cap_section"]
        self.assertEqual(cap, [{"kind": "cap_section", "section": "facts", "kept": 8, "dropped": 1}])
        self.assertEqual(value, original)
