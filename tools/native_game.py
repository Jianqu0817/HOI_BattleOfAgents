"""Typed Windows game API. No screen input or automatic mutation retries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from native_rpc import process_creation_time, request


class NativeGame:
    def __init__(self, pid: int, journal: Path):
        if type(pid) is not int or pid <= 0:
            raise ValueError("pid must be a positive integer")
        self.pid = pid
        self.journal = Path(journal)
        self.creation_time = process_creation_time(pid)

    def call(self, op: str, payload: str = "", *, request_id: str) -> dict:
        """Persist intent before dispatch, and retain receipt for reconciliation."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", request_id):
            raise ValueError("invalid request ID")
        self.journal.mkdir(parents=True, exist_ok=True)
        path = self.journal / (request_id + ".json")
        if process_creation_time(self.pid) != self.creation_time:
            raise RuntimeError("game process restarted; reconcile the original session")
        intent = {"pid": self.pid, "creation_time": self.creation_time,
                  "operation": op, "payload": payload, "request_id": request_id}
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["intent"] != intent:
                raise ValueError("request ID already has a different intent")
            if "receipt" in record:
                return self._result(record["receipt"])
        else:
            # Exclusive creation prevents overwriting another pending submission.
            with path.open("x", encoding="utf-8") as stream:
                json.dump({"intent": intent, "status": "pending"}, stream, indent=2)
        receipt = request(self.pid, op, payload, request_id=request_id)
        record = {"intent": intent, "status": "received", "receipt": receipt}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return self._result(receipt)

    @staticmethod
    def _result(receipt: dict) -> dict:
        if not receipt.get("ok"):
            raise RuntimeError(json.dumps(receipt, ensure_ascii=False))
        result = receipt["result"]
        if isinstance(result, dict) and result.get("ok") is False:
            raise RuntimeError(json.dumps(receipt, ensure_ascii=False))
        return result

    def status(self, *, request_id: str) -> dict:
        return self.call("main_status", request_id=request_id)

    def clock(self, *, request_id: str) -> dict:
        return self.call("native_clock_v1", "status", request_id=request_id)

    def step(self, hours: int, *, request_id: str) -> dict:
        if type(hours) is not int or not 1 <= hours <= 360:
            raise ValueError("turn hours must be 1..360")
        return self.call("native_clock_v1", f"step {hours}", request_id=request_id)

    def country_status(self, country: str, *, request_id: str) -> dict:
        return self.call("native_control_v1", "status " + self._country(country), request_id=request_id)

    def control(self, expected: str, target: str, *, request_id: str) -> dict:
        return self.call("native_control_v1", f"tag {self._country(expected)} {self._country(target)}",
                         request_id=request_id)

    def set_ai(self, country: str, expected: bool, enabled: bool, *, request_id: str) -> dict:
        if type(expected) is not bool or type(enabled) is not bool:
            raise ValueError("AI states must be booleans")
        return self.call("native_control_v1", f"ai {self._country(country)} {int(expected)} {int(enabled)}",
                         request_id=request_id)

    def save(self, basename: str, *, request_id: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", basename):
            raise ValueError("save must be a simple basename")
        return self.call("native_control_v1", "save " + basename, request_id=request_id)

    def war_goals(self, country: str, *, request_id: str) -> dict:
        return self.call("native_diplomacy_v2", "goals " + self._country(country), request_id=request_id)

    def war_status(self, country: str, recipient: str, *, request_id: str) -> dict:
        return self.call("native_diplomacy_v2", f"read {self._country(country)} {self._country(recipient)}",
                         request_id=request_id)

    def declare_war(self, country: str, recipient: str, goal_handle: str, *, request_id: str,
                    construct_only: bool = False) -> dict:
        if not isinstance(goal_handle, str) or not re.fullmatch(r"0x[0-9a-fA-F]{1,16}", goal_handle):
            raise ValueError("goal handle must come from the owned goal snapshot")
        if country == recipient:
            raise ValueError("a country cannot declare war on itself")
        action = "build_declare" if construct_only else "post_declare"
        return self.call("native_diplomacy_v2",
                         f"{action} {self._country(country)} {self._country(recipient)} {goal_handle}",
                         request_id=request_id)

    def air(self, country: str, *, request_id: str) -> dict:
        return self.call("native_air_navy_v2", "air_list " + self._country(country), request_id=request_id)

    def navy(self, country: str, *, request_id: str) -> dict:
        return self.call("native_air_navy_v2", "navy_list " + self._country(country), request_id=request_id)

    def air_mission(self, country: str, wing_id: int, expected_mask: int, mission_bit: int,
                    enabled: bool, *, request_id: str, construct_only: bool = False) -> dict:
        self._positive(wing_id)
        self._nonnegative(expected_mask)
        self._positive(mission_bit)
        if mission_bit & (mission_bit - 1) or type(enabled) is not bool:
            raise ValueError("choose one supported mission bit and a boolean enabled state")
        action = "air_build" if construct_only else "air_post"
        return self.call("native_air_navy_v2",
                         f"{action} {self._country(country)} {wing_id} {expected_mask} {mission_bit} {int(enabled)}",
                         request_id=request_id)

    def air_region(self, country: str, wing_id: int, expected: int, target: int, *, request_id: str,
                   construct_only: bool = False) -> dict:
        self._positive(wing_id)
        self._nonnegative(expected)
        self._nonnegative(target)
        action = "air_region_build" if construct_only else "air_region_post"
        return self.call("native_air_navy_v2", f"{action} {self._country(country)} {wing_id} {expected} {target}",
                         request_id=request_id)

    def navy_mission(self, country: str, task_force_id: int, expected: int, target: int, *, request_id: str,
                     construct_only: bool = False) -> dict:
        self._positive(task_force_id)
        self._nonnegative(expected)
        if type(target) is not int or target not in (0, 1, 2, 3, 4, 5, 6, 7, 9):
            raise ValueError("unsupported naval mission type")
        action = "navy_build" if construct_only else "navy_post"
        return self.call("native_air_navy_v2", f"{action} {self._country(country)} {task_force_id} {expected} {target}",
                         request_id=request_id)

    def navy_regions(self, country: str, task_force_id: int, expected: list[int], target: list[int], *,
                     request_id: str, construct_only: bool = False) -> dict:
        """Set the selected task force's parent fleet regions, including its siblings."""
        self._positive(task_force_id)
        action = "navy_regions_build" if construct_only else "navy_regions_post"
        return self.call("native_air_navy_v2",
                         f"{action} {self._country(country)} {task_force_id} {self._region_csv(expected)} {self._region_csv(target)}",
                         request_id=request_id)

    @classmethod
    def _region_csv(cls, values: list[int]) -> str:
        if not isinstance(values, list) or len(values) > 32:
            raise ValueError("regions must be a list of at most 32 distinct IDs")
        for value in values:
            cls._positive(value)
        if len(set(values)) != len(values):
            raise ValueError("duplicate regions")
        return ",".join(map(str, values)) or "-"

    def units(self, country: str, *, request_id: str) -> dict:
        return self.call("native_move", "units " + self._country(country), request_id=request_id)

    def inspect(self, country: str, unit_id: int, *, request_id: str) -> dict:
        self._positive(unit_id)
        return self.call("native_move", f"inspect_unit {self._country(country)} {unit_id}", request_id=request_id)

    def move(self, country: str, unit_id: int, source: int, target: int, *, request_id: str,
             construct_only: bool = False) -> dict:
        for value in (unit_id, source, target):
            self._positive(value)
        if source == target:
            raise ValueError("source equals target")
        operation = "build_unit" if construct_only else "post_unit"
        return self.call("native_move", f"{operation} {self._country(country)} {unit_id} {source} {target}",
                         request_id=request_id)

    @staticmethod
    def _country(value: str) -> str:
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("country must be a three-letter tag")
        return value

    @staticmethod
    def _positive(value: int):
        if type(value) is not int or not 0 < value <= 0x7fffffff:
            raise ValueError("IDs must be positive 32-bit integers")

    @staticmethod
    def _nonnegative(value: int):
        if type(value) is not int or not 0 <= value <= 0x7fffffff:
            raise ValueError("state values must be nonnegative 32-bit integers")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--request-id", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("clock")
    step = sub.add_parser("step")
    step.add_argument("hours", type=int)
    country_status = sub.add_parser("country")
    country_status.add_argument("country")
    control = sub.add_parser("control")
    control.add_argument("expected")
    control.add_argument("target")
    ai = sub.add_parser("ai")
    ai.add_argument("country")
    ai.add_argument("expected", type=int, choices=(0, 1))
    ai.add_argument("enabled", type=int, choices=(0, 1))
    save = sub.add_parser("save")
    save.add_argument("basename")
    for command in ("war-goals", "air", "navy"):
        sub.add_parser(command).add_argument("country")
    war_status = sub.add_parser("war-status")
    war_status.add_argument("country")
    war_status.add_argument("recipient")
    war = sub.add_parser("war")
    war.add_argument("country")
    war.add_argument("recipient")
    war.add_argument("goal_handle")
    war.add_argument("--construct-only", action="store_true")
    for command, fields in (("air-mission", ("wing_id", "expected_mask", "mission_bit", "enabled")),
                            ("air-region", ("wing_id", "expected", "target")),
                            ("navy-mission", ("task_force_id", "expected", "target"))):
        action = sub.add_parser(command)
        action.add_argument("country")
        for field in fields:
            action.add_argument(field, type=int, choices=(0, 1) if field == "enabled" else None)
        action.add_argument("--construct-only", action="store_true")
    navy_regions = sub.add_parser("navy-regions")
    navy_regions.add_argument("country")
    navy_regions.add_argument("task_force_id", type=int)
    navy_regions.add_argument("expected", type=json.loads, help="JSON array, e.g. []")
    navy_regions.add_argument("target", type=json.loads, help="JSON array, e.g. [9]")
    navy_regions.add_argument("--construct-only", action="store_true")
    units = sub.add_parser("units")
    units.add_argument("country")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("country")
    inspect.add_argument("unit_id", type=int)
    for command in ("build", "move"):
        action = sub.add_parser(command)
        action.add_argument("country")
        for field in ("unit_id", "source", "target"):
            action.add_argument(field, type=int)
    args = parser.parse_args()
    game = NativeGame(args.pid, args.journal)
    kwargs = {"request_id": args.request_id}
    if args.command == "status":
        result = game.status(**kwargs)
    elif args.command == "clock":
        result = game.clock(**kwargs)
    elif args.command == "step":
        result = game.step(args.hours, **kwargs)
    elif args.command == "country":
        result = game.country_status(args.country, **kwargs)
    elif args.command == "control":
        result = game.control(args.expected, args.target, **kwargs)
    elif args.command == "ai":
        result = game.set_ai(args.country, bool(args.expected), bool(args.enabled), **kwargs)
    elif args.command == "save":
        result = game.save(args.basename, **kwargs)
    elif args.command == "war-goals":
        result = game.war_goals(args.country, **kwargs)
    elif args.command == "war-status":
        result = game.war_status(args.country, args.recipient, **kwargs)
    elif args.command == "war":
        result = game.declare_war(args.country, args.recipient, args.goal_handle,
                                  construct_only=args.construct_only, **kwargs)
    elif args.command in ("air", "navy"):
        result = getattr(game, args.command)(args.country, **kwargs)
    elif args.command == "air-mission":
        result = game.air_mission(args.country, args.wing_id, args.expected_mask, args.mission_bit,
                                  bool(args.enabled), construct_only=args.construct_only, **kwargs)
    elif args.command == "air-region":
        result = game.air_region(args.country, args.wing_id, args.expected, args.target,
                                 construct_only=args.construct_only, **kwargs)
    elif args.command == "navy-mission":
        result = game.navy_mission(args.country, args.task_force_id, args.expected, args.target,
                                   construct_only=args.construct_only, **kwargs)
    elif args.command == "navy-regions":
        result = game.navy_regions(args.country, args.task_force_id, args.expected, args.target,
                                   construct_only=args.construct_only, **kwargs)
    elif args.command == "units":
        result = game.units(args.country, **kwargs)
    elif args.command == "inspect":
        result = game.inspect(args.country, args.unit_id, **kwargs)
    else:
        result = game.move(args.country, args.unit_id, args.source, args.target,
                           construct_only=args.command == "build", **kwargs)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
