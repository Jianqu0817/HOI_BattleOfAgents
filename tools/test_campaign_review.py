"""Campaign credential selection."""
import unittest

import campaign


class CampaignReviewTests(unittest.TestCase):
    def test_disabled_commentary_does_not_require_its_unused_key(self):
        value = {"model": {"api_key_env": "PLAYER_KEY"}, "commentary_model": {},
                 "show": {"referee_model": {}, "models": {}}}
        value["model_commentary"] = False
        value["commentary_model"]["api_key_env"] = "UNUSED_COMMENTARY_KEY"
        self.assertNotIn("UNUSED_COMMENTARY_KEY", campaign.key_envs(value))
        value["model_commentary"] = True
        self.assertIn("UNUSED_COMMENTARY_KEY", campaign.key_envs(value))
