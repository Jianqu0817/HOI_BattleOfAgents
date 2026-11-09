"""Native integration must preserve cached chat and sealed decision approvals."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_trial import call_country, compact_native_context, digest, fit_player_prompt, validate_trial_request, native_brief
from native_campaign import verify_decisions, require_no_pending_choice
from native_observation import IDENTITY
from showrunner import player_prompt_limits, settings
from test_native_observation import observe
from test_showrunner import CONFIG


class NativeCampaignTests(unittest.TestCase):
    def test_model_budget_starts_trimming_before_rejecting_required_input(self):
        show = {"player_prompt_bytes": 5000, "player_prompt_limits": {"qwen": {"trim_bytes": 600, "max_bytes": 1500}}}
        payload = {"own_observation": {"own_memory": {"records": [{"turn_id": 1, "text": "旧" * 60}]}},
                   "own_private_diplomacy": "信" * 250}
        messages = [{"role": "user", "content": "规则"}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        self.assertEqual(fit_player_prompt(copy.deepcopy(messages), *player_prompt_limits(show, "another")), messages)
        fitted = fit_player_prompt(copy.deepcopy(messages), *player_prompt_limits(show, "qwen"))
        body = json.loads(fitted[1]["content"])
        self.assertEqual(body["own_observation"]["own_memory"]["records"], [])
        self.assertEqual(body["own_private_diplomacy"], payload["own_private_diplomacy"])
        self.assertGreater(len(fitted[1]["content"].encode("utf-8")), 600)
        payload["own_private_diplomacy"] *= 3
        messages[1]["content"] = json.dumps(payload, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, "exceeds 1500"):
            fit_player_prompt(messages, *player_prompt_limits(show, "qwen"))

    def test_tight_budget_keeps_current_orders_mail_and_commitments(self):
        memory = {"records": [{"turn_id": 1, "private_letters": ["旧信" * 900]}],
                  "retained_turn_count": 1, "retained_letter_count": 1}
        payload = {"own_observation": {"identity": {"country": "AUS"}, "action_options": ["合法动作"], "own_memory": memory},
                   "own_private_diplomacy": ["必须答复的私信"], "own_commitment_memory": ["明天到期"]}
        messages = [{"role": "system", "content": "规则"},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        result = json.loads(fit_player_prompt(messages, 500)[1]["content"])
        self.assertEqual(result["own_observation"]["own_memory"]["retained_letter_count"], 0)
        self.assertEqual(result["own_observation"]["action_options"], ["合法动作"])
        self.assertEqual(result["own_private_diplomacy"], payload["own_private_diplomacy"])
        self.assertEqual(result["own_commitment_memory"], payload["own_commitment_memory"])
        with self.assertRaisesRegex(ValueError, "exceeds"):
            fit_player_prompt(messages, 20)

    def test_known_unresolved_event_stops_before_paid_planning(self):
        require_no_pending_choice({"pending_events": {"availability": "partial", "pending": None}})
        require_no_pending_choice({"pending_events": {"availability": "partial", "pending": []}})
        with self.assertRaisesRegex(RuntimeError, "before model calls"):
            require_no_pending_choice({"pending_events": {"availability": "partial", "pending": [{"country": "AUS"}]}})

    def test_context_compaction_keeps_speech_conditions_and_current_action_menu(self):
        promise = {'country':'AUS','to':'GER','text':'逐字承诺','due_turn':3,'condition':'条件','visibility':'private','status':'unverified','source':{'file':'large/provenance/path'}}
        speech = {'country':'GER','to':'AUS','message':'完整原话不截断','commitments':[promise]}
        menu = {'division.move': [{'id':'candidate','type':'division.move','args':{'unit_id':1,'source':10,'target':11}}]}
        payload = {'own_observation':{'action_options':menu,'recent_private_history':[
            {'own_decision':{'country':'AUS','actions':[],'reason':'理由','observation_id':'oldhash'},
             'public_diplomacy':[speech],'own_private_diplomacy':{'received':[speech]},
             'execution':[{'dispatch':{'province':10,'path':[11],'division':'RAW_POINTER'}}]}]},
            'own_commitment_memory':[promise,copy.deepcopy(promise)]}
        compact = compact_native_context(copy.deepcopy(payload))
        self.assertEqual(compact['own_observation']['action_options'], menu)
        self.assertEqual(compact['own_observation']['recent_private_history'][0]['own_private_diplomacy']['received'][0]['message'], speech['message'])
        self.assertEqual(compact['own_commitment_memory'][0]['condition'], promise['condition'])
        self.assertEqual(compact['own_commitment_memory'][0]['due_turn'], 3)
        self.assertEqual(len(compact['own_commitment_memory']), 1)
        self.assertNotIn('RAW_POINTER', json.dumps(compact))
        self.assertNotIn('large/provenance', json.dumps(compact))

    def test_normalization_retains_raw_chat_and_avoids_paid_resampling(self):
        raw = {"country": "AUS", "message": "PUBLIC_POSITION", "actions": [], "commitments_note": None,
               "round": 2, "due_turn": 4, "private_annotation": "NOT_FOR_OTHER_PLAYERS"}
        metadata = {"requested_model": CONFIG["model"], "returned_model": CONFIG["model"]}
        with tempfile.TemporaryDirectory() as folder, patch("model_trial.complete", return_value=(raw, metadata)) as model:
            show = settings({"step_hours": 360}, ["AUS", "GER", "FRA"])
            args = (Path(folder), CONFIG, observe(), "diplomacy", [])
            first = call_country(*args, tags=["AUS", "GER", "FRA"], show_config=show)
            second = call_country(*args, tags=["AUS", "GER", "FRA"], show_config=show)
            self.assertEqual(model.call_count, 1)
            self.assertEqual(first, second)
            self.assertEqual(first, {"country": "AUS", "message": "PUBLIC_POSITION"})
            stored = json.loads((Path(folder) / "model_calls/diplomacy_AUS.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["response"], raw)
            self.assertEqual(stored["normalized_response"], first)

    def test_chat_actions_are_projected_with_a_receipt_and_the_message_still_flows(self):
        raw = {"country": "AUS", "message": "position", "actions": [{"type": "division.move"}]}
        metadata = {"requested_model": CONFIG["model"]}
        with tempfile.TemporaryDirectory() as folder, patch("model_trial.complete", return_value=(raw, metadata)):
            value = call_country(Path(folder), CONFIG, observe(), "diplomacy", [],
                                 tags=["AUS", "GER", "FRA"], show_config=settings({}, ["AUS", "GER", "FRA"]))
            self.assertEqual(value, {"country": "AUS", "message": "position"})
            stored = json.loads((Path(folder) / "model_calls/diplomacy_AUS.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["response"], raw)
        self.assertEqual(stored["chat_actions_projection"],
                         {"method": "chat_is_not_execution_envelope.v1", "rejected_actions": raw["actions"]})
        self.assertEqual(stored["normalizations"], ["actions"])
        self.assertEqual(stored["normalized_response"], value)

    def test_decision_shaped_private_payload_becomes_a_recorded_noop_message(self):
        raw = {"country": "AUS", "actions": [], "reason": "维持现状"}
        metadata = {"requested_model": CONFIG["model"]}
        show = settings({}, ["AUS", "GER", "FRA"])
        show["strict_public_context"] = True
        with tempfile.TemporaryDirectory() as folder, patch("model_trial.complete", return_value=(raw, metadata)):
            value = call_country(Path(folder), CONFIG, observe(), "private", [],
                                 tags=["AUS", "GER", "FRA"], show_config=show)
            self.assertEqual(value, {"country": "AUS", "to": None, "message": ""})
            stored = json.loads((Path(folder) / "model_calls/private_AUS.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["response"], raw)
        self.assertEqual(stored["speech_projection"]["method"], "noop_message.v1")
        self.assertEqual(stored["speech_projection"]["raw"], raw)
        self.assertEqual(stored["normalized_response"], value)

    def test_annotation_repair_is_cached_and_never_changes_the_spoken_message(self):
        raw = {"country": "AUS", "message": "我方不援奥", "commitments": [
            {"text": "法方不援奥", "to": "GER", "due_turn": 2, "condition": "对等"}]}
        fixed = copy.deepcopy(raw); fixed['commitments'][0]['text'] = '我方不援奥'
        metadata = {"requested_model": CONFIG['model']}
        with tempfile.TemporaryDirectory() as folder, patch('model_trial.complete', side_effect=[(raw, metadata), (fixed, metadata)]) as model:
            args = (Path(folder), CONFIG, observe(), 'diplomacy', [])
            kwargs = {'tags': ['AUS', 'GER', 'FRA'], 'show_config': settings({}, ['AUS', 'GER', 'FRA'])}
            self.assertEqual(call_country(*args, **kwargs)['message'], raw['message'])
            self.assertEqual(call_country(*args, **kwargs)['commitments'], fixed['commitments'])
            self.assertEqual(model.call_count, 2)
        changed = copy.deepcopy(fixed); changed['message'] = 'NEW_UNAUTHORIZED_SPEECH'
        with tempfile.TemporaryDirectory() as folder, patch('model_trial.complete', side_effect=[(raw, metadata), (changed, metadata)]):
            with self.assertRaisesRegex(ValueError, 'changed speech'):
                call_country(Path(folder), CONFIG, observe(), 'diplomacy', [], **kwargs)

    def test_count_repair_only_selects_original_commitments(self):
        raw = {'country': 'AUS', 'message': '承诺0，承诺1，承诺2，承诺3',
               'commitments': [{'text': f'承诺{i}', 'to': 'GER', 'due_turn': 2, 'condition': '对等'} for i in range(4)]}
        fixed = copy.deepcopy(raw); fixed['commitments'] = fixed['commitments'][:3]
        metadata = {'requested_model': CONFIG['model']}
        with tempfile.TemporaryDirectory() as folder, patch('model_trial.complete', side_effect=[(raw, metadata), (fixed, metadata)]):
            result = call_country(Path(folder), CONFIG, observe(), 'diplomacy', [], tags=['AUS', 'GER', 'FRA'], show_config=settings({}, ['AUS', 'GER', 'FRA']))
            self.assertEqual(result['message'], raw['message'])
            self.assertEqual(result['commitments'], raw['commitments'][:3])

    def test_native_decision_uses_exact_candidate_and_excludes_unactionable_units(self):
        observed = observe()
        action = copy.deepcopy(observed["action_options"]["air.mission"][0])
        request = {**{key: observed[key] for key in IDENTITY}, "actions": [action], "reason": "本国计划"}
        self.assertEqual(validate_trial_request(request, observed), [action])
        request["actions"][0]["args"]["wing_id"] = 999
        with self.assertRaises(ValueError): validate_trial_request(request, observed)
        brief = native_brief(observed)
        self.assertEqual(brief["division_count"], 2)
        self.assertEqual(brief["divisions_with_available_orders"], [])

    def test_referee_hash_binds_the_exact_executed_sealed_decision(self):
        observed = {"AUS": observe()}
        request = {**{key: observed["AUS"][key] for key in IDENTITY}, "actions": [], "reason": "保持"}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("requests", "private_decisions"):
                (root / name).mkdir()
                (root / name / "AUS.json").write_text(json.dumps(request), encoding="utf-8")
            result = {"status": "approved", "referee": {"reviews": [{"country": "AUS", "approved": True, "request_sha256": digest(request)}]}}
            (root / "decision_result.json").write_text(json.dumps(result), encoding="utf-8")
            self.assertEqual(verify_decisions(root, observed)["AUS"], request)
            request["reason"] = "CHANGED_AFTER_REVIEW"
            (root / "requests/AUS.json").write_text(json.dumps(request), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed after"):
                verify_decisions(root, observed)


if __name__ == "__main__":
    unittest.main()
