"""Offline tests for the auditable resume-time provider/model switch."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import campaign
from campaign import (apply_model_switch, build_switch_record, effective_resumed_config,
                      model_slot_fingerprints, switch_fingerprint_list, validate_switch_spec)
from model_trial import previous_setup_digest, recorded_complete
from showrunner import model_settings
from verify_campaign import Audit
from verify_native_campaign import audit_model_switches, effective_config_at


OLD = {"base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V4.1-Flash",
       "api_key_env": "IRON_SILICONFLOW_API_KEY", "enable_thinking": False, "temperature": 0.3,
       "max_tokens": 1800, "timeout_seconds": 90}
SPEC = {"base_url": "https://kuaipao.ai/v1", "model": "deepseek-v4.1-flash-1017-特价",
        "api_key_env": "IRON_KUAIPAO_API_KEY", "timeout_seconds": 120}


def show_config():
    return {"countries": ["GER", "FRA", "ITA", "AUS", "SWI"], "model": dict(OLD),
            "commentary_model": dict(OLD), "model_commentary": True,
            "show": {"referee_model": dict(OLD), "models": {}}}


class SwitchSpecTests(unittest.TestCase):
    def test_apply_switch_replaces_every_slot_without_touching_the_frozen_config(self):
        config = show_config()
        applied = apply_model_switch(config, SPEC)
        for slot in (applied["model"], applied["commentary_model"], applied["show"]["referee_model"]):
            self.assertEqual(slot["base_url"], SPEC["base_url"])
            self.assertEqual(slot["model"], SPEC["model"])
            self.assertEqual(slot["api_key_env"], SPEC["api_key_env"])
            self.assertEqual(slot["timeout_seconds"], 120)
        for slot in (applied["model"], applied["commentary_model"], applied["show"]["referee_model"]):
            self.assertEqual(slot["temperature"], 0.3)
            self.assertEqual(slot["max_tokens"], 1800)
        self.assertEqual(config["model"]["base_url"], OLD["base_url"])
        self.assertEqual(config["show"]["referee_model"]["model"], OLD["model"])

    def test_spec_validation_rejects_unknown_fields_and_missing_identity(self):
        with self.assertRaises(ValueError):
            validate_switch_spec({})
        with self.assertRaises(ValueError):
            validate_switch_spec({"base_url": "https://kuaipao.ai/v1", "model": "x", "messages": []})
        with self.assertRaises(ValueError):
            validate_switch_spec({"model": "only-model"})
        self.assertEqual(validate_switch_spec(dict(SPEC)), SPEC)

    def test_fingerprint_lists_cover_player_referee_and_commentary_slots(self):
        config = show_config()
        applied = apply_model_switch(config, SPEC)
        old_list, new_list = switch_fingerprint_list(model_slot_fingerprints(config)), switch_fingerprint_list(model_slot_fingerprints(applied))
        self.assertEqual(len(old_list), len(config["countries"]) + 2)
        self.assertEqual(len(new_list), len(old_list))
        self.assertNotIn(switch_identity_of(model_settings(config["model"])), new_list)
        self.assertIn(switch_identity_of(model_settings(applied["model"])), new_list)
        self.assertNotEqual(old_list, new_list)

    def test_switch_record_requires_a_real_provider_change(self):
        with self.assertRaises(ValueError):
            build_switch_record(show_config(), {"timeout_seconds": 300}, reason="r", source="s")
        record = build_switch_record(show_config(), SPEC, reason="balance", source="resume")
        self.assertEqual(record["schema"], "iron.model-switch.v1")
        self.assertEqual(record["reason"], "balance")
        self.assertEqual(switch_fingerprint_list(record["from"]), switch_fingerprint_list(model_slot_fingerprints(show_config())))
        self.assertEqual(switch_fingerprint_list(record["to"]), switch_fingerprint_list(model_slot_fingerprints(apply_model_switch(show_config(), SPEC))))

    def test_effective_resumed_config_folds_recorded_switches(self):
        manifest = {"config": show_config(), "turns": [], "model_switches": [
            build_switch_record(show_config(), SPEC, reason="balance", source="resume")]}
        applied = effective_resumed_config(manifest)
        self.assertEqual(applied["model"]["base_url"], SPEC["base_url"])
        self.assertEqual(effective_resumed_config({"config": show_config(), "turns": []})["model"]["base_url"], OLD["base_url"])

    def test_re_resuming_with_a_recorded_spec_reuses_the_ledger_entry(self):
        from native_campaign import apply_campaign_switch
        manifest = {"config": show_config(), "turns": [{"index": 5, "status": "deciding"}], "model_switches": []}
        config, first = apply_campaign_switch(manifest, dict(SPEC), reason="balance", source="resume")
        self.assertEqual(len(manifest["model_switches"]), 1)
        self.assertEqual(first["first_affected_turn"], 5)
        budget = {"reasoning_effort": "low", "max_tokens": 4000}
        config2, second = apply_campaign_switch(manifest, dict(SPEC, request_parameters=budget),
                                                reason="budget", source="resume")
        self.assertEqual(len(manifest["model_switches"]), 2)
        self.assertEqual(second["first_affected_turn"], 5)
        self.assertEqual(config2["model"]["request_parameters"], budget)
        again_config, again = apply_campaign_switch(manifest, dict(SPEC, request_parameters=budget),
                                                    reason="budget", source="resume")
        self.assertEqual(len(manifest["model_switches"]), 2)
        self.assertIs(again, second)
        self.assertEqual(again_config["model"]["request_parameters"], budget)
        self.assertEqual(config["model"]["base_url"], SPEC["base_url"])
        self.assertNotIn("request_parameters", config["model"])


def switch_identity_of(fingerprint):
    from campaign import switch_identity
    return switch_identity(fingerprint)


class CachedCallReuseTests(unittest.TestCase):
    MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "prompt"}]

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        (self.directory / "model_calls").mkdir()
        self.old_model, self.new_model = dict(OLD), {**OLD, **SPEC}
        self.record = {"phase": "intent", "country": "GER", "messages": self.MESSAGES,
                       "response": {"country": "GER", "goal": "hold the line"},
                       "metadata": {"requested_model": OLD["model"], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}},
                       "request_config": model_settings(self.old_model)}
        (self.directory / "model_calls" / "intent_GER.json").write_text(json.dumps(self.record), encoding="utf-8")
        self.switch = build_switch_record(show_config(), SPEC, reason="balance", source="resume")

    def write_switch(self, value=None):
        if value is not None or value is None and self.switch is not None:
            payload = self.switch if value is None else value
            (self.directory / "model_switch.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_cached_record_is_reused_across_a_recorded_switch_without_a_network_call(self):
        self.write_switch()
        with mock.patch("model_trial.complete", side_effect=AssertionError("network call attempted")):
            record = recorded_complete(self.directory, "intent_GER", self.new_model, self.MESSAGES)
        self.assertEqual(record["response"], {"country": "GER", "goal": "hold the line"})
        self.assertEqual(record["request_config"], model_settings(self.old_model))

    def test_reuse_is_refused_without_a_switch_file_or_with_a_foreign_fingerprint(self):
        with self.assertRaises(ValueError):
            recorded_complete(self.directory, "intent_GER", self.new_model, self.MESSAGES)
        forged = build_switch_record(apply_model_switch(show_config(), {"base_url": "https://elsewhere.example/v1", "model": "third"}), SPEC, reason="r", source="s")
        self.write_switch(forged)
        with self.assertRaises(ValueError):
            recorded_complete(self.directory, "intent_GER", self.new_model, self.MESSAGES)

    def test_prompt_mismatch_is_still_refused_under_a_switch(self):
        self.write_switch()
        with self.assertRaises(ValueError):
            recorded_complete(self.directory, "intent_GER", self.new_model, [{"role": "user", "content": "different"}])

    def test_a_switch_that_changes_nothing_cannot_bridge_two_different_live_models(self):
        with self.assertRaises(ValueError):
            build_switch_record(show_config(), {"max_tokens": 900}, reason="r", source="s")

    def test_request_parameters_count_as_identity_but_plain_limits_do_not(self):
        budget = build_switch_record(show_config(), dict(SPEC, request_parameters={"reasoning_effort": "low", "max_tokens": 4000}),
                                     reason="reasoning budget", source="resume")
        self.assertEqual(budget["schema"], "iron.model-switch.v1")
        self.assertNotEqual(budget["from_fingerprints"], budget["to_fingerprints"])
        with self.assertRaises(ValueError):
            build_switch_record(show_config(), {"max_tokens": 4000}, reason="r", source="s")


class DecideResumeTests(unittest.TestCase):
    def test_previous_setup_digest_reconstructs_the_frozen_decision_setup(self):
        from showrunner import country_model, referee_model
        from model_trial import digest
        config = {"countries": ["GER", "FRA"], "model": dict(OLD), "show": {"referee_model": dict(OLD)}}
        setup_old = {"countries": ["GER", "FRA"], "communication_rounds": 2, "private_diplomacy": True,
                     "models": {tag: model_settings(country_model(config["model"], config["show"], tag)) for tag in config["countries"]},
                     "referee_model": model_settings(referee_model(config["model"], config["show"])), "show": {"step_hours": 24}}
        stored = digest(setup_old)
        new_default = {**OLD, **SPEC}
        setup_new = {**setup_old, "models": {tag: model_settings(country_model(new_default, config["show"], tag)) for tag in config["countries"]},
                     "referee_model": model_settings(referee_model(new_default, config["show"]))}
        switch = build_switch_record(config, SPEC, reason="balance", source="resume")
        self.assertEqual(previous_setup_digest(setup_new, switch["from"]), stored)
        self.assertNotEqual(digest(setup_new), stored)


class VerifierLedgerTests(unittest.TestCase):
    def test_valid_ledger_passes_and_timeline_selects_by_started_at(self):
        audit = Audit(Path("."))
        manifest = {"config": show_config(),
                    "model_switches": [dict(build_switch_record(show_config(), SPEC, reason="balance", source="resume"),
                                            first_affected_turn=17, applied_at="2026-11-08T15:00:00+00:00")]}
        steps = audit_model_switches(audit, manifest)
        statuses = {row["check"]: row["status"] for row in audit.checks}
        self.assertEqual(statuses.get("model_switch_ledger"), "passed")
        self.assertEqual(len(steps), 1)
        before = effective_config_at(steps, manifest["config"], "2026-11-08T14:00:00+00:00")
        after = effective_config_at(steps, manifest["config"], "2026-11-08T16:00:00+00:00")
        self.assertEqual(before["model"]["base_url"], OLD["base_url"])
        self.assertEqual(after["model"]["base_url"], SPEC["base_url"])
        self.assertEqual(effective_config_at(steps, manifest["config"], None)["model"]["base_url"], OLD["base_url"])

    def test_a_ledger_that_does_not_match_the_frozen_config_fails_the_audit(self):
        audit = Audit(Path("."))
        config = show_config()
        config["model"] = {**OLD, "model": "tampered-model"}
        manifest = {"config": config, "model_switches": [
            dict(build_switch_record(show_config(), SPEC, reason="r", source="s"),
                 first_affected_turn=17, applied_at="2026-11-08T15:00:00+00:00")]}
        steps = audit_model_switches(audit, manifest)
        statuses = {row["check"]: row["status"] for row in audit.checks}
        self.assertEqual(statuses.get("model_switch_ledger"), "failed")
        self.assertEqual(steps, [])


if __name__ == "__main__":
    unittest.main()
