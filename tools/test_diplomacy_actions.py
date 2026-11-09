"""Declarations require real goals, preserve private orders and handle reciprocal sealed requests."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

from agent_contract import country_fingerprint, validate_request, verify_action
from diplomacy_actions import (checkbox_state, diplomacy_fingerprint, diplomacy_observation,
                               public_war_graph, reciprocal_war, retained_declaration, selected_dialog_goal,
                               state_id_from_tooltip, verify_declaration)
from model_trial import options, validate_trial_request
from run_turn import check_diplomacy_baseline, resume_checkpoint
from verify_campaign import Audit, audit_execution


def fixture():
    state = {"date": "1936.5.7.11", "faction": [], "states": {}, "countries": {}}
    for index, tag in enumerate(("GER", "FRA", "ITA"), 1):
        state["states"][str(index)] = {"owner": tag, "buildings": {}}
        state["countries"][tag] = {"capital": index, "diplomacy": {"active_relations": {}, "wargoals": {}},
                                    "focus": {}, "technology": {}, "production": {}, "units": {}, "politics": {}}
    for actor, target, identity in (("GER", "FRA", 11), ("FRA", "GER", 12)):
        state["countries"][actor]["diplomacy"]["wargoals"] = {"annex_everything": {
            "id": {"id": identity, "type": 4713}, "wargoaldata_actor": actor, "wargoaldata_recipient": target,
            "type": "annex_everything"}}
    return state


def war(state, actor, target):
    result = copy.deepcopy(state)
    relation = {"first": actor, "second": target, "start_date": state["date"]}
    for country, other in ((actor, target), (target, actor)):
        result["countries"][country]["diplomacy"]["active_relations"][other] = {"war_relation": copy.deepcopy(relation)}
    return result


def action(actor="GER", target="FRA", identity=11):
    return {"id": "1-" + actor + "-1", "type": "diplomacy.declare_war", "args": {"target_country": target, "wargoal_id": identity}}


def observed(state, tag):
    return {"match_id": "arena", "turn_id": 1, "country": tag, "date": state["date"], "observation_id": "frozen-" + tag,
            "research": {"slot_count": 0, "visible_slots": []}, "military_production": [], "owned_states": [],
            "candidates": {"research": [], "focus": [], "construction_buildings": []},
            "diplomacy": diplomacy_observation(state, tag)}


class DiplomacyTests(unittest.TestCase):
    def test_selected_goal_requires_unique_exact_label_in_right_pane(self):
        def line(text, x, y):
            return SimpleNamespace(text=text, center=(x, y))
        left_goal = line("吞 并", 510, 252)
        right_goal = line("吞 并", 766, 237)
        self.assertFalse(selected_dialog_goal([left_goal], "吞并", 377, 94))
        self.assertTrue(selected_dialog_goal([left_goal, right_goal], "吞并", 377, 94))
        self.assertFalse(selected_dialog_goal([left_goal, right_goal, right_goal], "吞并", 377, 94))
        self.assertFalse(selected_dialog_goal([left_goal, line("傀儡", 766, 237)], "吞并", 377, 94))
        self.assertFalse(selected_dialog_goal([left_goal, right_goal, line("未选择战争目标", 766, 270)], "吞并", 377, 94))

    def test_menu_requires_own_goal_and_reports_unsupported_justification(self):
        state = fixture()
        data = diplomacy_observation(state, "GER", {"annex_everything": "吞并"})
        targets = data["action_options"]["diplomacy.declare_war"]["targets"]
        self.assertEqual([row["country"] for row in targets], ["FRA"])
        self.assertEqual(targets[0]["wargoals"][0]["id"], 11)
        self.assertEqual(targets[0]["wargoals"][0]["name"], "吞并")
        self.assertFalse(data["justify_supported"])
        self.assertIn({"country": "ITA", "reason": "no_wargoal"}, data["blocked_targets"])
        state.pop("faction")
        self.assertEqual(diplomacy_observation(state, "GER")["action_options"], {})

    def test_same_faction_and_ambiguous_goal_type_are_not_offered(self):
        state = fixture()
        state["faction"] = {"id": {"id": 5}, "name": "team", "members": {"__items__": ["GER", "FRA"]}}
        self.assertEqual(diplomacy_observation(state, "GER")["action_options"], {})
        state["faction"] = []
        goal = state["countries"]["GER"]["diplomacy"]["wargoals"]["annex_everything"]
        state["countries"]["GER"]["diplomacy"]["wargoals"]["annex_everything"] = [goal, {**goal, "id": {"id": 99}}]
        self.assertEqual(diplomacy_observation(state, "GER")["action_options"], {})

    def test_contract_binds_goal_target_and_rejects_duplicate_target(self):
        state = fixture()
        obs = observed(state, "GER")
        request = {key: obs[key] for key in ("match_id", "turn_id", "country", "date", "observation_id")}
        request["actions"] = [action()]
        self.assertEqual(len(validate_request(request, obs)), 1)
        self.assertEqual(len(validate_trial_request(request, obs)), 1)
        self.assertIn("diplomacy.declare_war", options(obs))
        for changes in ({"target_country": "ITA"}, {"wargoal_id": True}, {"wargoal_id": 12}):
            bad = copy.deepcopy(request)
            bad["actions"][0]["args"].update(changes)
            with self.assertRaises(ValueError):
                validate_request(bad, obs)
        request["actions"].append({**action(), "id": "other-id"})
        with self.assertRaises(ValueError):
            validate_request(request, obs)

    def test_new_war_and_reciprocal_second_order_are_distinct(self):
        before = fixture()
        after = war(before, "GER", "FRA")
        proof = verify_action(action(), before, after, "GER")
        self.assertTrue(proof["verified"], proof)
        self.assertEqual(proof["effect_status"], "full")
        self.assertTrue(proof["observations"]["new_war"])
        second = action("FRA", "GER", 12)
        proof = verify_declaration(second, after, copy.deepcopy(after), "FRA")
        self.assertTrue(proof["verified"], proof)
        self.assertEqual(proof["effect_status"], "already_present")
        self.assertFalse(proof["observations"]["new_war"])
        self.assertFalse(proof["observations"]["declaration_sent"])
        self.assertTrue(retained_declaration(second, after, after, "FRA"))

    def test_single_shared_relation_is_accepted_but_conflicts_and_side_effects_are_not(self):
        before = fixture()
        after = war(before, "GER", "FRA")
        after["countries"]["FRA"]["diplomacy"]["active_relations"].pop("GER")
        self.assertTrue(verify_declaration(action(), before, after, "GER")["verified"])
        self.assertEqual(diplomacy_observation(after, "FRA")["wars"][0]["enemy"], "GER")
        conflicting = war(before, "GER", "FRA")
        conflicting["countries"]["FRA"]["diplomacy"]["active_relations"]["GER"]["war_relation"]["start_date"] = "1936.1.1.12"
        with self.assertRaises(ValueError):
            public_war_graph(conflicting)
        after = war(before, "GER", "FRA")
        after["countries"]["ITA"]["production"] = {"unexpected": 1}
        self.assertFalse(verify_declaration(action(), before, after, "GER")["verified"])
        after = war(before, "GER", "FRA")
        after["date"] = "1936.5.7.12"
        self.assertFalse(verify_declaration(action(), before, after, "GER")["verified"])

    def test_diplomacy_is_a_separate_baseline_and_recovery_uses_latest_checkpoint(self):
        before = fixture()
        after = war(before, "GER", "FRA")
        self.assertEqual(country_fingerprint(before, "FRA"), country_fingerprint(after, "FRA"))
        self.assertNotEqual(diplomacy_fingerprint(before), diplomacy_fingerprint(after))
        snapshots = {tag: observed(before, tag) for tag in before["countries"]}
        self.assertTrue(check_diplomacy_baseline(before, snapshots)["verified"])
        with self.assertRaises(ValueError):
            check_diplomacy_baseline(after, snapshots)
        self.assertTrue(check_diplomacy_baseline(after, snapshots, after)["verified"])
        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)
            (directory / "before.json").write_text(json.dumps(before), encoding="utf-8")
            (directory / "after.json").write_text(json.dumps(after), encoding="utf-8")
            result = {"status": "interrupted", "countries": list(before["countries"]),
                      "before_save": {"name": "hoi4live_before"}, "actions": [
                          {"country": "GER", "action": action(), "status": "unknown", "save": {"name": "hoi4live_after", "path": str(directory / "after.hoi4")}}]}
            _, resolutions = resume_checkpoint(result, directory)
            self.assertEqual(resolutions[0]["resolved_status"], "applied")
            self.assertTrue(reciprocal_war(after, "GER", "FRA"))

    def test_checkbox_unknown_is_not_assumed_off_and_state_ids_must_be_unique(self):
        frames = np.zeros((30, 68, 4), dtype=np.uint8)
        frames[..., 3] = 255
        frames[:, :34, :3] = 20
        frames[:, 34:, :3] = 90
        image = np.zeros((80, 100, 3), dtype=np.uint8)
        image[10:40, 10:44] = 20
        self.assertIs(checkbox_state(image, frames, (10, 10)), False)
        image[10:40, 10:44] = 90
        self.assertIs(checkbox_state(image, frames, (10, 10)), True)
        image[10:40, 10:44] = 55
        self.assertIsNone(checkbox_state(image, frames, (10, 10)))
        self.assertEqual(state_id_from_tooltip(["State ID = 16 (France)"]), 16)
        self.assertIsNone(state_id_from_tooltip(["State ID = 16", "State ID = 64"]))

    def test_campaign_audit_distinguishes_mutual_sealed_declarations(self):
        before = fixture()
        after = war(before, "GER", "FRA")
        # The engine stores the shared relation only in the initiating side.
        after["countries"]["FRA"]["diplomacy"]["active_relations"].pop("GER")
        snapshots = {tag: observed(before, tag) for tag in before["countries"]}
        requests = {"GER": {"actions": [action()]}, "FRA": {"actions": [action("FRA", "GER", 12)]}, "ITA": {"actions": []}}

        def receipt(tags, date):
            return {"date": date, "receipts": [{"country": tag, "player": tag, "date": date} for tag in tags]}

        with tempfile.TemporaryDirectory() as path:
            directory = Path(path)

            def put(name, state):
                (directory / (name + ".json")).write_text(json.dumps(state), encoding="utf-8")
                return {"name": "hoi4live_" + name, "parsed_date": state["date"]}

            first = {"country": "GER", "action": action(), "status": "applied", "effect_status": "full",
                     "actor_receipt": receipt(["GER"], before["date"]), "save": put("after_first", after),
                     "verification": verify_declaration(action(), before, after, "GER"),
                     "ui_result": {"declaration_sent": True, "input_path": "ordinary UI"}}
            second_action = action("FRA", "GER", 12)
            second = {"country": "FRA", "action": second_action, "status": "applied", "effect_status": "already_present",
                      "actor_receipt": receipt(["FRA"], before["date"]), "save": put("after_second", after),
                      "verification": verify_declaration(second_action, after, after, "FRA"),
                      "ui_result": {"declaration_sent": False, "input_path": "none"}}
            final = copy.deepcopy(after)
            final["date"] = "1936.5.8.11"
            result = {"status": "completed_deterministic_turn", "before_save": put("before", before), "actions": [first, second],
                      "initial_switch": receipt(requests, before["date"]), "before_clock": receipt(requests, before["date"]),
                      "after_clock": receipt(requests, final["date"]), "final_save": put("final", final), "elapsed_game_hours": 24,
                      "clock_steps": [{"hours": 24, "advanced_hours": 24, "receipt": receipt(["GER"], final["date"])}],
                      "diplomacy_baseline_verification": check_diplomacy_baseline(before, snapshots)}
            audit = Audit(directory)
            audit_execution(audit, directory, result, requests, snapshots, 24, "mutual")
            self.assertTrue(all(row["status"] == "passed" for row in audit.checks), audit.checks)
            result["actions"][1]["verification"]["observations"]["new_war"] = True
            audit = Audit(directory)
            audit_execution(audit, directory, result, requests, snapshots, 24, "mutual")
            self.assertTrue(any(row["status"] == "failed" and row["check"].endswith("declaration_new_vs_already_present") for row in audit.checks))


if __name__ == "__main__":
    unittest.main()
