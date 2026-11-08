"""Campaign orchestration acceptance with no game input or model requests."""
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import campaign


TAGS = ["GER", "ITA", "ENG"]
MODEL = {"base_url": "https://example.invalid/v1", "model": "default-model", "api_key_env": "IRON_TEST_KEY",
         "enable_thinking": False, "temperature": .3, "max_tokens": 1800, "timeout_seconds": 90}


def config():
    return {"countries": TAGS, "step_hours": 24, "turn_limit": 2, "action_limit": 1, "communication_rounds": 2,
            "model": copy.deepcopy(MODEL), "commentary_model": copy.deepcopy(MODEL),
            "summarize_lessons": True, "model_commentary": True,
            "show": {"enabled": True, "step_hours": 24, "action_limit": 1,
                     "profiles": {tag: {"name": tag, "persona": "PERSONA_" + tag} for tag in TAGS},
                     "models": {"ITA": {**MODEL, "model": "italian-player-model"}},
                     "referee_model": copy.deepcopy(MODEL)}}


@dataclass(frozen=True)
class FakeIdentity:
    pid: int
    creation_time: int = 12345
    executable_path: str = "test-python.exe"


class FakePlatform:
    def capture_process_identity(self, pid):
        return FakeIdentity(pid)


class FakeSession:
    platform = FakePlatform()

    def shutdown(self):
        pass


class FakeProbe:
    def __init__(self, *args):
        self.session = FakeSession()


