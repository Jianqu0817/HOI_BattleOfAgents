"""Native episode records retain truthful execution status and outcomes."""
import json
from pathlib import Path
import tempfile
import unittest

from episode import collect_episode


class NativeEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trial, self.turn = self.root / "trial", self.root / "turn"
        self.tags = ["GER", "ITA"]
        self.requests = {}
        for tag in self.tags:
            value = {"country": tag, "turn_id": 3, "match_id": "native-test", "date": "1936.1.1.12",
                     "observation_id": tag, "reason": "提交理由 " + tag, "actions": []}
            self.requests[tag] = value
            self.put(self.trial / "private_decisions" / f"{tag}.json", value)
        self.put(self.turn / "result.json", {
            "execution_backend": "native", "match_id": "native-test", "turn_id": 3,
            "countries": self.tags, "status": "completed_native_turn", "requests": self.requests,
            "actions": [], "before_save": {"parsed_date": "1936.1.1.12"},
            "final_save": {"name": "native-save.json", "path": "C:/game/saves/native-save.json", "parsed_date": "1936.1.16.12"},
            "elapsed_game_hours": 360, "native_after": {
                "GER": {"date": "1936.1.16.12", "date_raw": "1936.1.16.12",
                        "divisions": [{"id": 21, "location": 300, "path": [301, 302]}],
                        "army": {"wars": [{"enemy": "ITA"}], "division_count": 1},
                        "naval_air": {"air_wings": [{"id": 4, "current": 1, "region": 2, "capability_mask": 3}],
                                      "task_forces": [{"id": 7, "current": 1, "fleet_id": 2, "regions": [10, 11]}]}},
                "ITA": {"date": "1936.1.16.12", "divisions": [], "army": {"wars": [], "division_count": 0},
                        "naval_air": {"air_wings": [], "task_forces": []}},
            }})

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def result(self):
        path = self.turn / "result.json"
        return path, json.loads(path.read_text(encoding="utf-8"))

    def test_native_move_reports_ordered_path_without_claiming_arrival(self):
        path, result = self.result()
        result["actions"] = [{"country": "GER", "action": {"id": "move", "type": "division.move",
                                "args": {"unit_id": 21, "source": 300, "target": 301}},
                              "status": "applied", "effect_status": "ordered",
                              "verification": {"verified": True, "checks": {"native_state": True},
                                                "observations": {"path": [301, 302]}}}]
        self.put(path, result)
        execution = next(row for row in collect_episode(self.trial, self.turn)["events"] if row["kind"] == "execution")
        self.assertIn("移动命令已下达（原生状态核验；尚未确认抵达）", execution["text"])
        self.assertIn("师 21", execution["text"])
        self.assertNotIn("存档核验", execution["text"])

    def test_native_after_emits_readback_outcomes_with_actionable_state(self):
        events = collect_episode(self.trial, self.turn)["events"]
        outcome = next(row for row in events if row["kind"] == "outcome" and row["country"] == "GER")
        self.assertIn("原生状态回读", outcome["text"])
        self.assertIn("交战关系：ITA", outcome["text"])
        self.assertIn("师位置：21 位于 300（路径：301 → 302）", outcome["text"])
        self.assertIn("空域 2，任务掩码 1", outcome["text"])
        self.assertIn("所属舰队 2，任务类型 1", outcome["text"])
        self.assertEqual(outcome["source"]["file"], "turn/result.json")
        self.assertEqual(outcome["source"]["json_pointer"], "/native_after/GER")

    def test_native_private_sealed_decisions_remain_country_scoped_and_exact(self):
        events = collect_episode(self.trial, self.turn)["events"]
        decisions = [row for row in events if row["kind"] == "decision"]
        self.assertEqual([row["country"] for row in decisions], self.tags)
        self.assertTrue(all(row["visibility"] == "private" for row in decisions))
        self.assertEqual([row["data"]["reason"] for row in decisions], ["提交理由 GER", "提交理由 ITA"])
        changed = dict(self.requests["GER"], reason="不匹配的理由")
        self.put(self.trial / "private_decisions/GER.json", changed)
        with self.assertRaises(ValueError):
            collect_episode(self.trial, self.turn)


if __name__ == "__main__":
    unittest.main()
