"""Audience export, source fidelity, subtitle timing and commentary boundaries."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from episode import collect_episode, export_episode, generate_commentary, subtitle_timeline, validate_commentary, _render_html


class EpisodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trial, self.turn = self.root / "trial", self.root / "turn"
        self.tags = ["GER", "ITA", "JAP"]
        self.requests = {}
        for tag in self.tags:
            value = {"country": tag, "turn_id": 1, "match_id": "test", "date": "1936.1.1.12", "observation_id": tag,
                     "reason": "本国提交理由", "actions": []}
            self.requests[tag] = value
            self.put(self.trial / "private_decisions" / (tag + ".json"), value)
            self.put(self.trial / "observations" / (tag + ".json"), {"political_power": 10, "divisions": []})
        self.put(self.trial / "public_diplomacy.json", [{"country": "GER", "round": 1, "message": "<script>alert('x')</script> & 外交"}])
        self.put(self.trial / "private_intents/GER.json", {"country": "GER", "goal": "秘密目标", "plan": "保存兵力", "concealment": "隐瞒谈判底线", "predictions": []})
        letter = {"country": "GER", "to": "ITA", "message": "秘密来信"}
        reply = {"country": "ITA", "to": "GER", "message": "秘密回复", "kind": "reply"}
        self.put(self.trial / "private_diplomacy/GER.json", {"sent": [letter], "received": [reply]})
        self.put(self.trial / "private_diplomacy/ITA.json", {"sent": [reply], "received": [letter]})
        self.put(self.turn / "result.json", {"match_id": "test", "turn_id": 1, "countries": self.tags,
                 "status": "completed_deterministic_turn", "requests": self.requests, "actions": [],
                 "before_save": {"parsed_date": "1936.1.1.12"}, "elapsed_game_hours": 360})

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def commentary(self, episode):
        first = episode["events"][0]
        return {"title": "本轮点评", "facts": [{"evidence_id": first["id"], "quote": "秘密目标"}],
                "analysis": [{"text": "这是一条事前目标记录，未证明已经实现。", "evidence_ids": [first["id"]]}],
                "jokes": [], "highlights": []}

    def test_export_is_explicitly_omniscient_and_escapes_model_text(self):
        output = self.root / "episode"
        summary = export_episode(self.trial, self.turn, output)
        index = (output / "index.html").read_text(encoding="utf-8")
        manifest = json.loads((output / "episode.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["audience"], "audience_full")
        self.assertEqual(manifest["audience"], "audience_full")
        self.assertIn("秘密来信", index)
        self.assertIn("不是模型隐藏思维链", index)
        self.assertIn("&lt;script&gt;", index)
        self.assertNotIn("<script>alert", index)
        for name in ("subtitles.srt", "timeline.csv", "timeline.jsonl", "narration.md", "shotlist.md", "assets.json"):
            self.assertTrue((output / name).is_file())
        self.assertEqual(summary["commentary"], "deterministic_overview")

    def test_private_messages_are_not_duplicated_and_replies_follow_initial_mail(self):
        events = collect_episode(self.trial, self.turn)["events"]
        mail = [event for event in events if event["kind"] in {"private", "private_reply"}]
        self.assertEqual([event["text"] for event in mail], ["秘密来信", "秘密回复"])
        self.assertTrue(all(event["visibility"] == "private" for event in mail))
        self.put(self.trial / "private_diplomacy/ITA.json", {"sent": [{"country": "GER", "to": "JAP", "message": "伪造发件"}]})
        with self.assertRaises(ValueError):
            collect_episode(self.trial, self.turn)

    def test_war_declaration_distinguishes_new_war_existing_war_and_failed_adapter(self):
        path = self.turn / "result.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        action = {"id": "declare", "type": "diplomacy.declare_war", "args": {"target_country": "ITA", "wargoal_id": 3}}
        proof = {"verified": True, "effect_status": "full", "checks": {"same_date": True, "peers_unchanged": True},
                 "observations": {"actor": "GER", "target_country": "ITA", "wargoal_id": 3, "new_war": True, "declaration_sent": True, "war_pairs_added": [["GER", "ITA"]]}}
        result["actions"] = [{"country": "GER", "action": action, "status": "applied", "effect_status": "full", "verification": proof}]
        self.put(path, result)
        episode = collect_episode(self.trial, self.turn)
        wars = [row for row in episode["events"] if row["kind"] == "war"]
        self.assertEqual(len(wars), 1)
        self.assertEqual(wars[0]["participants"], ["GER", "ITA"])
        self.assertIn("被宣战方：意大利", wars[0]["text"])
        proof.update(effect_status="already_present")
        proof["observations"].update(new_war=False, declaration_sent=False, war_pairs_added=[])
        result["actions"][0].update(effect_status="already_present")
        self.put(path, result)
        episode = collect_episode(self.trial, self.turn)
        self.assertFalse(any(row["kind"] == "war" for row in episode["events"]))
        self.assertIn("未重复宣战", next(row["text"] for row in episode["events"] if row["kind"] == "execution"))
        result["actions"][0].update(status="not_applied", effect_status="none", ui_error="adapter failed", verification={"verified": False})
        self.put(path, result)
        episode = collect_episode(self.trial, self.turn)
        self.assertFalse(any(row["kind"] == "war" for row in episode["events"]))
        self.assertIn("不能直接认定为玩家反悔", next(row["text"] for row in episode["events"] if row["kind"] == "execution"))

    def test_sealed_order_must_match_actual_request(self):
        value = copy.deepcopy(self.requests["GER"])
        value["reason"] = "改写过的理由"
        self.put(self.trial / "private_decisions/GER.json", value)
        with self.assertRaises(ValueError):
            collect_episode(self.trial, self.turn)

    def test_commentary_facts_quote_real_evidence_and_every_judgment_cites_it(self):
        episode = collect_episode(self.trial, self.turn)
        valid = self.commentary(episode)
        validate_commentary(valid, episode)
        bad = copy.deepcopy(valid)
        bad["facts"][0]["quote"] = "凭空编造的背叛"
        with self.assertRaises(ValueError):
            validate_commentary(bad, episode)
        bad = copy.deepcopy(valid)
        bad["analysis"][0]["evidence_ids"] = ["MISSING"]
        with self.assertRaises(ValueError):
            validate_commentary(bad, episode)

    def test_subtitles_have_positive_monotonic_durations_and_valid_event_references(self):
        episode = collect_episode(self.trial, self.turn)
        cues, chapters = subtitle_timeline(episode["events"])
        self.assertEqual(cues[0]["start"], 0)
        for previous, current in zip(cues, cues[1:]):
            self.assertGreaterEqual(current["start"], previous["end"])
        self.assertTrue(all(cue["end"] > cue["start"] and len(cue["text"].splitlines()) <= 2 for cue in cues))
        self.assertEqual({chapter["evidence_id"] for chapter in chapters}, {event["id"] for event in episode["events"]})

    def test_commentary_cache_is_audience_only_and_reuses_identical_evidence(self):
        response = self.commentary(collect_episode(self.trial, self.turn))
        config = {"base_url": "https://example.invalid/v1", "model": "fixture", "temperature": .3, "max_tokens": 1000, "enable_thinking": False}
        with patch("episode.complete", return_value=(response, {"requested_model": "fixture", "usage": {"total_tokens": 10}})) as call:
            first = generate_commentary(self.trial, self.turn, config)
            second = generate_commentary(self.trial, self.turn, config)
            self.assertEqual(first, second)
            self.assertEqual(call.call_count, 1)
        self.assertEqual(first["audience"], "audience_full")
        self.assertFalse((self.trial / "observations/GER.json").read_text(encoding="utf-8").find("本轮点评") >= 0)
        changed = {**config, "base_url": "https://different.invalid/v1"}
        with self.assertRaises(ValueError):
            generate_commentary(self.trial, self.turn, changed)

    def test_unrelated_output_is_not_overwritten(self):
        output = self.root / "existing"
        output.mkdir()
        (output / "notes.txt").write_text("保留", encoding="utf-8")
        with self.assertRaises(ValueError):
            export_episode(self.trial, self.turn, output)
        self.assertEqual((output / "notes.txt").read_text(encoding="utf-8"), "保留")

    def test_factual_fallback_can_explicitly_ignore_failed_cached_commentary(self):
        self.put(self.trial / "audience_commentary/commentary.json", {"invalid": "old result"})
        with self.assertRaises(ValueError):
            export_episode(self.trial, self.turn, self.root / "failed")
        result = export_episode(self.trial, self.turn, self.root / "facts", load_cached_commentary=False)
        self.assertEqual(result["commentary"], "deterministic_overview")

    def test_actual_recording_markers_are_separate_from_editorial_time(self):
        self.put(self.trial / "model_calls/intent_GER.json", {"recorded_at": "2026-11-08T03:00:00+00:00"})
        output = self.root / "markers"
        export_episode(self.trial, self.turn, output, production_markers=[{"turn": 7, "phase": "prepare_start", "recorded_at": "2026-11-08T02:59:00+00:00", "epoch": 1794106740}])
        with (output / "recording_markers.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["campaign_turn"], "7")
        self.assertEqual(rows[0]["suggested_editorial_start"], "")
        self.assertEqual(rows[1]["recorded_at_utc"], "2026-11-08T03:00:00+00:00")
        self.assertEqual(float(rows[1]["suggested_editorial_start"]), 0)
        self.assertNotIn("mtime", rows[0])

    def test_partial_asset_export_can_resume_only_for_the_bound_source(self):
        image = self.root / "source.png"
        image.write_bytes(b"fixture image")
        self.put(self.trial / "observations/GER.json", {"country": "GER", "political_power": 10, "divisions": [], "research": {"screen_evidence": str(image)}})
        output = self.root / "partial"
        with patch("episode.shutil.copy2", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                export_episode(self.trial, self.turn, output)
        self.assertTrue((output / "export_owner.json").exists())
        self.assertFalse((output / "episode.json").exists())
        self.assertEqual(export_episode(self.trial, self.turn, output)["assets"], 1)
        result = json.loads((self.turn / "result.json").read_text(encoding="utf-8"))
        result["match_id"] = "different-match"
        self.put(self.turn / "result.json", result)
        with self.assertRaises(ValueError):
            export_episode(self.trial, self.turn, output)

    def test_html_simplifies_predictions_and_folds_technical_checks_without_mutating_evidence(self):
        self.put(self.trial / "private_intents/GER.json", {"country": "GER", "goal": "秘密目标", "plan": "保存兵力", "concealment": "底线", "predictions": [{"subject": "战线", "prediction": "预计守住目标", "confidence": .7}]})
        episode = collect_episode(self.trial, self.turn)
        execution = {"id": "T1-EXAMPLE", "kind": "execution", "country": "GER", "to": None, "participants": ["GER"], "visibility": "public", "game_date": "1936.1.1.12", "title": "实际执行", "text": "已生效\n核验：same_date ✓；peers_unchanged ✓", "source": {"file": "turn/result.json"}, "data": {}}
        episode["events"].append(execution)
        before = copy.deepcopy(episode)
        rendered = _render_html(episode)
        self.assertIn("预测 · 战线：预计守住目标（信心 70%）", rendered)
        card = rendered.split('id="T1-EXAMPLE"', 1)[1].split("</article>", 1)[0]
        self.assertNotIn("same_date", card.split("<details>", 1)[0])
        self.assertIn("same_date", card.split("<details>", 1)[1])
        self.assertEqual(episode, before)


if __name__ == "__main__":
    unittest.main()