class CampaignReviewTests(unittest.TestCase):
    def test_disabled_commentary_does_not_require_its_unused_key(self):
        value = config()
        value["model_commentary"] = False
        value["commentary_model"]["api_key_env"] = "UNUSED_COMMENTARY_KEY"
        self.assertNotIn("UNUSED_COMMENTARY_KEY", campaign.key_envs(value))
        value["model_commentary"] = True
        self.assertIn("UNUSED_COMMENTARY_KEY", campaign.key_envs(value))

    def test_campaign_lock_excludes_second_handle_and_recovers_after_release(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            first = campaign.acquire_campaign_lock(root, FakeSession())
            try:
                with self.assertRaises((ValueError, RuntimeError)):
                    campaign.acquire_campaign_lock(root, FakeSession())
            finally:
                campaign.release_campaign_lock(first)
            second = campaign.acquire_campaign_lock(root, FakeSession())
            campaign.release_campaign_lock(second)
            # Metadata remains useful but must never be mistaken for an active lease.
            third = campaign.acquire_campaign_lock(root, FakeSession())
            campaign.release_campaign_lock(third)
            start = threading.Barrier(2)

            def compete():
                start.wait(timeout=5)
                try:
                    return campaign.acquire_campaign_lock(root, FakeSession())
                except (ValueError, RuntimeError):
                    return None

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(compete) for _ in range(2)]
                held = [future.result() for future in futures]
            winners = [handle for handle in held if handle is not None]
            try:
                self.assertEqual(len(winners), 1)
            finally:
                for handle in winners:
                    campaign.release_campaign_lock(handle)

    def run_fixture(self, root, selected_config=None, fail_export=False, fail_lessons=False):
        calls = {"prepare": [], "decide": [], "children": [], "lessons": [], "commentary": [], "export": []}
        current = copy.deepcopy(selected_config or config())

        def prepare(trial, tags, pid, prior_trial, prior_turn, resume=False):
            calls["prepare"].append((trial, prior_trial, prior_turn, resume))
            campaign.write_json(trial / "prepared.json", {"countries": tags})

        def decide(trial, model, tags, **kwargs):
            calls["decide"].append((trial, copy.deepcopy(kwargs)))
            campaign.write_json(trial / "decision_result.json", {"status": "approved"})

        def run_child(arguments):
            calls["children"].append(arguments)
            args = list(map(str, arguments))
            name = args[args.index("--name") + 1]
            index = int(name.split("_t")[-1].split("_")[0])
            campaign.write_json(root / "runs/turns" / name / "result.json",
                                {"status": "completed_deterministic_turn", "countries": TAGS,
                                 "turn_id": index, "match_id": "fixture", "final_save": {"parsed_date": f"1936.5.{index}.11"}})

        def lessons(trial, turn, model, configs_by_country=None):
            calls["lessons"].append((trial, copy.deepcopy(configs_by_country)))
            if fail_lessons:
                raise RuntimeError("simulated lessons transport failure")
            index = int(trial.name.split("_t")[-1])
            return {tag: {"country": tag, "turn_id": index, "lessons": [{"claim": "OWN_LESSON_" + tag,
                        "evidence_ids": ["test"], "when_to_apply": "test condition", "confidence": .5,
                        "category": "strategy"}]} for tag in TAGS}

        def commentary(trial, turn, model):
            calls["commentary"].append(trial)
            return {"kind": "model_commentary", "content": {}}

        def export(trial, turn, output, content, **kwargs):
            calls["export"].append(trial)
            if fail_export and len(calls["export"]) == 1:
                raise RuntimeError("simulated export disk failure")
            output.mkdir(parents=True, exist_ok=True)
            (output / "index.html").write_text("fixture", encoding="utf-8")
            return {"output": str(output), "audience": "audience_full"}

        stack = ExitStack()
        stack.enter_context(patch.object(campaign, "ROOT", root))
        stack.enter_context(patch.object(campaign, "load_config", return_value=current))
        stack.enter_context(patch.object(campaign, "Probe", FakeProbe))
        stack.enter_context(patch.object(campaign, "establish_control", return_value=34280))
        stack.enter_context(patch.object(campaign, "run_child", side_effect=run_child))
        stack.enter_context(patch("model_trial.prepare", side_effect=prepare))
        stack.enter_context(patch("model_trial.decide", side_effect=decide))
        stack.enter_context(patch("lessons.generate_lessons", side_effect=lessons))
        stack.enter_context(patch("episode.generate_commentary", side_effect=commentary))
        stack.enter_context(patch("episode.export_episode", side_effect=export))
        stack.enter_context(patch.dict(os.environ, {"IRON_TEST_KEY": "test-only-placeholder"}))
        return stack, calls

    def test_two_turns_link_exact_predecessor_and_route_private_learning(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            stack, calls = self.run_fixture(root)
            with stack:
                result = campaign.run_campaign("fixture")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(calls["children"]), 2)
            self.assertEqual(len(calls["prepare"]), 2)
            self.assertIsNone(calls["prepare"][0][1])
            self.assertEqual(calls["prepare"][1][1], root / "runs/fixture_t001")
            self.assertEqual(calls["prepare"][1][2], root / "runs/turns/fixture_t001_exec")
            second_show = calls["decide"][1][1]["show_config"]
            for tag in TAGS:
                own = second_show["profiles"][tag]["experience"]
                self.assertIn("OWN_LESSON_" + tag, own)
                for other in set(TAGS) - {tag}:
                    self.assertNotIn("OWN_LESSON_" + other, own)
            routes = calls["lessons"][0][1]
            self.assertIsNotNone(routes)
            self.assertEqual(routes["ITA"]["model"], "italian-player-model")
            self.assertEqual(routes["GER"]["model"], "default-model")

    def test_export_failure_resume_does_not_reobserve_redecide_or_replay_game(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            stack, calls = self.run_fixture(root, fail_export=True)
            with stack:
                with self.assertRaisesRegex(RuntimeError, "export disk failure"):
                    campaign.run_campaign("fixture", turns=1)
                self.assertEqual(len(calls["children"]), 1)
                result = campaign.run_campaign("fixture", resume=True, turns=1)
            self.assertEqual(result["turns"][0]["status"], "completed")
            self.assertEqual(len(calls["children"]), 1)
            self.assertEqual(len(calls["prepare"]), 1)
            self.assertEqual(len(calls["decide"]), 1)
            knowledge = campaign.read(root / "runs/campaigns/fixture/knowledge/GER.json")
            self.assertEqual(len(knowledge), 1)

    def test_lesson_failure_keeps_completed_game_and_exports_facts(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            value = config()
            value["model_commentary"] = False
            stack, calls = self.run_fixture(root, value, fail_lessons=True)
            with stack:
                result = campaign.run_campaign("fixture", turns=1)
                self.assertEqual(result["turns"][0]["status"], "completed")
                self.assertIn("transport failure", result["turns"][0]["lessons_error"])
                campaign.run_campaign("fixture", resume=True, turns=1)
            self.assertEqual(len(calls["children"]), 2)
            self.assertEqual(len(calls["prepare"]), 2)
            self.assertEqual(len(calls["decide"]), 2)
            self.assertEqual(len(calls["export"]), 2)
            self.assertEqual(calls["commentary"], [])


if __name__ == "__main__":
    unittest.main()
