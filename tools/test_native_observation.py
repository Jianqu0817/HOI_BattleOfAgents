import copy
import unittest

from native_observation import build_observation, to_native_plan, validate_request, IDENTITY


def snapshot(tag="AUS", date=100):
    return {"control": {"state": {"paused": True, "ai_enabled": False, "date_raw": date, "country_index": 11}},
            "state_date_raw": date, "date": "1936.5.24.4",
            "divisions": {"country": tag, "units": [{"unit_id": 7, "province": 10, "path": []},
                                                          {"unit_id": 8, "province": 10, "path": [11]}]},
            "air": {"country": tag, "units": [{"id": 3, "current": 1, "capability_mask": 5, "region": 21, "handle": "0xabc"},
                                                  {"id": 4, "current": 0, "capability_mask": 5, "region": 0}]},
            "navy": {"country": tag, "units": [{"id": 5, "current": 0, "fleet_id": 6, "regions": [9]}]}}


STATE = {"countries": {"AUS": {}}, "states": {"1": {"owner": "AUS"}, "2": {"owner": "ITA"}},
         "provinces": {}, "public_wars": []}
MAP = {"map": {"provinces": {"10": {"state": 1}, "11": {"state": 1}, "12": {"state": 2}}},
       "graph": {"10": [11, 12]}, "passability": {"1": True, "2": True}}


def observe(tag="AUS", snapshots=None, **kwargs):
    return build_observation(tag, snapshots or snapshot(tag), "match", 1, {"native": True}, **kwargs)


def request(observed, actions=None):
    return {**{key: observed[key] for key in IDENTITY}, "actions": actions or [], "reason": "保持计划"}


class NativeObservationTests(unittest.TestCase):
    def test_more_move_choices_do_not_expand_sealed_action_allowance(self):
        data = snapshot()
        data["divisions"]["units"] = [{"unit_id": i, "province": 10, "path": []} for i in range(12)]
        extended = observe(snapshots=data, state=STATE, map_context=MAP, move_limit=8)
        moves = extended["action_options"]["division.move"]
        self.assertEqual(len({a["args"]["unit_id"] for a in moves}), 8)
        self.assertEqual(len(observe(snapshots=data, state=STATE, map_context=MAP)["action_options"]["division.move"]), 2)
        from model_trial import validate_trial_request
        with self.assertRaisesRegex(ValueError, "action limit"):
            validate_trial_request(request(extended, moves[:3]), extended, 2)

    def test_reconsultation_uses_earliest_sealed_request_and_daily_default(self):
        obs = {tag: observe(tag, review_options=[24, 48, 72]) for tag in ("GER", "FRA", "AUS")}
        req = {tag: {**request(value), "review_after_hours": 72} for tag, value in obs.items()}
        self.assertEqual(to_native_plan(req, obs, step_hours=72)["step_hours"], 72)
        req["FRA"]["review_after_hours"] = 48
        self.assertEqual(to_native_plan(req, obs, step_hours=72)["step_hours"], 48)
        del req["GER"]["review_after_hours"]
        self.assertEqual(to_native_plan(req, obs, step_hours=72)["step_hours"], 24)
        req["FRA"]["review_after_hours"] = 96
        with self.assertRaisesRegex(ValueError, "published choices"):
            to_native_plan(req, obs, step_hours=72)

    def test_bounded_owned_actions_and_no_raw_handles(self):
        observed = observe(state=STATE, map_context=MAP)
        moves = observed["action_options"]["division.move"]
        self.assertEqual([row["args"] for row in moves], [{"unit_id": 7, "source": 10, "target": 11}])
        self.assertEqual(observed["action_options"]["air.mission"][0]["args"],
                         {"wing_id": 3, "expected_mask": 1, "mission_bit": 4, "enabled": True})
        self.assertFalse(observed["action_options"]["air.region"])
        self.assertNotIn("handle", str(observed))
        self.assertTrue(all(len(rows) <= 2 for rows in observed["action_options"].values()))

    def test_stale_ownership_never_generates_moves(self):
        data = snapshot(); data["state_date_raw"] -= 1
        self.assertFalse(observe(snapshots=data, state=STATE, map_context=MAP)["action_options"]["division.move"])

    def test_verified_public_war_allows_enemy_border_target(self):
        state = copy.deepcopy(STATE)
        state['public_wars'] = [{'first': 'AUS', 'second': 'ITA'}]
        observed = observe(state=state, map_context=MAP)
        self.assertEqual(observed['action_options']['division.move'][0]['args']['target'], 12)
        self.assertEqual(observed['army']['wars'][0]['enemy'], 'ITA')

    def test_foreign_identity_stale_args_and_mutated_observation_rejected(self):
        observed = observe()
        action = copy.deepcopy(observed["action_options"]["air.mission"][0])
        valid = request(observed, [action])
        self.assertEqual(validate_request(valid, observed), [action])
        for key, value in (("country", "GER"), ("observation_id", "foreign"), ("turn_id", 2)):
            bad = copy.deepcopy(valid); bad[key] = value
            with self.assertRaises(ValueError): validate_request(bad, observed)
        bad = copy.deepcopy(valid); bad["actions"][0]["args"]["expected_mask"] = 0
        with self.assertRaisesRegex(ValueError, "exact observed"): validate_request(bad, observed)
        bad = copy.deepcopy(valid); bad["actions"][0]["args"]["enabled"] = 1
        with self.assertRaisesRegex(ValueError, "exact observed"): validate_request(bad, observed)
        mutated = copy.deepcopy(observed); mutated["action_options"]["air.mission"][0]["args"]["wing_id"] = 999
        with self.assertRaisesRegex(ValueError, "content changed"): validate_request(valid, mutated)

    def test_foreign_snapshot_and_private_history_rejected(self):
        data = snapshot(); data["air"]["country"] = "GER"
        with self.assertRaisesRegex(ValueError, "another country"): observe(snapshots=data)
        with self.assertRaisesRegex(ValueError, "another country"):
            observe(history=[{"private_decision": {"country": "GER", "actions": []}}])
        own = [{"turn_id": 0, "private_decision": {"country": "AUS", "actions": []}}]
        self.assertEqual(observe(history=own)["recent_history"], own)

    def test_declarations_require_owned_available_current_goal(self):
        data = snapshot(); data["diplomacy"] = {
            "ITA": {"actor_index": 11, "recipient_index": 2, "at_war": False,
                    "goals": [{"handle": "0x123", "actor_index": 11, "recipient_index": 2, "available": True},
                              {"handle": "0x999", "actor_index": 99, "recipient_index": 2, "available": True}]}}
        observed = observe(snapshots=data)
        self.assertEqual(len(observed["action_options"]["diplomacy.declare_war"]), 1)
        data["diplomacy"]["ITA"]["at_war"] = True
        self.assertFalse(observe(snapshots=data)["action_options"]["diplomacy.declare_war"])

    def test_plan_reuses_exact_actions_and_rejects_mixed_dates(self):
        observed = {tag: observe(tag) for tag in ("GER", "FRA", "AUS")}
        requests = {tag: request(value) for tag, value in observed.items()}
        action = observed["AUS"]["action_options"]["air.mission"][0]
        requests["AUS"]["actions"] = [action]
        plan = to_native_plan(requests, observed, step_hours=1)
        self.assertEqual(plan["countries"][2]["actions"], [{"id": action["id"], "kind": action["type"], **action["args"]}])
        observed["FRA"]["date_raw"] += 1
        with self.assertRaisesRegex(ValueError, "frozen native turn"): to_native_plan(requests, observed)


if __name__ == "__main__":
    unittest.main()
