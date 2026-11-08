"""Run a frozen, journaled military turn through NativeGame only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from native_game import NativeGame
from native_rpc import NativeRpcError

SCHEMA = "iron.native-turn.v1"
AIR = "native_air_navy_v2"
WAR = "native_diplomacy_v2"
FIELDS = {
    "division.move": {"unit_id", "source", "target"},
    "diplomacy.declare_war": {"recipient", "goal_handle"},
    "air.mission": {"wing_id", "expected_mask", "mission_bit", "enabled"},
    "air.region": {"wing_id", "expected_region", "target_region"},
    "navy.mission": {"task_force_id", "expected_type", "mission_type"},
    "navy.regions": {"task_force_id", "expected_regions", "target_regions"},
}


def _integer(value, *, minimum=1, maximum=0x7fffffff):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"expected integer {minimum}..{maximum}")


def _tag(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise ValueError("country tags must be three uppercase letters")


def validate_plan(plan: dict) -> dict:
    """Validate the entire schema before connecting to or changing the game."""
    if not isinstance(plan, dict) or set(plan) - {"schema", "label", "countries", "step_hours", "disable_ai", "save_basename"}:
        raise ValueError("unknown plan fields")
    if plan.get("schema") != SCHEMA:
        raise ValueError(f"schema must be {SCHEMA}")
    _integer(plan.get("step_hours"), maximum=360)
    if type(plan.get("disable_ai", True)) is not bool:
        raise ValueError("disable_ai must be boolean")
    if "save_basename" in plan and (not isinstance(plan["save_basename"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,70}", plan["save_basename"])):
        raise ValueError("save_basename must contain 1..70 letters, digits, hyphens or underscores")
    countries = plan.get("countries")
    if not isinstance(countries, list) or not 3 <= len(countries) <= 5:
        raise ValueError("a native turn needs 3..5 countries")
    tags, ids, resources = set(), set(), set()
    for country in countries:
        if not isinstance(country, dict) or set(country) != {"tag", "actions"}:
            raise ValueError("country requires exactly tag and actions")
        tag = country["tag"]; _tag(tag)
        if tag in tags:
            raise ValueError("country tags must be distinct")
        tags.add(tag)
        actions = country["actions"]
        if not isinstance(actions, list) or len(actions) > 6:
            raise ValueError("each country allows at most six actions")
        for action in actions:
            if not isinstance(action, dict) or action.get("kind") not in FIELDS:
                raise ValueError("unknown native action kind")
            kind = action["kind"]
            if set(action) != FIELDS[kind] | {"id", "kind"}:
                raise ValueError(f"wrong fields for {kind}")
            action_id = action["id"]
            if not isinstance(action_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", action_id) or action_id in ids:
                raise ValueError("action IDs must be unique 1..40 character names")
            ids.add(action_id)
            for field in ("unit_id", "source", "target", "wing_id", "task_force_id"):
                if field in action: _integer(action[field])
            for field in ("expected_mask", "expected_region", "target_region"):
                if field in action: _integer(action[field], minimum=0, maximum=0xffffffff)
            if kind == "division.move" and action["source"] == action["target"]:
                raise ValueError("move source equals target")
            if kind == "diplomacy.declare_war":
                _tag(action["recipient"])
                if action["recipient"] == tag or not isinstance(action["goal_handle"], str) or not re.fullmatch(r"0x[0-9a-fA-F]{1,16}", action["goal_handle"]) or int(action["goal_handle"], 16) == 0:
                    raise ValueError("declaration needs another country and a nonzero hexadecimal owned goal handle")
            if kind == "air.mission":
                bit = action["mission_bit"]; _integer(bit, maximum=0x80000000)
                if bit & (bit - 1) or type(action["enabled"]) is not bool:
                    raise ValueError("mission_bit must be a single bit and enabled a boolean")
            if kind == "navy.mission":
                for field in ("expected_type", "mission_type"): _integer(action[field], minimum=0, maximum=9)
                if action["mission_type"] == 8:
                    raise ValueError("navy mission type 8 has no normal UI choice")
            if kind == "navy.regions":
                for field in ("expected_regions", "target_regions"):
                    regions = action[field]
                    if not isinstance(regions, list) or len(regions) > 32:
                        raise ValueError("navy regions must be lists of at most 32 IDs")
                    for value in regions: _integer(value)
                    if len(set(regions)) != len(regions): raise ValueError("duplicate region IDs")
            identity = action.get("unit_id", action.get("wing_id", action.get("task_force_id", action.get("recipient"))))
            resource = (tag, kind, identity)
            if resource in resources: raise ValueError("two actions change the same guarded field")
            resources.add(resource)
    return plan


class NativeTurn:
    def __init__(self, game, plan, output: Path, *, save_dir: Path | None = None,
                 timeout: float = 900, poll_interval: float = 0.1):
        self.plan = validate_plan(plan)
        self.game, self.output, self.save_dir = game, Path(output), save_dir
        self.timeout, self.poll_interval = timeout, poll_interval
        self.run_id = uuid.uuid4().hex[:16]
        self.sequence = 0
        self.current = self.initial = None
        self.date_raw = None
        self.events = []

    def event(self, event_type, **data):
        row = {"event": event_type, "utc": datetime.now(timezone.utc).isoformat(), **data}
        self.events.append(row)
        with (self.output / "audience_facts.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    def call(self, op, payload="", *, mutation=False, purpose="read"):
        self.sequence += 1
        request_id = f"{self.run_id}.{self.sequence:05d}.{purpose}"
        self.event("intent", request_id=request_id, operation=op, payload=payload, mutation=mutation)
        result = self.game.call(op, payload, request_id=request_id)
        if isinstance(result, dict) and result.get("ok") is False:
            raise RuntimeError(f"native operation rejected: {result}")
        self.event("receipt", request_id=request_id, operation=op, result=result)
        return result

    def country(self, tag, *, frozen=True, ai_off=False):
        state = self.call("native_control_v1", "status " + tag)["state"]
        if not state["paused"] or (frozen and self.date_raw is not None and state["date_raw"] != self.date_raw):
            raise RuntimeError("game must remain paused at the frozen turn date")
        if ai_off and state["ai_enabled"]:
            raise RuntimeError(f"AI is enabled for {tag}")
        return state

    def control(self, target):
        if target != self.current:
            self.call("native_control_v1", f"tag {self.current} {target}", mutation=True, purpose="control")
            self.current = target
        state = self.country(target)
        if state["played_index"] != state["country_index"]:
            raise RuntimeError("controller switch was not observed")

    @staticmethod
    def payload(tag, action, *, build=False):
        kind = action["kind"]; stage = "build" if build else "post"
        if kind == "division.move":
            return "native_move", f"{stage}_unit {tag} {action['unit_id']} {action['source']} {action['target']}"
        if kind == "diplomacy.declare_war":
            return WAR, f"{stage}_declare {tag} {action['recipient']} {action['goal_handle']}"
        if kind == "air.mission":
            return AIR, f"air_{stage} {tag} {action['wing_id']} {action['expected_mask']} {action['mission_bit']} {int(action['enabled'])}"
        if kind == "air.region":
            return AIR, f"air_region_{stage} {tag} {action['wing_id']} {action['expected_region']} {action['target_region']}"
        if kind == "navy.mission":
            return AIR, f"navy_{stage} {tag} {action['task_force_id']} {action['expected_type']} {action['mission_type']}"
        def regions(values): return ",".join(map(str, values)) or "-"
        return AIR, f"navy_regions_{stage} {tag} {action['task_force_id']} {regions(action['expected_regions'])} {regions(action['target_regions'])}"

    def observe(self, tag, action):
        kind = action["kind"]
        if kind == "division.move":
            return self.call("native_move", f"inspect_unit {tag} {action['unit_id']}")
        if kind == "diplomacy.declare_war":
            return self.call(WAR, f"read {tag} {action['recipient']}")
        air = kind.startswith("air.")
        rows = self.call(AIR, ("air_list " if air else "navy_list ") + tag)["units"]
        wanted = action["wing_id"] if air else action["task_force_id"]
        selected = [unit for unit in rows if unit["id"] == wanted]
        if len(selected) != 1: raise RuntimeError("unit ID is absent, ambiguous, or not owned by the requested country")
        return selected[0]

    @staticmethod
    def check_baseline(action, state):
        kind = action["kind"]
        if kind == "division.move":
            valid = state["province"] == action["source"] and not state["path"]
        elif kind == "diplomacy.declare_war":
            valid = not state["at_war"] and any(int(goal["handle"], 16) == int(action["goal_handle"], 16) and goal["available"] and goal["actor_index"] == state["actor_index"] and goal["recipient_index"] == state["recipient_index"] for goal in state["goals"])
        elif kind == "air.mission":
            valid = state["current"] == action["expected_mask"] and (not action["enabled"] or bool(state["capability_mask"] & action["mission_bit"]))
        elif kind == "air.region": valid = state["region"] == action["expected_region"]
        elif kind == "navy.mission": valid = state["current"] == action["expected_type"]
        else: valid = state["regions"] == action["expected_regions"]
        if not valid: raise RuntimeError(f"stale or invalid action baseline: {action['id']}")

    @staticmethod
    def effect(action, state, *, after_clock=False):
        kind = action["kind"]
        if kind == "division.move":
            return state["province"] == action["target"] if after_clock else state["province"] == action["target"] or action["target"] in state["path"]
        if kind == "diplomacy.declare_war": return state["at_war"] and state["shared_war_relation_count"] > 0
        if kind == "air.mission":
            # The engine may clear mutually exclusive missions while setting this bit.
            return bool(state["current"] & action["mission_bit"]) == action["enabled"]
        if kind == "air.region": return state["region"] == action["target_region"]
        if kind == "navy.mission": return state["current"] == action["mission_type"]
        return state["regions"] == action["target_regions"]

    def preflight(self):
        states = {country["tag"]: self.country(country["tag"]) for country in self.plan["countries"]}
        dates = {state["date_raw"] for state in states.values()}
        played = {state["played_index"] for state in states.values()}
        if len(dates) != 1 or len(played) != 1: raise RuntimeError("country observations do not share one frozen date and controller")
        if len({state["country_index"] for state in states.values()}) != len(states):
            raise RuntimeError("country tags resolved to duplicate native identities")
        self.date_raw = dates.pop()
        initial = [tag for tag, state in states.items() if state["country_index"] == next(iter(played))]
        if len(initial) != 1: raise RuntimeError("initial played country must appear exactly once in the plan")
        self.current = self.initial = initial[0]
        self.event("frozen_initial", countries=states, initial_country=self.initial)
        if not self.plan.get("disable_ai", True) and any(state["ai_enabled"] for state in states.values()):
            raise RuntimeError("all planned countries must have AI disabled")
        fleet_regions = set()
        for country in self.plan["countries"]:
            tag = country["tag"]; self.control(tag)
            inputs = {"divisions": self.call("native_move", "units " + tag),
                      "air": self.call(AIR, "air_list " + tag), "navy": self.call(AIR, "navy_list " + tag)}
            self.event("country_inputs", country=tag, inputs=inputs, date_raw=self.date_raw)
            for action in country["actions"]:
                observed = self.observe(tag, action); self.check_baseline(action, observed)
                if action["kind"] == "navy.regions":
                    key = (tag, observed["fleet_id"])
                    if key in fleet_regions: raise RuntimeError("two region actions change the same fleet")
                    fleet_regions.add(key)
                op, payload = self.payload(tag, action, build=True)
                built = self.call(op, payload, purpose="construct")
                if not built.get("constructed_verified") or ("can_execute" in built and not built["can_execute"]):
                    raise RuntimeError(f"native construction or CanExecute rejected {action['id']}")
                self.event("preflight_action", country=tag, action=action, observed=observed)
        self.control(self.initial)
        self.event("preflight_complete", date_raw=self.date_raw)
        for tag in states:
            state = self.country(tag)
            if state["ai_enabled"]:
                if not self.plan.get("disable_ai", True): raise RuntimeError("AI changed during preflight")
                self.call("native_control_v1", f"ai {tag} 1 0", mutation=True, purpose="disable_ai")
        for tag in states: self.country(tag, ai_off=True)
        self.event("all_ai_disabled", countries=list(states), date_raw=self.date_raw)

    def execute_actions(self):
        for country in self.plan["countries"]:
            tag = country["tag"]; self.control(tag)
            for action in country["actions"]:
                self.country(tag, ai_off=True)
                self.check_baseline(action, self.observe(tag, action))
                op, payload = self.payload(tag, action)
                receipt = self.call(op, payload, mutation=True, purpose="order")
                if not receipt.get("queue_accepted") or not receipt.get("posted"):
                    raise RuntimeError("native queue did not acknowledge the order")
                deadline = time.monotonic() + 3
                while True:
                    observed = self.observe(tag, action)
                    verified = self.effect(action, observed)
                    if verified or time.monotonic() >= deadline: break
                    time.sleep(self.poll_interval)
                self.event("action_effect", country=tag, action=action, queue_acknowledged=True,
                           effect_verified=verified, observed=observed)
                if not verified and action["kind"] != "division.move":
                    raise RuntimeError(f"order effect was not observed: {action['id']}")
        self.control(self.initial)

    def advance(self):
        hours = self.plan["step_hours"]
        self.country(self.initial, ai_off=True)
        self.call("native_clock_v1", f"step {hours}", mutation=True, purpose="step")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            state = self.call("native_control_v1", "status " + self.initial)["state"]
            clock = self.call("native_clock_v1", "status")["state"]
            delta = state["date_raw"] - self.date_raw
            if delta < 0 or delta > hours: raise RuntimeError("native clock advanced outside its requested bound")
            if state["paused"] and delta == hours and clock["remaining_hours"] == 0:
                self.date_raw = state["date_raw"]
                self.event("clock_effect", requested_hours=hours, observed_hour_delta=delta, state=state)
                return
            if state["paused"] and 0 < delta < hours: raise RuntimeError("game paused before the requested bound")
            time.sleep(self.poll_interval)
        raise TimeoutError("clock receipt exists but bounded completion is unobserved; inspect journal before continuing")

    def final_observations(self):
        results = []
        for country in self.plan["countries"]:
            tag = country["tag"]; self.control(tag)
            self.country(tag, ai_off=True)
            for action in country["actions"]:
                observed = self.observe(tag, action)
                result = {"country": tag, "action_id": action["id"], "kind": action["kind"],
                          "effect_observed_after_clock": self.effect(action, observed, after_clock=True), "observed": observed}
                results.append(result); self.event("after_clock_effect", **result)
        self.control(self.initial)
        basename = self.plan.get("save_basename", "iron_turn") + "_" + self.run_id
        saved = self.call("native_control_v1", "save " + basename, mutation=True, purpose="save")
        file_result = {"basename": basename, "handler_ack": saved, "file_verified": False}
        if self.save_dir is not None:
            path = Path(self.save_dir) / (basename + ".hoi4")
            deadline = time.monotonic() + 15; prior = None
            while time.monotonic() < deadline:
                stat = path.stat() if path.is_file() else None
                current = (stat.st_size, stat.st_mtime_ns) if stat else None
                if current and current[0] > 0 and current == prior:
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1048576), b""): digest.update(block)
                    file_result.update(file_verified=True, path=str(path.resolve()), bytes=current[0], sha256=digest.hexdigest()); break
                prior = current; time.sleep(0.2)
            if not file_result["file_verified"]: raise RuntimeError("save acknowledged but output file was not verified")
        self.event("save_observation", **file_result)
        return {"schema": SCHEMA, "run_id": self.run_id, "status": "completed", "pid": self.game.pid,
                "creation_time": self.game.creation_time, "initial_country_restored": self.initial,
                "step_hours": self.plan["step_hours"], "date_raw": self.date_raw, "effects": results, "save": file_result}

    def run(self):
        # Existing output may contain a pending native order. Never assign it new IDs.
        self.output.mkdir(parents=True, exist_ok=False)
        (self.output / "plan.json").write_text(json.dumps(self.plan, ensure_ascii=False, indent=2), encoding="utf-8")
        self.event("session", run_id=self.run_id, pid=self.game.pid, creation_time=self.game.creation_time)
        try:
            self.call("main_status")
            self.preflight(); self.execute_actions(); self.advance()
            result = self.final_observations()
        except Exception as error:
            unknown = isinstance(error, NativeRpcError) and error.outcome == "unknown"
            self.event("stopped", error=str(error), unknown_outcome=unknown,
                       reconcile_required=True, controller=self.current, initial_controller=self.initial)
            # No automatic recovery orders, controller switches, or unpause after failure.
            raise
        (self.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--save-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--execute", action="store_true", help="dispatch the validated plan; otherwise validate schema only")
    args = parser.parse_args()
    plan = validate_plan(json.loads(args.plan.read_text(encoding="utf-8")))
    if not args.execute:
        print(json.dumps({"valid": True, "schema": SCHEMA, "countries": len(plan["countries"]), "actions": sum(len(c["actions"]) for c in plan["countries"]), "executed": False})); return
    if not args.pid or args.timeout <= 0: parser.error("execution requires positive --pid and --timeout")
    if args.output.exists(): parser.error("output exists; reconcile its journal, then choose a fresh output for a new turn")
    game = NativeGame(args.pid, args.output / "requests")
    result = NativeTurn(game, plan, args.output, save_dir=args.save_dir, timeout=args.timeout).run()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
