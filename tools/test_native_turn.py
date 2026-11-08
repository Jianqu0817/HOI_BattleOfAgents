import copy
import json
from pathlib import Path
import tempfile
import unittest

from native_rpc import NativeRpcError
from native_turn import NativeTurn, validate_plan


def plan():
    return {"schema": "iron.native-turn.v1", "step_hours": 1, "disable_ai": True,
            "countries": [{"tag": "AUS", "actions": [{"id": "move", "kind": "division.move", "unit_id": 7, "source": 10, "target": 11}]},
                          {"tag": "GER", "actions": []}, {"tag": "FRA", "actions": []}]}


class FakeGame:
    pid = 100
    creation_time = 200
    def __init__(self, *, stale=False, unknown=False):
        self.calls = []; self.played = "AUS"; self.date = 60762746
        self.ai = {"AUS": True, "GER": True, "FRA": True}
        self.index = {"AUS": 11, "GER": 1, "FRA": 2}
        self.path = []; self.province = 10; self.stale = stale; self.unknown = unknown

    def call(self, op, payload="", *, request_id):
        self.calls.append((op, payload, request_id))
        words = payload.split()
        if op == "main_status": return {"synthetic": True}
        if op == "native_control_v1":
            if words[0] == "status":
                tag = words[1]
                return {"ok": True, "state": {"paused": True, "date_raw": self.date, "country_index": self.index[tag], "played_index": self.index[self.played], "ai_enabled": self.ai[tag]}}
            if words[0] == "tag":
                assert words[1] == self.played; self.played = words[2]; return {"ok": True}
            if words[0] == "ai":
                self.ai[words[1]] = False; return {"ok": True}
            if words[0] == "save": return {"ok": True, "file_verified": False}
        if op == "native_move":
            assert words[1] == self.played
            if words[0] == "units": return {"ok": True, "units": []}
            if words[0] == "inspect_unit": return {"ok": True, "province": 99 if self.stale else self.province, "path": self.path.copy()}
            if words[0] == "build_unit": return {"ok": True, "constructed_verified": True}
            if words[0] == "post_unit":
                if self.unknown: raise NativeRpcError("unknown", request_id=request_id, outcome="unknown")
                self.path = [11]; return {"ok": True, "queue_accepted": True, "posted": True}
        if op == "native_air_navy_v2": return {"ok": True, "units": []}
        if op == "native_clock_v1":
            if words[0] == "step": self.date += int(words[1]); self.province = 11; self.path = []; return {"ok": True, "queue_accepted": True}
            return {"ok": True, "state": {"paused": True, "remaining_hours": 0}}
        raise AssertionError((op, payload))


