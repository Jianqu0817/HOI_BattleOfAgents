import copy
import unittest
from communication_annotations import project_commitment_quotes, project_reply_routes
from showrunner import check_replies


class CommunicationAnnotationsTests(unittest.TestCase):
    def test_undelivered_drafts_do_not_reach_an_unaddressed_country(self):
        value = {"country": "ITA", "replies": [
            {"to": "AUS", "message": "保留原话"}, {"to": "SWI", "message": "未送达原话"},
            {"to": "FRA", "message": "第二封原话"}, {"to": "AUS", "message": "重复原话"}]}
        raw = copy.deepcopy(value)
        projected, rejected = project_reply_routes(value, "ITA", [{"country": "AUS", "to": "ITA"}, {"country": "FRA", "to": "ITA"}])
        self.assertEqual(projected["replies"], [raw["replies"][0], raw["replies"][2]])
        self.assertEqual([r["to"] for r in rejected], ["SWI", "AUS"])
        self.assertEqual([r["reply"]["message"] for r in rejected], ["未送达原话", "重复原话"])
        self.assertEqual(value, raw)

    def test_past_reference_is_not_silently_extended_to_a_new_promise(self):
        value = {"country": "AUS", "message": "确认维持至第12回合不变", "commitments": [
            {"text": "确认维持至第12回合不变", "to": None, "due_turn": 12, "condition": "对等"}]}
        projected, changes = project_commitment_quotes(value, turn_id=13)
        self.assertEqual(projected["message"], value["message"])
        self.assertEqual(projected["commitments"], [])
        self.assertEqual(changes[0]["kind"], "omit_annotation_deadline_out_of_window")
        self.assertEqual(changes[0]["original"]["due_turn"], 12)

    def test_foreign_promise_is_not_attributed_to_the_reply_author(self):
        value = {"country": "SWI", "replies": [{"to": "ITA", "message": "贵方不借道，我方记录。",
                 "commitments": [{"text": "我方不寻求借道", "to": "ITA", "due_turn": 12, "condition": "保持中立"}]}]}
        original = copy.deepcopy(value)
        projected, changes = project_commitment_quotes(value)
        self.assertEqual(projected["replies"][0]["message"], value["replies"][0]["message"])
        self.assertEqual(projected["replies"][0]["commitments"], [])
        self.assertEqual(changes[0]["original"], value["replies"][0]["commitments"][0])
        self.assertEqual(value, original)
        check_replies(projected, "SWI", [{"country": "ITA", "to": "SWI"}], ["SWI", "ITA", "GER"], 7)

    def test_literal_projection_keeps_conditions_and_all_speech(self):
        promises = [{"text": str(i) + "。", "to": "ITA", "due_turn": 8, "condition": "条件"} for i in range(4)]
        value = {"country": "SWI", "message": "0，1，2，3。", "commitments": promises}
        result, changes = project_commitment_quotes(value)
        self.assertEqual(result["message"], value["message"])
        self.assertEqual(len(result["commitments"]), 3)
        self.assertTrue(all(p["condition"] == "条件" and p["due_turn"] == 8 for p in result["commitments"]))
        self.assertTrue(any(c["kind"] == "omit_annotation_over_limit" for c in changes))

    def test_private_promise_to_a_third_country_is_dropped_not_attributed(self):
        from showrunner import check_message
        value = {"country": "AUS", "to": "GER", "message": "我方承诺不越境，并希望法方同样克制。",
                 "commitments": [
                     {"text": "我方承诺不越境", "to": "GER", "due_turn": 13, "condition": "对等"},
                     {"text": "并希望法方同样克制", "to": "FRA", "due_turn": 13, "condition": "对等"}]}
        original = copy.deepcopy(value)
        projected, changes = project_commitment_quotes(value)
        self.assertEqual(len(projected["commitments"]), 1)
        self.assertEqual(projected["commitments"][0]["to"], "GER")
        self.assertEqual([c["kind"] for c in changes], ["omit_annotation_misaddressed_recipient"])
        self.assertEqual(changes[0]["original"]["to"], "FRA")
        self.assertEqual(value, original)
        check_message(projected, "AUS", ["AUS", "GER", "FRA"], 7, private=True)
