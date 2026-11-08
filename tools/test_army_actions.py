"""Own-country army identity, exact movement effects and recovery checks."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from army_actions import army_observation, movement_targets, validate_army_action, verify_army_action, retained_army_order, province_id_from_tooltip, tooltip_army_name, retained_acceptance, debug_response_state, full_frame_province_id, verified_operations, existing_army_tooltip, numeric_army_identity
from agent_contract import validate_request


def unit(identity, location, country="GER"):
    return {"id": [{"id": identity, "type": 51}, {"id": identity, "type": 51}],
            "location": location, "logical_country": country, "organisation": 45,
            "strength": 227.2, "army_manpower": {"army_manpower_value": {"value": {"tag": country, "value": 9600}}}}


class ArmyTests(unittest.TestCase):
    def setUp(self):
        army = {"id": {"id": 7}, "name": "IRONGERA7", "member": [{"unit": {"id": 1}}, {"unit": {"id": 2}}]}
        self.state = {"date": "1936.4.30.11", "countries": {
            "GER": {"units": {"division": [unit(1, 10), unit(2, 10), unit(3, 11)]},
                    "theatres": {"theatre": {"orders_group": army}},
                    "diplomacy": {"active_relations": {"POL": {"war_relation": {"first": "GER", "second": "POL", "first_casualties": 7}}}}},
            "ITA": {"units": {"division": unit(90, 90, "ITA")}}},
            "states": {"1": {"owner": "GER"}, "2": {"owner": "POL"}}, "provinces": {}}
        self.context = {"map": {"provinces": {str(pid): {"state": 1 if pid < 20 else 2} for pid in (10, 11, 20)}},
                        "graph": {"10": [11, 20], "11": [10], "20": [10]},
                        "profile": {"countries": {"GER": {"7": {"name": "IRONGERA7", "accepted_actions": ["army.move", "army.stop"]}}}}}
        self.context["operations"] = ["army.move", "army.stop"]
        self.context["passability"] = {"1": True, "2": True}
        self.context["passability_available"] = True
        self.observed = {"match_id": "x", "turn_id": 1, "country": "GER", "date": "1936.4.30.11", "observation_id": "snapshot",
                         "army": army_observation(self.state, "GER", self.context)}
        self.action = {"id": "1-GER-1", "type": "army.move", "args": {"army_id": 7, "target_province_id": 11}}

    def test_own_observation_has_raw_stats_but_no_foreign_units(self):
        data = self.observed["army"]
        self.assertEqual({row["id"] for row in data["divisions"]}, {1, 2, 3})
        self.assertEqual(data["divisions"][0]["manpower"], 9600)
        self.assertEqual(data["divisions"][0]["strength_raw"], 227.2)
        self.assertEqual(data["wars"], [{"enemy": "POL", "since": None, "our_casualties": 7}])

    def test_only_own_battle_participants_and_no_enemy_details_are_observed(self):
        self.state["countries"]["GER"]["units"]["division"][0]["last_combat_date"] = "1936.4.29.11"
        self.state["combat"] = {"land_combat": [
            {"id": {"id": 90}, "location": 20, "duration": 7, "day": 1,
             "attacker": {"unit": {"id": 1}, "losses": 5},
             "defender": {"unit": {"id": 999}, "strength": "ENEMY_SECRET", "losses": "SECRET_LOSSES"}},
            {"id": {"id": 91}, "location": 30, "attacker": {"unit": {"id": 999}}, "defender": {"unit": {"id": 998}}}]}
        observed = army_observation(self.state, "GER", self.context)
        self.assertEqual(observed["active_battles"], [{"battle_id": 90, "province_id": 20, "role": "attacker",
                                                       "own_division_ids": [1], "duration_raw": 7, "day_raw": 1}])
        self.assertEqual(observed["divisions"][0]["last_combat_date"], "1936.4.29.11")
        self.assertTrue(observed["combat_observation_available"])
        self.assertNotIn("ENEMY_SECRET", json.dumps(observed))
        self.assertNotIn("SECRET_LOSSES", json.dumps(observed))
        self.assertNotIn("999", json.dumps(observed))

    def test_repeated_unit_references_inside_one_combat_side_are_all_observed(self):
        self.state["combat"] = {"land_combat": {"id": {"id": 108}, "location": 20,
            "attacker": {"unit": [{"id": 1, "type": 51}, {"id": 2, "type": 51}],
                         "front": [{"id": 2, "type": 51}, {"id": 1, "type": 51}]},
            "defender": {"unit": [{"id": 999, "type": 51}, {"id": 998, "type": 51}], "losses": "ENEMY_LOSSES"}}}
        observed = army_observation(self.state, "GER", self.context)
        self.assertEqual(observed["active_battles"][0]["own_division_ids"], [1, 2])
        self.assertNotIn("999", json.dumps(observed))
        self.assertNotIn("ENEMY_LOSSES", json.dumps(observed))

    def test_no_binding_or_acceptance_means_no_executable_menu(self):
        data = army_observation(self.state, "GER")
        self.assertEqual(data["action_options"], {})
        wrong = copy.deepcopy(self.context)
        wrong["profile"]["countries"]["GER"]["7"]["name"] = "OLD_ARMY"
        self.assertEqual(army_observation(self.state, "GER", wrong)["action_options"], {})

    def test_missing_province_observation_closes_movement_and_enemy_candidates(self):
        old = copy.deepcopy(self.state)
        old.pop("provinces")
        data = army_observation(old, "GER", self.context)
        self.assertNotIn("army.move", data["action_options"])
        self.assertEqual(data["armies"][0]["attack_candidates"], [])

    def test_exact_province_override_and_war_relation_control_attack_targets(self):
        group = self.state["countries"]["GER"]["theatres"]["theatre"]["orders_group"]
        targets = movement_targets(self.state, "GER", group, self.context, attack=True)
        self.assertEqual([row["province_id"] for row in targets], [20])
        self.state["provinces"]["20"] = {"controller": "GER"}
        self.assertEqual(movement_targets(self.state, "GER", group, self.context, attack=True), [])
        self.state["provinces"]["20"] = {"controller": "ITA"}
        self.assertEqual(movement_targets(self.state, "GER", group, self.context, attack=True), [])

    def test_attack_province_and_state_must_match(self):
        context = copy.deepcopy(self.context)
        context["profile"]["countries"]["GER"]["7"]["accepted_actions"].append("army.attack")
        context["operations"].append("army.attack")
        observed = {**self.observed, "army": army_observation(self.state, "GER", context)}
        action = {"type": "army.attack", "args": {"army_id": 7, "target_province_id": 20, "target_state_id": 2}}
        validate_army_action(action, observed)
        action["args"]["target_state_id"] = 1
        with self.assertRaises(ValueError):
            validate_army_action(action, observed)

    def test_tooltip_identity_does_not_confuse_state_and_province(self):
        self.assertEqual(province_id_from_tooltip(["State ID: 159", "Province ID: 3776"]), 3776)
        self.assertEqual(province_id_from_tooltip(["省 份 I D ： 3776"]), 3776)
        self.assertIsNone(province_id_from_tooltip(["State ID: 159", "Population: 3776"]))
        self.assertEqual(province_id_from_tooltip(["Province ID- 13246 (o EN)"]), 13246)
        self.assertIsNone(province_id_from_tooltip(["Province ID=132祸 (ETH)"]))
        self.assertIsNone(province_id_from_tooltip(["Province ID=13246abc"]))
        self.assertIsNone(province_id_from_tooltip(["Province 13246 (ETH)"]))

    def test_debug_toggle_uses_last_explicit_console_state(self):
        self.assertEqual(debug_response_state([{"response": ["Province tooltip debug ENABLED", "Province tooltip debug DISABLED"]}]), "disabled")
        self.assertEqual(debug_response_state([{"response": ["Province toottip debug ENABLED"]}]), "enabled")
        self.assertIsNone(debug_response_state([{"response": ["Centering to province."]}]))

    def test_bulk_binding_never_matches_new_army_or_marshal_group(self):
        names = ["第1集团军", "New Army", "Army 1"]
        self.assertEqual(tooltip_army_name(["第 1 集 团 军", "2 个师"], names), "第1集团军")
        self.assertEqual(tooltip_army_name(["点击选中", "第1集团军", "创建集团军之后可以编写作战计划"], names), "第1集团军")
        self.assertIsNone(tooltip_army_name(["创建新集团军"], names))
        self.assertIsNone(tooltip_army_name(["New Army"], names))
        self.assertIsNone(tooltip_army_name(["第1集团军群"], names))
        self.assertIsNone(tooltip_army_name(["Army 11"], names))
        self.assertTrue(existing_army_tooltip(["击选中", "创建集团军之后就可以编写作战计划，从而可以组织", "大量单位的多阶段行动。"] ))
        self.assertTrue(existing_army_tooltip(["0点击选中", "创建集团军之后就可以编写作战计划"] ))
        self.assertFalse(existing_army_tooltip(["要创建集团军群必须先选中一些集团军！", "新建集团军群"]))
        self.assertFalse(existing_army_tooltip(["点击选中", "集团军群可以通过高层指令和计划来指挥所属的集团军。"]))

    def test_bulk_rebind_only_retains_proved_same_member_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proof.json"
            proof = {"verified": True, "country": "GER", "army_id": 7,
                     "action": self.action, "verification": {"verified": True, "observations": {"members": [1, 2]}}}
            path.write_text(json.dumps(proof), encoding="utf-8")
            old = self.state["countries"]["GER"]["theatres"]["theatre"]["orders_group"]
            new = copy.deepcopy(old)
            new["name"] = "IRONGERB1X1234"
            profile = {"name": old["name"], "accepted_actions": ["army.move"], "acceptance_evidence": {"army.move": str(path)}}
            self.assertEqual(retained_acceptance(profile, "GER", 7, old, new)["accepted_actions"], ["army.move"])
            proof["country"] = "ITA"
            path.write_text(json.dumps(proof), encoding="utf-8")
            self.assertEqual(retained_acceptance(profile, "GER", 7, old, new)["accepted_actions"], [])
            proof["country"] = "GER"
            path.write_text(json.dumps(proof), encoding="utf-8")
            new["member"] = [{"unit": {"id": 1}}]
            self.assertEqual(retained_acceptance(profile, "GER", 7, old, new)["accepted_actions"], [])

    def test_full_frame_fallback_requires_one_id_across_both_ocr_scales(self):
        class OCR:
            def recognize(self, image, scale, language=None):
                return SimpleNamespace(lines=[SimpleNamespace(text="Province ID=3776")])
        identity, candidates, _ = full_frame_province_id(SimpleNamespace(ocr=OCR()), object())
        self.assertEqual((identity, candidates), (3776, [3776]))
        class AmbiguousOCR:
            def recognize(self, image, scale, language=None):
                return SimpleNamespace(lines=[SimpleNamespace(text="Province ID=3776 (ITA) Province ID=123 (ETH)")])
        self.assertIsNone(full_frame_province_id(SimpleNamespace(ocr=AmbiguousOCR()), object())[0])

    def test_english_complete_field_precedes_truncated_chinese_digits(self):
        class OCR:
            calls = []
            def recognize(self, image, scale, language=None):
                self.calls.append(language)
                text = ("Province 13246 (o EN)" if scale == 1.5 else "Province ID- 13246 (o EN)") if language == "en-US" else "Province ID=132祸 (ETH)"
                return SimpleNamespace(lines=[SimpleNamespace(text=text)])
        ocr = OCR()
        identity, candidates, _ = full_frame_province_id(SimpleNamespace(ocr=ocr), object())
        self.assertEqual((identity, candidates), (13246, [13246]))
        self.assertEqual(ocr.calls, ["en-US", "en-US"])

    def test_conflicting_complete_english_ids_do_not_fall_back_to_chinese(self):
        class OCR:
            def recognize(self, image, scale, language=None):
                self_language = language
                if self_language != "en-US":
                    raise AssertionError("Conflicting English identities must stop")
                return SimpleNamespace(lines=[SimpleNamespace(text=f"Province ID= {13246 if scale == 1.5 else 13240} (ETH)")])
        identity, candidates, _ = full_frame_province_id(SimpleNamespace(ocr=OCR()), object())
        self.assertIsNone(identity)
        self.assertEqual(candidates, [13240, 13246])

    def test_numeric_army_identity_uses_whole_english_token_not_joined_chinese_digits(self):
        class OCR:
            def recognize(self, image, region=None, scale=None, language=None):
                text = "709919" if language == "en-US" else "7 919"
                return SimpleNamespace(lines=[SimpleNamespace(text=text)])
        label, candidates, _ = numeric_army_identity(SimpleNamespace(ocr=OCR()), object(), [62, 84, 145, 38])
        self.assertEqual((label, candidates), ("709919", ["709919"]))
        class BrokenOCR:
            def recognize(self, image, region=None, scale=None, language=None):
                return SimpleNamespace(lines=[SimpleNamespace(text="7 919")])
        self.assertIsNone(numeric_army_identity(SimpleNamespace(ocr=BrokenOCR()), object(), [62, 84, 145, 38])[0])

    def test_numeric_army_identity_conflicts_are_not_chosen_by_request(self):
        class OCR:
            def recognize(self, image, region=None, scale=None, language=None):
                return SimpleNamespace(lines=[SimpleNamespace(text="709919" if scale == 2 else "709918")])
        label, candidates, _ = numeric_army_identity(SimpleNamespace(ocr=OCR()), object(), [62, 84, 145, 38])
        self.assertIsNone(label)
        self.assertEqual(candidates, ["709918", "709919"])

    def test_ui_capability_is_shared_only_for_matching_profile_and_real_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "move.json"
            identity = {"exe_sha256": "build-a", "render_size": [1280, 720], "ocr_language": "zh-Hans-CN"}
            proof = {"operation": "probe", "status": "verified", "verified": True, "ui_profile": identity,
                     "army_id": 7, "action": self.action,
                     "verification": {"verified": True, "checks": {"same_date": True}, "observations": {"members": [1, 2]}}}
            path.write_text(json.dumps(proof), encoding="utf-8")
            profile = {"accepted_operations": {"army.move": {"ui_profile": identity, "evidence": str(path)}}}
            self.assertEqual(verified_operations(profile, identity), ["army.move"])
            self.assertEqual(verified_operations(profile, {**identity, "exe_sha256": "build-b"}), [])
            self.assertEqual(verified_operations(profile, {**identity, "ocr_language": "en-US"}), [])
            proof["action"]["type"] = "army.stop"
            path.write_text(json.dumps(proof), encoding="utf-8")
            self.assertEqual(verified_operations(profile, identity), [])

    def test_target_is_reachable_on_friendly_land_for_every_member(self):
        targets = self.observed["army"]["action_options"]["army.move"]["armies"][0]["targets"]
        self.assertEqual([row["province_id"] for row in targets], [11])
        validate_army_action(self.action, self.observed)
        invalid = copy.deepcopy(self.action)
        invalid["args"]["target_province_id"] = 20
        with self.assertRaises(ValueError):
            validate_army_action(invalid, self.observed)

    def test_impassable_and_unknown_states_are_neither_targets_nor_routes(self):
        context = copy.deepcopy(self.context)
        context["map"]["provinces"].update({"11": {"state": 3}, "12": {"state": 1}})
        context["graph"] = {"10": [11], "11": [10, 12, 20], "12": [11], "20": [11]}
        self.state["states"]["3"] = {"owner": "GER"}
        group = self.state["countries"]["GER"]["theatres"]["theatre"]["orders_group"]
        context["passability"]["3"] = False
        self.assertEqual(movement_targets(self.state, "GER", group, context), [])
        self.assertEqual(movement_targets(self.state, "GER", group, context, attack=True), [])
        context["passability"].pop("3")
        context["passability_available"] = False
        context["passability_unknown_states"] = [3]
        observed = army_observation(self.state, "GER", context)
        self.assertFalse(observed["passability_observation_available"])
        self.assertEqual(observed["unknown_passability_states"], [3])
        self.assertNotIn("army.move", observed["action_options"])

    def test_foreign_army_and_duplicate_members_are_rejected(self):
        for identity in (99, True):
            invalid = copy.deepcopy(self.action)
            invalid["args"]["army_id"] = identity
            with self.assertRaises(ValueError):
                validate_army_action(invalid, self.observed)
        observed = copy.deepcopy(self.observed)
        observed["army"]["action_options"]["army.move"]["armies"][0]["division_ids"] = [1, 1]
        with self.assertRaises(ValueError):
            validate_army_action(self.action, observed)

    def test_same_army_cannot_receive_two_orders_in_one_batch(self):
        request = {key: self.observed[key] for key in ("match_id", "turn_id", "country", "date", "observation_id")}
        request["actions"] = [self.action, {"id": "1-GER-2", "type": "army.stop", "args": {"army_id": 7}}]
        with self.assertRaises(ValueError):
            validate_request(request, self.observed)
        request["actions"] = [self.action]
        self.assertEqual(len(validate_request(request, self.observed)), 1)
        request["date"] = "1936.5.1.11"
        with self.assertRaises(ValueError):
            validate_request(request, self.observed)

    def moved(self):
        after = copy.deepcopy(self.state)
        for row in after["countries"]["GER"]["units"]["division"][:2]:
            row.update(path={"__items__": [11]}, move_priority="player_order")
        return after

    def test_order_requires_exact_target_path_not_teleport_or_foreign_change(self):
        after = self.moved()
        self.assertTrue(verify_army_action(self.action, self.state, after, "GER")["verified"])
        for mutate in (lambda value: value["countries"]["GER"]["units"]["division"][0].update(location=11),
                       lambda value: value["countries"]["ITA"]["units"]["division"].update(location=91),
                       lambda value: value["countries"]["GER"]["units"]["division"][0].update(path={"__items__": [20]}),
                       lambda value: value.update(date="1936.5.1.11")):
            bad = copy.deepcopy(after)
            mutate(bad)
            self.assertFalse(verify_army_action(self.action, self.state, bad, "GER")["verified"])

    def test_completed_movement_is_retained_without_requiring_old_path(self):
        checkpoint, current = self.moved(), self.moved()
        current["date"] = "1936.5.2.11"
        for row in current["countries"]["GER"]["units"]["division"][:2]:
            row["location"] = 11
            row.pop("path")
            row.pop("move_priority")
        self.assertTrue(retained_army_order(self.action, checkpoint, current, "GER"))
        current["countries"]["GER"]["units"]["division"][0]["location"] = 20
        self.assertFalse(retained_army_order(self.action, checkpoint, current, "GER"))

    def test_stop_clears_both_movement_and_plan_execution(self):
        before = self.moved()
        after = copy.deepcopy(before)
        for row in after["countries"]["GER"]["units"]["division"][:2]:
            row.pop("path")
            row.pop("move_priority")
        action = {"type": "army.stop", "args": {"army_id": 7}}
        self.assertTrue(verify_army_action(action, before, after, "GER")["verified"])
        self.assertTrue(retained_army_order(action, before, after, "GER"))
        after["countries"]["GER"]["units"]["division"][0]["execute_order"] = 1
        self.assertFalse(verify_army_action(action, before, after, "GER")["verified"])

    def test_attack_order_can_resolve_into_a_recorded_battle_retreat(self):
        checkpoint = self.moved()
        current = copy.deepcopy(checkpoint)
        current["date"] = "1936.5.2.11"
        for row in current["countries"]["GER"]["units"]["division"][:2]:
            row.pop("path")
            row["last_combat_date"] = "1936.5.1.11"
        action = {"type": "army.attack", "args": {"army_id": 7, "target_province_id": 20, "target_state_id": 2}}
        self.assertTrue(retained_army_order(action, checkpoint, current, "GER"))
        current["countries"]["GER"]["units"]["division"][0]["last_combat_date"] = "1936.4.29.11"
        self.assertFalse(retained_army_order(action, checkpoint, current, "GER"))

    def test_destroyed_attacking_unit_needs_new_casualty_evidence(self):
        checkpoint = self.moved()
        checkpoint["combat"] = {"land_combat": {"id": {"id": 100}, "location": 20, "attacker": {"unit": {"id": 1}}}}
        current = copy.deepcopy(checkpoint)
        current["date"] = "1936.5.2.11"
        current["countries"]["GER"]["units"]["division"] = current["countries"]["GER"]["units"]["division"][1:]
        current["countries"]["GER"]["theatres"]["theatre"]["orders_group"]["member"] = [{"unit": {"id": 2}}]
        current["countries"]["GER"]["units"]["division"][0]["last_combat_date"] = "1936.5.1.11"
        action = {"type": "army.attack", "args": {"army_id": 7, "target_province_id": 20, "target_state_id": 2}}
        self.assertFalse(retained_army_order(action, checkpoint, current, "GER"))
        current["countries"]["GER"]["diplomacy"]["active_relations"]["POL"]["war_relation"]["first_casualties"] = 9000
        self.assertTrue(retained_army_order(action, checkpoint, current, "GER"))
        checkpoint.pop("combat")
        self.assertFalse(retained_army_order(action, checkpoint, current, "GER"))

    def partial_attack(self):
        before = copy.deepcopy(self.state)
        group = before["countries"]["GER"]["theatres"]["theatre"]["orders_group"]
        group["order_instance"] = [{"instance_id": 2, "type": 2, "path": {"__items__": [10, 11]}, "can_execute": 1},
                                   {"instance_id": 15, "type": 1, "path": {"__items__": [10, 20]}, "can_execute": 1}]
        before["countries"]["GER"]["units"]["division"][1]["execute_order"] = 48
        before["combat"] = {"land_combat": {"id": {"id": 119}, "location": 10, "duration": 81,
                                             "defender": {"unit": {"id": 2}}, "attacker": {"unit": {"id": 999}}}}
        after = copy.deepcopy(before)
        after["countries"]["GER"]["units"]["division"][0].update(path={"__items__": [20]}, move_priority="player_order")
        for order in after["countries"]["GER"]["theatres"]["theatre"]["orders_group"]["order_instance"]:
            order.pop("can_execute")
        action = {"type": "army.attack", "args": {"army_id": 7, "target_province_id": 20, "target_state_id": 2}}
        return action, before, after

    def test_attack_reports_unchanged_local_defender_as_partial_dispatch(self):
        action, before, after = self.partial_attack()
        result = verify_army_action(action, before, after, "GER")
        self.assertTrue(result["verified"], result)
        self.assertEqual(result["effect_status"], "partial")
        self.assertEqual(result["observations"]["responded_units"], [1])
        self.assertEqual(result["observations"]["blocked_units"], [{"division_id": 2, "reason": "defending_local_battle", "battle_id": 119, "province_id": 10}])
        self.assertEqual(result["observations"]["new_path_units"], [1])
        self.assertEqual(len(result["observations"]["plan_stop_changes"]), 2)

    def test_partial_attack_does_not_explain_unknown_or_changed_units_away(self):
        action, before, after = self.partial_attack()
        no_defense = copy.deepcopy(after)
        no_defense["combat"] = {}
        self.assertFalse(verify_army_action(action, before, no_defense, "GER")["verified"])
        changed = copy.deepcopy(after)
        changed["countries"]["GER"]["units"]["division"][1]["organisation"] -= 1
        self.assertFalse(verify_army_action(action, before, changed, "GER")["verified"])
        no_new_path = copy.deepcopy(before)
        before["countries"]["GER"]["units"]["division"][0].update(path={"__items__": [20]}, move_priority="player_order")
        no_new_path["countries"]["GER"]["units"]["division"][0].update(path={"__items__": [20]}, move_priority="player_order")
        self.assertFalse(verify_army_action(action, before, no_new_path, "GER")["verified"])

    def test_plan_stop_allows_only_selected_army_enabled_to_disabled_flag(self):
        action, before, after = self.partial_attack()
        bad = copy.deepcopy(after)
        bad["countries"]["GER"]["theatres"]["theatre"]["orders_group"]["order_instance"][0]["path"] = {"__items__": [999]}
        self.assertFalse(verify_army_action(action, before, bad, "GER")["verified"])
        old_group = before["countries"]["GER"]["theatres"]["theatre"]["orders_group"]
        new_group = after["countries"]["GER"]["theatres"]["theatre"]["orders_group"]
        other = {"id": {"id": 8}, "name": "OTHER", "order_instance": {"can_execute": 1}}
        before["countries"]["GER"]["theatres"]["theatre"]["orders_group"] = [old_group, copy.deepcopy(other)]
        other["order_instance"].pop("can_execute")
        after["countries"]["GER"]["theatres"]["theatre"]["orders_group"] = [new_group, other]
        self.assertFalse(verify_army_action(action, before, after, "GER")["verified"])

    def test_partial_attack_recovery_accepts_continuing_defense_and_evidenced_retreat(self):
        action, _, checkpoint = self.partial_attack()
        current = copy.deepcopy(checkpoint)
        current["date"] = "1936.5.1.11"
        defender = current["countries"]["GER"]["units"]["division"][1]
        defender["organisation"] -= 5
        self.assertTrue(retained_army_order(action, checkpoint, current, "GER"))
        current["combat"] = {}
        defender.update(location=11, last_combat_date="1936.5.1.5", path={"__items__": [11]})
        self.assertTrue(retained_army_order(action, checkpoint, current, "GER"))
        defender["last_combat_date"] = "1936.4.20.1"
        self.assertFalse(retained_army_order(action, checkpoint, current, "GER"))


if __name__ == "__main__":
    unittest.main()
