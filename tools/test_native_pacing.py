import unittest

from native_pacing import select_step_hours, validate_policy


class NativePacingTests(unittest.TestCase):
    def test_peace_war_and_public_decision_share_one_explicit_clock(self):
        self.assertEqual(select_step_hours(at_war=False)["step_hours"], 720)
        self.assertEqual(select_step_hours(at_war=True)["step_hours"], 24)
        choice = select_step_hours(at_war=False, public_events=[{"decision_required": True}])
        self.assertEqual(choice["step_hours"], 6)
        self.assertTrue(choice["shared_clock"])
        self.assertEqual(select_step_hours(at_war=False, policy={"peace_hours": 360})["step_hours"], 360)

    def test_invalid_config_cannot_change_clock_bounds(self):
        for config in ({"chunk_hours": 7}, {"peace_hours": 721}, {"war_hours": True},
                       {"maximum_hours": 100}, {"per_country_hours": 24}):
            with self.assertRaises(ValueError):
                validate_policy(config)


if __name__ == "__main__":
    unittest.main()
