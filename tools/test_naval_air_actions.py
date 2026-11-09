"""Exact own-identity air/naval assignment and paused isolation; no game or API."""
import copy
import unittest
import tempfile
from pathlib import Path

from naval_air_actions import (air_wings, naval_air_observation, validate_naval_air_action,
                               verify_naval_air_action, retained_naval_air_order,
                               verify_naval_air_binding, discover_naval_air_binding, selected_air_count)


def wing(identity, tag="GER", definition="fighter"):
    return {"id": {"id": identity + 2000}, "definition": definition, "air_base": {"id": 9},
            "air_wings": {"id": {"id": identity}, "tag": tag, "name": str(71000000 + identity), "count": 100,
                          "allow_mission_type": 2053 if definition == "fighter" else 2330,
                          "mission": {"type": 0, "active": False}}}


def fleet(identity, location=10):
    return {"id": {"id": identity}, "name": str(81000000 + identity),
            "task_force": {"id": {"id": identity + 100}, "logical_country": "GER", "location": location,
                           "ship": {"id": {"id": identity + 500}, "strength": 100}, "mission": {"mission": 8}}}


class NavalAirTests(unittest.TestCase):
    def setUp(self):
        self.state = {"date": "1936.5.1.0", "states": {}, "provinces": {},
            "countries": {"GER": {"units": {"fleet": [fleet(1), fleet(2)]}}, "ITA": {"units": {}}},
            "strategic_air": {"air_base": {"id": {"id": 9}, "state": 64},
                              "GER": {"air_wing_pool": [wing(1), wing(2, definition="tac_bomber")]},
                              "ITA": {"air_wing_pool": wing(90, "ITA")}}}
        air = {"name": "71000001", "accepted_actions": ["air.assign", "air.stop"], "missions": ["air_superiority", "cas"], "region_ids": [19]}
        navy = {"name": "81000001", "accepted_actions": ["navy.assign", "navy.move", "navy.stop"], "missions": ["patrol"], "region_ids": [42], "province_ids": [11]}
        self.context = {"profile": {"countries": {"GER": {"air_wings": {"1": air}, "fleets": {"1": navy}}}},
                        "map": {"regions": {"19": {"name_key": "AIR19", "naval": False}, "42": {"name_key": "SEA42", "naval": True}},
                                "provinces": {"11": {"kind": "sea"}, "12": {"kind": "land"}}}}
        self.action = {"id": "air", "type": "air.assign", "args": {"wing_id": 1, "region_id": 19, "mission": "air_superiority"}}

    def observed(self):
        return {"naval_air": naval_air_observation(self.state, "GER", self.context)}

    def test_own_observation_excludes_foreign_wings_and_unsupported_types(self):
        observed = self.observed()["naval_air"]
        self.assertEqual({row["id"] for row in observed["air_wings"]}, {1, 2})
        self.assertEqual(observed["action_options"]["air.assign"]["wings"][0]["missions"], ["air_superiority"])
        self.assertEqual(validate_naval_air_action(self.action, self.observed()), ("air", 1))
        bad = copy.deepcopy(self.action)
        bad["args"]["mission"] = "cas"
        with self.assertRaises(ValueError):
            validate_naval_air_action(bad, self.observed())

    def test_no_profile_no_own_forces_or_missing_air_block_does_not_invent_capabilities(self):
        result = naval_air_observation(self.state, "GER")
        self.assertTrue(all(value is None for value in result["action_options"].values()))
        self.assertEqual(naval_air_observation(self.state, "ITA")["unavailable"]["navy"], "no own fleets")
        state = copy.deepcopy(self.state)
        state.pop("strategic_air")
        self.assertFalse(naval_air_observation(state, "GER")["air_data_available"])

    def test_wrong_object_region_boolean_and_land_naval_target_are_rejected(self):
        for changes in ({"wing_id": 90}, {"wing_id": True}, {"region_id": 99}, {"extra": 1}):
            action = copy.deepcopy(self.action)
            action["args"].update(changes)
            with self.assertRaises(ValueError):
                validate_naval_air_action(action, self.observed())
        action = {"id": "navy", "type": "navy.move", "args": {"fleet_id": 1, "province_id": 12}}
        with self.assertRaises(ValueError):
            validate_naval_air_action(action, self.observed())

    def test_exact_air_mission_and_region_preserve_every_other_wing(self):
        after = copy.deepcopy(self.state)
        air_wings(after, "GER")[1]["wing"]["mission"] = {"type": 1, "active": True, "strategic_region": 19}
        self.assertTrue(verify_naval_air_action(self.action, self.state, after, "GER")["verified"])
        self.assertTrue(retained_naval_air_order(self.action, self.state, after, "GER"))
        air_wings(after, "ITA")[90]["wing"]["mission"] = {"type": 1}
        self.assertFalse(verify_naval_air_action(self.action, self.state, after, "GER")["verified"])

    def test_air_rebase_count_change_and_wrong_region_do_not_count_as_success(self):
        for mutation in ("base", "count", "region"):
            after = copy.deepcopy(self.state)
            row = air_wings(after, "GER")[1]
            row["wing"]["mission"] = {"type": 1, "strategic_region": 19}
            if mutation == "base":
                after["strategic_air"]["GER"]["air_wing_pool"][0]["air_base"] = {"id": 99}
            elif mutation == "count":
                row["wing"]["count"] = 200
            else:
                row["wing"]["mission"]["strategic_region"] = 20
            self.assertFalse(verify_naval_air_action(self.action, self.state, after, "GER")["verified"])

    def test_air_stop_clears_mission_without_requiring_immediate_return_to_base(self):
        before = copy.deepcopy(self.state)
        air_wings(before, "GER")[1]["wing"]["mission"] = {"type": 1, "strategic_region": 19}
        after = copy.deepcopy(before)
        air_wings(after, "GER")[1]["wing"]["mission"]["type"] = 0
        action = {"id": "stop", "type": "air.stop", "args": {"wing_id": 1}}
        self.assertTrue(verify_naval_air_action(action, before, after, "GER")["verified"])

    def test_navy_patrol_needs_fleet_region_and_every_taskforce_assignment(self):
        after = copy.deepcopy(self.state)
        f = after["countries"]["GER"]["units"]["fleet"][0]
        f["strategic_region"] = {"__items__": [42]}
        f["task_force"]["mission"] = {"mission": 0, "accessible_regions": {"__items__": [42]}}
        action = {"id": "patrol", "type": "navy.assign", "args": {"fleet_id": 1, "region_id": 42, "mission": "patrol"}}
        self.assertTrue(verify_naval_air_action(action, self.state, after, "GER")["verified"])
        f["task_force"]["mission"]["accessible_regions"] = {"__items__": [18]}
        self.assertFalse(verify_naval_air_action(action, self.state, after, "GER")["verified"])

    def test_navy_move_requires_player_path_and_rejects_teleport_or_ship_changes(self):
        after = copy.deepcopy(self.state)
        tf = after["countries"]["GER"]["units"]["fleet"][0]["task_force"]
        tf.update(path={"__items__": [11]}, move_priority="player_order")
        action = {"id": "move", "type": "navy.move", "args": {"fleet_id": 1, "province_id": 11}}
        self.assertTrue(verify_naval_air_action(action, self.state, after, "GER")["verified"])
        tf["location"] = 11
        self.assertFalse(verify_naval_air_action(action, self.state, after, "GER")["verified"])
        tf["location"] = 10
        tf["ship"]["strength"] = 999
        self.assertFalse(verify_naval_air_action(action, self.state, after, "GER")["verified"])

    def test_numeric_binding_changes_only_the_named_identity(self):
        after = copy.deepcopy(self.state)
        air_wings(after, "GER")[1]["wing"]["name"] = "71999999"
        self.assertTrue(verify_naval_air_binding(self.state, after, "GER", "air_wings", 1, "71999999")["verified"])
        air_wings(after, "GER")[2]["wing"]["count"] += 1
        self.assertFalse(verify_naval_air_binding(self.state, after, "GER", "air_wings", 1, "71999999")["verified"])

    def test_discovery_binding_finds_real_id_and_requires_the_own_base(self):
        after = copy.deepcopy(self.state)
        air_wings(after, "GER")[2]["wing"]["name"] = "71999999"
        result = discover_naval_air_binding(self.state, after, "GER", "air_wings", "71999999", base_id=9)
        self.assertTrue(result["verified"])
        self.assertEqual(result["id"], 2)
        self.assertFalse(discover_naval_air_binding(self.state, after, "GER", "air_wings", "71999999", base_id=99)["verified"])

    def test_selection_count_does_not_guess_from_plane_numbers_or_fractions(self):
        self.assertEqual(selected_air_count(["1 个联队"]), 1)
        self.assertEqual(selected_air_count(["1 selected"]), 1)
        self.assertIsNone(selected_air_count(["100/200"]))
        self.assertIsNone(selected_air_count(["0/1 个联队"]))
        self.assertIsNone(selected_air_count(["100"]))


    def test_green_counter_reads_zero_every_digit_and_multidigit_without_expected_value(self):
        import numpy as np
        from PIL import Image
        from naval_air_actions import green_air_count
        glyphs = []
        for digit in range(10):
            glyph = np.zeros((5, 11), dtype=bool)
            glyph[[0, 4], :] = True
            glyph[1:4, digit] = True
            glyphs.append(glyph)
        atlas = np.zeros((5, 120, 4), dtype=np.uint8)
        definitions = []
        for digit, glyph in enumerate(glyphs):
            atlas[:, digit * 12:digit * 12 + 11, 3] = glyph * 255
            definitions.append(f"char id={48 + digit} x={digit * 12} y=0 width=11 height=5")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "hoi_18mbs.fnt").write_text("\n".join(definitions), encoding="utf-8")
            Image.fromarray(atlas).save(path / "hoi_18mbs.dds", format="PNG")
            for text in [str(i) for i in range(10)] + ["12", "101", "999"]:
                image = np.zeros((720, 1280, 3), dtype=np.uint8)
                for index, char in enumerate(text):
                    image[155:160, 98 + index * 12:109 + index * 12, 1] = glyphs[int(char)] * 200
                count, evidence = green_air_count(image, path)
                self.assertEqual(count, int(text))
                self.assertEqual(evidence["text"], text)
            self.assertIsNone(green_air_count(np.zeros((720, 1280, 3), dtype=np.uint8), path)[0])


if __name__ == "__main__":
    unittest.main()
