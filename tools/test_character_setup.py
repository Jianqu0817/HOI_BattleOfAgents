"""Authored card fidelity and identity visibility, without game or network calls."""
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest

from campaign import apply_model_switch, load_config
from model_trial import country_messages
from production import export_production
from showrunner import extend_prompt, settings
from story_channels import interview_messages, public_commentary_messages, public_context
from test_native_observation import observe


ROOT = Path(__file__).resolve().parents[1]


class CharacterSetupTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config("configs/campaign.characters.kuaipao.json")
        self.show = settings(self.config["show"], self.config["countries"])

    def test_import_preserves_authored_cards_and_personas(self):
        text = (ROOT / "docs/CHARACTERS.md").read_text(encoding="utf-8-sig")
        registry = json.loads((ROOT / "configs/characters.authored.json").read_text(encoding="utf-8"))
        for section in re.split(r"^## ", text, flags=re.M):
            face = re.search(r"```json\n(.*?)```", section, re.S)
            persona = re.search(r"```text\n(.*?)```", section, re.S)
            if not face or not persona:
                continue
            authored = json.loads(face.group(1))
            expected = authored["character_card"]
            entry = next(row for row in registry["characters"].values() if row["name"] == expected["display_name"])
            card = json.loads((ROOT / entry["card_file"]).read_text(encoding="utf-8"))
            self.assertEqual(card["character_card"], expected)
            self.assertEqual(card["persona"], persona.group(1).strip())
            self.assertEqual(card["voice"], authored["voice"])
            self.assertTrue((ROOT / card["portrait"]).is_file())

    def test_approved_cast_and_actual_backend_are_explicit(self):
        expected = {"GER": "白龙酱", "FRA": "克莱德大小姐", "ITA": "基米",
                    "AUS": "大肥鱼", "SWI": "千问大小姐", None: "大狐狸"}
        self.assertEqual({row["country"]: row["name"] for row in self.show["public_cast"]}, expected)
        for row in self.show["public_cast"]:
            self.assertEqual(row["execution_model"], self.config["model"]["model"])
        self.assertEqual(next(row for row in self.show["public_cast"] if row["country"] == "GER")["character_model"], "GPT")
        self.assertNotIn("{cast}", self.show["common_rules"])
        self.assertIn("战术欺骗", self.show["common_rules"])

    def test_cast_does_not_publish_other_personas_or_private_fields(self):
        show = copy.deepcopy(self.show)
        show["profiles"]["GER"]["experience"] = "OTHER_PRIVATE_EXPERIENCE_CANARY"
        show["profiles"]["GER"]["persona"] += "OTHER_PERSONA_CANARY"
        show["public_cast"][0]["private_decision"] = "PRIVATE_CAST_CANARY"
        observed = observe()
        messages = extend_prompt(country_messages(observed, "diplomacy", []), observed,
                                 "diplomacy", show, self.config["countries"])
        projected = public_context(messages, observed)
        self.assertNotIn("CANARY", json.dumps(projected, ensure_ascii=False))
        payload = json.loads(projected[1]["content"])
        self.assertEqual(len(payload["public_cast"]), 6)
        self.assertEqual(payload["own_profile"]["name"], "大肥鱼")
        self.assertEqual(json.loads(public_context(projected, observed)[1]["content"]), payload)

    def test_host_and_interview_keep_their_roles_and_channel_boundaries(self):
        observed = observe()
        host = json.loads(public_commentary_messages(observed, [], show=self.show)[1]["content"])
        self.assertEqual(host["own_profile"]["name"], "大狐狸")
        self.assertNotIn("action_options", host["own_observation"])
        seal = {"country": "AUS", "actions": [], "reason": "OWN_SEAL_CANARY"}
        messages = interview_messages(observed, seal, "感受如何？", show=self.show)
        interview = json.loads(messages[1]["content"])
        self.assertEqual(interview["own_profile"]["name"], "大肥鱼")
        self.assertEqual(interview["visibility"], "audience_only")
        self.assertEqual(interview["materials"]["locked_decision"], seal)
        self.assertNotIn("OWN_SEAL_CANARY", json.dumps(host))

    def test_provider_switch_updates_public_backend_without_recasting(self):
        old = copy.deepcopy(self.config)
        switched = apply_model_switch(self.config, {"base_url": "https://example.invalid/v1", "model": "next-test-model"})
        self.assertEqual(self.config, old)
        self.assertEqual([row["name"] for row in switched["show"]["public_cast"]],
                         [row["name"] for row in old["show"]["public_cast"]])
        self.assertTrue(all(row["execution_model"] == "next-test-model" for row in switched["show"]["public_cast"]))

    def test_video_export_contains_ready_card_and_voice_direction(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "episode.json").write_text(json.dumps({"events": [
                {"id": "public1", "kind": "public", "country": "AUS", "visibility": "public", "text": "先说正事。"}
            ]}), encoding="utf-8")
            export_production(output, self.config["production"])
            result = json.loads((output / "characters.json").read_text(encoding="utf-8"))
            character = result["characters"]["deepseek"]
            self.assertEqual(character["card_status"], "ready")
            self.assertEqual(character["name"], "大肥鱼")
            self.assertEqual(character["character_card"]["self_reference"], "我(不用'本鱼''人家'这类自称)")
            self.assertEqual(character["voice"]["speed"], .95)


if __name__ == "__main__":
    unittest.main()
