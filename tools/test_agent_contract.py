"""Exercise cross-country, stale-snapshot and repeated-action boundaries."""
import copy
import unittest

from agent_contract import validate_request, country_fingerprint, construction_queue, equipment_variant_name, verify_action


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.observed = {"match_id": "test", "turn_id": 2, "country": "GER", "date": "1936.3.15.11",
                         "observation_id": "snapshot", "research": {"slot_count": 4},
                         "candidates": {"research": [{"id": "construction1"}], "focus": [],
                                        "construction_buildings": ["industrial_complex"]},
                         "military_production": [{"id": 1, "requested_factories": 9}],
                         "owned_states": [{"id": 64}]}
        self.request = {key: self.observed[key] for key in ("match_id", "turn_id", "country", "date", "observation_id")}
        self.request["actions"] = [{"id": "2-GER-1", "type": "production.assign", "args": {"line_id": 1, "factories": 8}}]

    def test_foreign_country_and_stale_snapshot_are_rejected(self):
        for key, value in (("country", "ITA"), ("turn_id", 3), ("turn_id", True), ("observation_id", "old"), ("date", "1936.3.16.11")):
            request = copy.deepcopy(self.request)
            request[key] = value
            with self.assertRaises(ValueError):
                validate_request(request, self.observed)

    def test_raw_commands_extra_arguments_and_bool_numbers_are_rejected(self):
        for action in ({"id": "x", "type": "console", "args": {"command": "research all"}},
                       {"id": "x", "type": "production.assign", "args": {"line_id": 1, "factories": 8, "force": True}},
                       {"id": "x", "type": "production.assign", "args": {"line_id": True, "factories": 8}},
                       {"id": "x", "type": "construction.queue", "args": {"state_id": 1, "building_id": "industrial_complex", "count": 1}}):
            request = copy.deepcopy(self.request)
            request["actions"] = [action]
            with self.assertRaises(ValueError):
                validate_request(request, self.observed)

    def test_repeated_target_with_distinct_ids_does_not_double_apply(self):
        request = copy.deepcopy(self.request)
        second = copy.deepcopy(request["actions"][0])
        second["id"] = "another-id"
        request["actions"].append(second)
        with self.assertRaises(ValueError):
            validate_request(request, self.observed)
        self.assertEqual(len(validate_request(self.request, self.observed)), 1)

    def test_fingerprint_ignores_log_flags_but_detects_gameplay_changes(self):
        state = {"countries": {"GER": {"flags": {"receipt": True}, "technology": {"slots": {}}}},
                 "states": {"64": {"owner": "GER", "buildings": {}}}}
        previous = country_fingerprint(state, "GER")
        state["countries"]["GER"]["flags"]["receipt"] = False
        self.assertEqual(previous, country_fingerprint(state, "GER"))
        state["countries"]["GER"]["technology"]["slots"]["construction1"] = {}
        self.assertNotEqual(previous, country_fingerprint(state, "GER"))

    def test_queue_comes_from_general_lines_not_country_report(self):
        country = {"country_reports": {"construction": {"civilian_factory": 9}}, "production": {}}
        self.assertEqual(construction_queue(country), [])
        entry = {"building": {"template": "industrial_complex", "location": 64}, "amount": 1}
        country["production"]["general_lines"] = {"building": entry}
        self.assertEqual(construction_queue(country), [entry])

    def test_variant_name_uses_exact_id_among_repeated_equipment_types(self):
        state = {"equipments": {"infantry_equipment_1": [
            {"id": {"id": 1326, "type": 70}, "name": "Kar98k"},
            {"id": {"id": 2073, "type": 70}, "name": "九九式步枪"}]}}
        self.assertEqual(equipment_variant_name(state, 2073), "九九式步枪")
        self.assertIsNone(equipment_variant_name(state, 9999))
        state["equipments"]["infantry_equipment_1"][0]["name"] = ""
        self.assertEqual(equipment_variant_name(state, 1326, "GER", {"GER_infantry_equipment_1": "Kar98k"}), "Kar98k")
        self.assertEqual(equipment_variant_name(state, 1326, "GER", {"GER_infantry_equipment_1": "Kar 98k式步枪", "GER_infantry_equipment_1_short": "Kar 98k"}, prefer_short=True), "Kar 98k")

    def test_freed_factory_can_fill_existing_request_without_changing_other_targets(self):
        line = lambda identity, requested, active: {"id": {"id": identity}, "requested_factories": requested,
                                                   "active_factories": active, "equipment_variant_index": {"id": identity + 100}}
        before = {"date": "1936.3.15.11", "countries": {"JAP": {"production": {"military_lines": [line(106, 7, 7), line(108, 4, 2)]}}}}
        after = copy.deepcopy(before)
        after["countries"]["JAP"]["production"]["military_lines"] = [line(106, 6, 6), line(108, 4, 3)]
        action = {"type": "production.assign", "args": {"line_id": 106, "factories": 6}}
        result = verify_action(action, before, after, "JAP")
        self.assertTrue(result["verified"])
        self.assertEqual(len(result["observations"]["active_factory_changes"]), 2)
        after["countries"]["JAP"]["production"]["military_lines"][1]["requested_factories"] = 5
        self.assertFalse(verify_action(action, before, after, "JAP")["verified"])


if __name__ == "__main__":
    unittest.main()
