"""Naval/air action routing, separate baselines and recovery stay in the ordinary turn chain."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agent_contract import country_fingerprint, validate_request, verify_action
from model_trial import options
from naval_air_actions import naval_air_fingerprint
from recover_clock import retained_orders
from run_turn import check_naval_air_baseline, resume_checkpoint


def fixture():
    line = {"id": {"id": 1}, "priority": 0, "requested_factories": 2, "equipment_variant_index": {"id": 10}}
    state = {"date": "1936.5.7.11", "states": {}, "provinces": {}, "countries": {
        tag: {"focus": {}, "technology": {}, "politics": {}, "production": {"military_lines": [copy.deepcopy(line)]}, "units": {}}
        for tag in ("GER", "ITA", "SWI")}}
    state["strategic_air"] = {"air_base": {"id": {"id": 3}, "state": 1}, "GER": {"air_wing_pool": {
        "id": {"id": 4}, "air_base": {"id": 3}, "definition": "fighter", "air_wings": {
            "id": {"id": 5}, "tag": "GER", "name": "Wing", "count": 100, "mission": {"type": 0}, "allow_mission_type": 3}}}}
    state["countries"]["GER"]["units"]["fleet"] = {"id": {"id": 1}, "name": "Fleet", "task_force": {
        "id": {"id": 2}, "name": "Force", "location": 10, "logical_country": "GER", "mission": {"mission": 8}}}
    return state


def observed(state, tag):
    return {"match_id": "test", "turn_id": 1, "country": tag, "date": state["date"], "observation_id": "snapshot",
            "research": {"slot_count": 0, "visible_slots": []}, "military_production": [], "owned_states": [],
            "candidates": {"research": [], "focus": [], "construction_buildings": []},
            "naval_air": {"state_fingerprint": naval_air_fingerprint(state, tag), "action_options": {
                "air.assign": {"wings": [{"id": 5, "missions": ["air_superiority", "cas"], "targets": [{"region_id": 1}]}]},
                "air.stop": {"wings": [{"id": 5}]}, "navy.move": {"fleets": [{"id": 1, "targets": [{"province_id": 10}]}]}}}}


def air_action():
    return {"id": "1-GER-1", "type": "air.assign", "args": {"wing_id": 5, "region_id": 1, "mission": "air_superiority"}}


class NavalAirIntegrationTests(unittest.TestCase):
    def test_model_menu_and_contract_route_exact_wing_and_target(self):
        state = fixture()
        obs = observed(state, "GER")
        request = {key: obs[key] for key in ("match_id", "turn_id", "country", "date", "observation_id")}
        request["actions"] = [air_action()]
        self.assertIn("air.assign", options(obs))
        self.assertEqual(validate_request(request, obs), request["actions"])
        request["actions"].append({"id": "different", "type": "air.stop", "args": {"wing_id": 5}})
        with self.assertRaises(ValueError):
            validate_request(request, obs)

    def test_air_changes_do_not_hide_behind_the_economy_army_fingerprint(self):
        before = fixture()
        after = copy.deepcopy(before)
        after["strategic_air"]["GER"]["air_wing_pool"]["air_wings"]["mission"] = {"type": 1, "strategic_region": 1}
        self.assertEqual(country_fingerprint(before, "GER"), country_fingerprint(after, "GER"))
        self.assertNotEqual(naval_air_fingerprint(before, "GER"), naval_air_fingerprint(after, "GER"))
        snapshots = {tag: observed(before, tag) for tag in before["countries"]}
        self.assertTrue(check_naval_air_baseline(before, snapshots)["verified"])
        with self.assertRaises(ValueError):
            check_naval_air_baseline(after, snapshots)
        self.assertTrue(check_naval_air_baseline(after, snapshots, after)["verified"])

    def test_saved_air_success_is_resolved_once_and_retained_during_clock_recovery(self):
        before = fixture()
        after = copy.deepcopy(before)
        after["strategic_air"]["GER"]["air_wing_pool"]["air_wings"]["mission"] = {"type": 1, "strategic_region": 1}
        proof = verify_action(air_action(), before, after, "GER")
        self.assertTrue(proof["verified"], proof)
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            for name, state in (("before", before), ("after", after)):
                (directory / (name + ".json")).write_text(json.dumps(state), encoding="utf-8")
            result = {"status": "interrupted", "countries": list(before["countries"]), "before_save": {"name": "hoi4live_before"},
                      "actions": [{"country": "GER", "action": air_action(), "status": "unknown", "save": {"name": "hoi4live_after", "path": "synthetic"}}]}
            _, rows = resume_checkpoint(result, directory)
            self.assertEqual(rows[0]["resolved_status"], "applied")
            self.assertTrue(retained_orders(result, after, after)[0]["retained_or_completed"])

    def test_economy_action_cannot_also_change_a_peers_air_mission(self):
        before = fixture()
        after = copy.deepcopy(before)
        after["countries"]["ITA"]["production"]["military_lines"][0]["requested_factories"] = 3
        after["strategic_air"]["GER"]["air_wing_pool"]["air_wings"]["mission"] = {"type": 1, "strategic_region": 1}
        action = {"id": "one", "type": "production.assign", "args": {"line_id": 1, "factories": 3}}
        proof = verify_action(action, before, after, "ITA")
        self.assertFalse(proof["verified"])
        self.assertFalse(proof["checks"]["peers_naval_air_unchanged"])


if __name__ == "__main__":
    unittest.main()
