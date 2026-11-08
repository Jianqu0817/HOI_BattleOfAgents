"""Synthetic reconciliation tests: no live process or native mutations."""
import json
from pathlib import Path
import tempfile
import unittest

from native_turn import NativeTurn
from test_native_turn import FakeGame, plan


class ResumeTests(unittest.TestCase):
    def interrupted(self, directory):
        game, candidate = FakeGame(), plan()
        candidate["step_hours"] = 13
        initial_date = game.date
        gate = {"pending": True, "unknown": False}

        def pending(turn, controls):
            return {"availability": "supported", "pending": None if gate["unknown"] else
                    ([{"id": "decision"}] if gate["pending"] and turn.date_raw > initial_date else [])}

        def trajectory(turn, controls):
            return {"date_raw": turn.date_raw, "public_wars": [{"first": "AUS", "second": "GER"}],
                    "actions": {"move": {"country": "AUS", "available": True,
                                           "observed": {"province": game.province, "path": game.path.copy()}}}}

        output = Path(directory) / "turn"
        options = dict(event_provider=pending, trajectory_provider=trajectory, poll_interval=0)
        result = NativeTurn(game, candidate, output, **options).run()
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["clock_interruption"]["remaining_budget_hours"], 7)
        return game, candidate, output, gate, options, result

    @staticmethod
    def facts(output):
        return [json.loads(line) for line in (output / "audience_facts.jsonl").read_text().splitlines()]

    def test_resume_exact_remainder_and_history_without_replaying_orders(self):
        with tempfile.TemporaryDirectory() as directory:
            game, candidate, output, gate, options, interrupted = self.interrupted(directory)
            # Deliberate harmless formatting ensures the original sealed bytes survive.
            path = output / "plan.json"
            path.write_bytes(b"\n" + path.read_bytes() + b"\n")
            sealed_bytes, old_result = path.read_bytes(), (output / "result.json").read_bytes()
            before_calls = len(game.calls)
            original_facts = self.facts(output)
            last_sequence = max(int(row["request_id"].split(".")[1]) for row in original_facts if "request_id" in row)
            gate["pending"] = False
            resumed = NativeTurn(game, candidate, output, **options)
            result = resumed.resume_interrupted()
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["run_id"], interrupted["run_id"])
            self.assertEqual(result["step_hours"], 13)
            self.assertEqual(result["remaining_budget_hours"], 0)
            self.assertEqual(path.read_bytes(), sealed_bytes)
            self.assertEqual((output / "result.interrupted.0001.json").read_bytes(), old_result)
            self.assertEqual([p for op, p, _ in game.calls if op == "native_clock_v1" and p.startswith("step ")], ["step 6", "step 6", "step 1"])
            self.assertEqual(sum(p.startswith("post_unit ") for _, p, _ in game.calls), 1)
            self.assertFalse(any(p.startswith("ai ") or p.startswith("build_") for _, p, _ in game.calls[before_calls:]))
            self.assertTrue(all(int(request_id.split(".")[1]) > last_sequence for _, _, request_id in game.calls[before_calls:]))
            self.assertEqual(len({request_id for _, _, request_id in game.calls}), len(game.calls))
            self.assertTrue(all(request_id.startswith(interrupted["run_id"] + ".") for _, _, request_id in game.calls))
            self.assertEqual(result["effects"][0]["first_arrival"], interrupted["first_arrivals"]["move"])
            self.assertIsNone(result["early_end"])
            self.assertEqual(resumed.boundary_wars, {("AUS", "GER")})
            facts = self.facts(output)
            reconciled = next(i for i, row in enumerate(facts) if row["event"] == "resume_reconciled")
            next_step = next(i for i, row in enumerate(facts) if i > reconciled and row["event"] == "intent" and row.get("payload", "").startswith("step "))
            self.assertLess(reconciled, next_step)
            self.assertEqual(facts[reconciled]["remaining_budget_hours"], 7)
            calls = game.calls.copy()
            cached = NativeTurn(game, candidate, output, **options).resume_interrupted()
            self.assertEqual(cached, result)
            self.assertEqual(game.calls, calls)

    def test_pending_remains_interrupted_then_clears_with_fresh_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            game, candidate, output, gate, options, original = self.interrupted(directory)
            before = len(game.calls)
            self.assertEqual(NativeTurn(game, candidate, output, **options).resume_interrupted(), original)
            self.assertTrue(all(p.startswith("status ") or p == "status" or op == "main_status" for op, p, _ in game.calls[before:]))
            self.assertFalse(list(output.glob("result.interrupted.*.json")))
            gate["pending"] = False
            self.assertEqual(NativeTurn(game, candidate, output, **options).resume_interrupted()["step_hours"], 13)
            self.assertEqual(sum(p.startswith("post_unit ") for _, p, _ in game.calls), 1)

    def test_second_interruption_keeps_original_budget_and_both_result_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            game, candidate, output, gate, options, original = self.interrupted(directory)
            first_date = game.date
            gate["pending"] = False
            second_gate = {"pending": True}
            options["event_provider"] = lambda turn, controls: {
                "availability": "supported", "pending": [{"id": "second"}]
                if second_gate["pending"] and turn.date_raw >= first_date + 6 else []}
            second = NativeTurn(game, candidate, output, **options).resume_interrupted()
            self.assertEqual(second["status"], "interrupted")
            self.assertEqual(second["clock_interruption"]["observed_hour_delta"], 12)
            self.assertEqual(second["clock_interruption"]["remaining_budget_hours"], 1)
            second_gate["pending"] = False
            completed = NativeTurn(game, candidate, output, **options).resume_interrupted()
            self.assertEqual(completed["step_hours"], 13)
            self.assertEqual(json.loads((output / "result.interrupted.0001.json").read_text()), original)
            self.assertEqual(json.loads((output / "result.interrupted.0002.json").read_text()), second)
            self.assertEqual([p for op, p, _ in game.calls if op == "native_clock_v1" and p.startswith("step ")], ["step 6", "step 6", "step 1"])
            self.assertEqual(sum(p.startswith("post_unit ") for _, p, _ in game.calls), 1)

    def test_process_date_controller_ai_and_clock_refusals_never_mutate(self):
        for mismatch in ("pid", "creation_time", "date", "controller", "ai", "clock_remaining", "clock_running"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as directory:
                game, candidate, output, gate, options, original = self.interrupted(directory)
                gate["pending"] = False
                if mismatch == "pid": game.pid += 1
                elif mismatch == "creation_time": game.creation_time += 1
                elif mismatch == "date": game.date += 1
                elif mismatch == "controller": game.played = "GER"
                elif mismatch == "ai": game.ai["GER"] = True
                else:
                    call = game.call
                    def changed(op, payload="", **kwargs):
                        result = call(op, payload, **kwargs)
                        if op == "native_clock_v1" and payload == "status":
                            result["state"]["remaining_hours"] = 1 if mismatch == "clock_remaining" else 0
                            result["state"]["paused"] = mismatch != "clock_running"
                        return result
                    game.call = changed
                before = len(game.calls)
                with self.assertRaises(RuntimeError): NativeTurn(game, candidate, output, **options).resume_interrupted()
                self.assertTrue(all(p.startswith("status ") or p == "status" or op == "main_status" for op, p, _ in game.calls[before:]))
                self.assertEqual(json.loads((output / "result.json").read_text()), original)

    def test_unknown_pending_is_not_treated_as_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            game, candidate, output, gate, options, _ = self.interrupted(directory)
            gate.update(pending=False, unknown=True)
            before = len(game.calls)
            with self.assertRaisesRegex(RuntimeError, "unknown"):
                NativeTurn(game, candidate, output, **options).resume_interrupted()
            self.assertFalse(any(p.startswith("step ") for _, p, _ in game.calls[before:]))

    def test_missing_mutation_receipt_effect_or_changed_plan_refuses_before_calls(self):
        for damage in ("receipt", "effect", "plan", "unknown"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as directory:
                game, candidate, output, gate, options, _ = self.interrupted(directory)
                gate["pending"] = False
                facts = self.facts(output)
                order = next(row["request_id"] for row in facts if row["event"] == "intent" and row.get("payload", "").startswith("post_unit "))
                if damage == "receipt": facts = [row for row in facts if not (row["event"] == "receipt" and row["request_id"] == order)]
                elif damage == "effect": facts = [row for row in facts if row["event"] != "action_effect"]
                elif damage == "unknown": facts.append({"event": "stopped", "unknown_outcome": True})
                else: candidate["step_hours"] = 14
                (output / "audience_facts.jsonl").write_text("".join(json.dumps(row) + "\n" for row in facts))
                before = len(game.calls)
                with self.assertRaises(RuntimeError): NativeTurn(game, candidate, output, **options).resume_interrupted()
                self.assertEqual(len(game.calls), before)


if __name__ == "__main__":
    unittest.main()
