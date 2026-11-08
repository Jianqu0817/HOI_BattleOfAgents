import unittest
from scoreboard import score, validate_rules


class ScoreboardTests(unittest.TestCase):
    def setUp(self):
        self.index = {"provinces": {"1": {"kind": "land", "state": 10}, "2": {"kind": "land", "state": 10}, "3": {"kind": "land", "state": 11}, "4": {"kind": "sea"}}}
        self.first = {"date": "1936.1.1.0", "states": {"10": {"owner": "ITA"}, "11": {"owner": "ETH"}}, "provinces": {}}
        self.last = {**self.first, "date": "1936.1.2.0", "provinces": {"1": {"controller": "ETH"}}}

    def test_single_province_occupation_overrides_state_owner(self):
        result = score(self.first, self.last, ["ITA", "ETH"], self.index, {"mode": "territory_delta"}, final=True)
        self.assertEqual([row["points"] for row in result["rows"]], [-1, 1])
        self.assertEqual(result["winners"], ["ETH"])
        self.assertEqual(result["rows"][0]["owned_states"], 1)

    def test_objectives_tie_and_intermediate_results_are_not_final_winners(self):
        rules = {"mode": "objectives", "province_ids": [1, 2]}
        result = score(self.first, self.last, ["ITA", "ETH"], self.index, rules, final=True)
        self.assertTrue(result["tied"])
        self.assertEqual(score(self.first, self.last, ["ITA", "ETH"], self.index, rules)["winners"], [])

    def test_exhibition_has_no_winner_and_unknown_map_does_not_invent_zero_land(self):
        result = score(self.first, self.last, ["ITA", "ETH"], {}, final=True)
        self.assertEqual(result["winners"], [])
        self.assertIsNone(result["rows"][0]["controlled_land"])

    def test_unknown_objective_or_missing_controller_snapshot_is_rejected(self):
        with self.assertRaises(ValueError):
            score(self.first, self.last, ["ITA"], self.index, {"mode": "objectives", "province_ids": [4]})
        for rules in ({"mode": "objectives", "province_ids": [True]}, {"mode": "objectives", "province_ids": [1, 1]}, {"mode": "surprise"}):
            with self.assertRaises(ValueError):
                validate_rules(rules)