class NativeTurnTests(unittest.TestCase):
    def test_entire_schema_rejected_before_any_calls(self):
        invalid = plan(); invalid["countries"][-1]["actions"] = [{"id": "bad", "kind": "division.move", "army_id": 1}]
        game = FakeGame()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError): NativeTurn(game, invalid, Path(directory) / "run")
        self.assertEqual(game.calls, [])

    def test_schema_boundaries_and_conflicts(self):
        for change in (lambda p: p.update(step_hours=0), lambda p: p.update(step_hours=True),
                       lambda p: p.update(step_hours=361), lambda p: p["countries"].pop(),
                       lambda p: p["countries"][0]["actions"].append(copy.deepcopy(p["countries"][0]["actions"][0]))):
            candidate = plan(); change(candidate)
            with self.assertRaises(ValueError): validate_plan(candidate)
        candidate = plan(); candidate["countries"][0]["actions"] = [dict(candidate["countries"][0]["actions"][0], id=f"m{i}", unit_id=i+1) for i in range(7)]
        with self.assertRaises(ValueError): validate_plan(candidate)

    def test_late_stale_baseline_prevents_all_orders(self):
        game = FakeGame(stale=True)
        candidate = plan()
        candidate["countries"][-1]["actions"] = candidate["countries"][0]["actions"]
        candidate["countries"][0]["actions"] = []
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "stale"):
                NativeTurn(game, candidate, Path(directory) / "run", poll_interval=0).run()
        self.assertFalse(any("post_" in payload or payload.startswith("step ") or payload.startswith("ai ") for _, payload, _ in game.calls))

    def test_full_turn_restores_controller_verifies_effect_and_journals(self):
        game = FakeGame()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            result = NativeTurn(game, plan(), output, poll_interval=0).run()
            facts = [json.loads(line) for line in (output / "audience_facts.jsonl").read_text().splitlines()]
            before = next(i for i, row in enumerate(facts) if row["event"] == "preflight_complete")
            post = next(i for i, row in enumerate(facts) if row["event"] == "intent" and "post_unit" in row.get("payload", ""))
            self.assertLess(before, post)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["effects"][0]["effect_observed_after_clock"])
            self.assertFalse(result["save"]["file_verified"])
            self.assertEqual(game.played, "AUS")
            self.assertFalse(any(game.ai.values()))
            self.assertEqual(len({request_id for _, _, request_id in game.calls}), len(game.calls))
            self.assertEqual(facts[0]["creation_time"], 200)
            with self.assertRaises(FileExistsError): NativeTurn(game, plan(), output).run()

    def test_unknown_order_stops_without_retry_clock_or_recovery(self):
        game = FakeGame(unknown=True)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            with self.assertRaises(NativeRpcError): NativeTurn(game, plan(), output, poll_interval=0).run()
            facts = [json.loads(line) for line in (output / "audience_facts.jsonl").read_text().splitlines()]
            self.assertTrue(facts[-1]["unknown_outcome"])
        self.assertEqual(sum(payload.startswith("post_unit") for _, payload, _ in game.calls), 1)
        self.assertTrue(game.calls[-1][1].startswith("post_unit"))

    def test_mission_and_region_payloads_and_effects(self):
        cases = [
            ({"kind": "air.mission", "wing_id": 1, "expected_mask": 2, "mission_bit": 1, "enabled": True}, "air_post AUS 1 2 1 1", {"current": 3}),
            ({"kind": "air.region", "wing_id": 1, "expected_region": 0, "target_region": 21}, "air_region_post AUS 1 0 21", {"region": 21}),
            ({"kind": "navy.mission", "task_force_id": 2, "expected_type": 0, "mission_type": 1}, "navy_post AUS 2 0 1", {"current": 1}),
            ({"kind": "navy.regions", "task_force_id": 2, "expected_regions": [], "target_regions": [9]}, "navy_regions_post AUS 2 - 9", {"regions": [9]}),
            ({"kind": "diplomacy.declare_war", "recipient": "ITA", "goal_handle": "0x123"}, "post_declare AUS ITA 0x123", {"at_war": True, "shared_war_relation_count": 1})]
        for action, payload, observed in cases:
            self.assertEqual(NativeTurn.payload("AUS", action)[1], payload)
            self.assertTrue(NativeTurn.effect(action, observed))

    def test_navy_internal_target_rejected_but_baseline_readable(self):
        candidate = plan()
        candidate["countries"][0]["actions"] = [{"id": "navy", "kind": "navy.mission", "task_force_id": 2,
                                                   "expected_type": 0, "mission_type": 8}]
        with self.assertRaisesRegex(ValueError, "normal UI"):
            validate_plan(candidate)
        candidate["countries"][0]["actions"][0].update(expected_type=8, mission_type=1)
        self.assertIs(validate_plan(candidate), candidate)

    def test_air_mission_observes_requested_bit_with_engine_exclusions(self):
        action = {"kind": "air.mission", "expected_mask": 1, "mission_bit": 4, "enabled": True}
        self.assertTrue(NativeTurn.effect(action, {"current": 4}))
        self.assertFalse(NativeTurn.effect(action, {"current": 1}))
        action["enabled"] = False
        self.assertTrue(NativeTurn.effect(action, {"current": 1}))
        self.assertFalse(NativeTurn.effect(action, {"current": 4}))


if __name__ == "__main__": unittest.main()
