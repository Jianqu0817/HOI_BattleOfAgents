"""Explicit, deterministic policy for the single shared native game clock."""
from __future__ import annotations

DEFAULT_POLICY = {"peace_hours": 168, "war_hours": 24, "critical_hours": 6,
                  "maximum_hours": 360, "chunk_hours": 6}


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) - set(DEFAULT_POLICY):
        raise ValueError("unknown clock policy fields")
    value = {**DEFAULT_POLICY, **policy}
    for key, hours in value.items():
        maximum = 6 if key == "chunk_hours" else 360
        if type(hours) is not int or not 1 <= hours <= maximum:
            raise ValueError(f"{key} must be an integer 1..{maximum}")
    if not value["critical_hours"] <= value["war_hours"] <= value["peace_hours"] <= value["maximum_hours"]:
        raise ValueError("clock hours must satisfy critical <= war <= peace <= maximum")
    return value


def select_step_hours(*, at_war, public_events=(), policy=None):
    """Choose before planning/sealing; no country gets an independent clock.

    Public events are observed facts, not forecasts. A pending decision, critical
    event, or newly changed war/controller calls for the shortest planning span.
    This pure function never advances time or calls a model.
    """
    if type(at_war) is not bool:
        raise ValueError("at_war must be boolean")
    policy = validate_policy({} if policy is None else policy)
    critical = any(event.get("critical") is True or event.get("decision_required") is True
                   or event.get("kind") in {"war_changed", "controller_changed", "war_started", "province_control_changed"}
                   for event in public_events)
    reason = "critical_event" if critical else "war" if at_war else "peace"
    hours = policy["critical_hours" if critical else "war_hours" if at_war else "peace_hours"]
    return {"step_hours": hours, "reason": reason, "shared_clock": True,
            "policy": policy}
