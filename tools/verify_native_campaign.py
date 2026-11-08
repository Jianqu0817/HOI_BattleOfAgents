"""Offline audit of the recorded five-country native campaign; no game/API calls."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from campaign import (ROOT, apply_model_switch, config_digest, model_slot_fingerprints, model_usage, name_ok,
                      switch_fingerprint_list, validate_switch_spec)
from model_trial import compact_native_context, decision_private_context, digest, native_brief, normalize_request, own_history, private_context, validate_trial_request
from native_capture import BUILD, GAME_SHA256
from native_observation import _digest as observation_digest, to_native_plan
from native_turn import NativeTurn
from native_pacing import select_step_hours, validate_policy
from story_channels import (check_public_asides, interview_messages, project_interview_answer,
                            public_commentary_messages, public_context, public_source_ids)
from showrunner import check_history, check_intent, check_replies, ledger, visible_promises
from verify_campaign import Audit, audit_episode, audit_model


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            value.update(block)
    return value.hexdigest()


def audit_model_switches(audit, manifest):
    """Validate the recorded provider switch ledger; return the effective configs a record may use."""
    switches = manifest.get("model_switches", [])
    if not switches:
        return []
    steps, config, ok = [], manifest["config"], True
    for record in switches:
        ok = (ok and isinstance(record, dict) and record.get("schema") == "iron.model-switch.v1"
              and isinstance(record.get("reason"), str) and isinstance(record.get("source"), str)
              and isinstance(record.get("applied_at"), str) and type(record.get("first_affected_turn")) is int)
        try:
            expected_from = switch_fingerprint_list(model_slot_fingerprints(config))
            spec = validate_switch_spec(record.get("spec", {}) if isinstance(record, dict) else {})
            config = apply_model_switch(config, spec)
            expected_to = switch_fingerprint_list(model_slot_fingerprints(config))
        except ValueError:
            audit.add("model_switch_ledger", False)
            return []
        ok = ok and switch_fingerprint_list(record.get("from", {})) == expected_from \
            and switch_fingerprint_list(record.get("to", {})) == expected_to
        steps.append({"applied_at": record["applied_at"], "config": config})
    audit.add("model_switch_ledger", ok)
    return steps if ok else []


def effective_config_at(steps, config, started_at):
    """The model configuration effective when a call started; switch boundaries follow recorded timestamps."""
    chosen = config
    for step in steps:
        if started_at and step["applied_at"] <= started_at:
            chosen = step["config"]
    return chosen


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def response(record):
    return record.get("normalized_response", record["response"])


def audit_clock(audit, initial, hours, index, native, captured, entry, prefix, *, start_raw=None):
    start = initial + hours * (index - 1) if start_raw is None else start_raw
    elapsed = native.get("step_hours")
    valid = type(elapsed) is int and 0 < elapsed <= hours
    early = native.get("early_end")
    if valid and elapsed < hours:
        details = early if isinstance(early, dict) else {}
        state, clock = details.get("state", {}), details.get("clock", {})
        valid = (isinstance(early, dict) and early.get("reason") in {"war_changed", "public_state_changed"}
                 and early.get("remaining_budget_hours") == hours - elapsed
                 and state.get("paused") is True and clock.get("paused") is True
                 and clock.get("remaining_hours") == 0 and state.get("date_raw") == native.get("date_raw")
                 and state.get("played_index") is not None and state.get("played_index") == state.get("country_index")
                 and not (early.get("details") or {}).get("controller_changed"))
    elif valid:
        valid = not early
    audit.add(prefix + ":exact_cumulative_raw_clock", bool(valid
              and native.get("requested_step_hours", hours) == hours
              and native.get("remaining_budget_hours", hours - elapsed) == hours - elapsed))
    expected = start + elapsed if type(elapsed) is int else None
    audit.add(prefix + ":matching_final_raw_clock", valid
              and native.get("date_raw") == captured.get("date_raw") == entry.get("final_date_raw") == expected)


def audit_capture(audit, directory, tags, expected, identity, prefix):
    manifest = audit.read(directory / "capture.json", prefix + ":manifest")
    if manifest is None:
        return None
    audit.add(prefix + ":native_identity_date", manifest.get("schema") == "iron.native-capture.v1"
              and manifest.get("date_raw") == expected and all(manifest.get(k) == v for k, v in identity.items())
              and manifest.get("world", {}).get("build") == BUILD and manifest.get("world", {}).get("sha256") == GAME_SHA256)
    values = {}
    for filename in ("snapshots.json", "world.json", "observations.json"):
        path = directory / filename
        values[filename] = audit.read(path, prefix + ":" + filename)
        inventory = manifest.get("files", {}).get(filename, {})
        audit.add(prefix + ":hash:" + filename, path.is_file() and inventory.get("bytes") == path.stat().st_size
                  and inventory.get("sha256") == file_hash(path))
    snapshots, world = values["snapshots.json"], values["world.json"]
    if snapshots is not None and world is not None:
        audit.add(prefix + ":same_frozen_world", set(snapshots) == set(tags) and world.get("date_raw") == expected
                  and bool(world.get("states")) and bool(world.get("provinces"))
                  and all(snapshots[tag]["control"]["date_raw"] == snapshots[tag]["state_date_raw"] == expected
                          and snapshots[tag]["control"].get("paused") is True
                          and snapshots[tag]["control"].get("ai_enabled") is False for tag in tags))
    return {"manifest": manifest, "snapshots": snapshots, "world": world, "observations": values["observations.json"]}


def audit_sealed(audit, trial, observed, decisions, result, prefix, limit):
    requests = {}
    reviews = {row["country"]: row for row in decisions.get("referee", {}).get("reviews", [])}
    audit.add(prefix + ":five_referee_approvals", decisions.get("status") == "approved" and set(reviews) == set(observed))
    for tag, observation in observed.items():
        label = prefix + ":sealed:" + tag
        sealed = audit.read(trial / "private_decisions" / (tag + ".json"), label)
        executed = audit.read(trial / "requests" / (tag + ".json"), label + ":request")
        call = audit.read(trial / "model_calls" / ("decision_" + tag + ".json"), label + ":call")
        if sealed is None or executed is None or call is None:
            continue
        validate_trial_request(sealed, observation, limit)
        review = reviews.get(tag, {})
        audit.add(label + ":unchanged_model_referee_execution", normalize_request(call["response"]) == sealed == executed == result.get("requests", {}).get(tag)
                  and review.get("approved") is True and review.get("request_sha256") == digest(sealed)
                  and decisions.get("request_hashes", {}).get(tag) == digest(sealed))
        requests[tag] = sealed
    return requests


def projected_history(rows):
    """Restore native_brief aliases before applying the established route checks."""
    return [{"private_decision": row.get("own_decision") or {},
             "private_intent": row.get("own_intent") or {},
             "private_diplomacy": row.get("own_private_diplomacy") or {}} for row in rows]


def audit_annotation(audit, record, calls, label):
    original, normalized = record["response"], response(record)

    def speech(value):
        return {k: value.get(k) for k in ("country", "to", "message")} | {
            "public_asides": value.get("public_asides"),
            "replies": [{k: row.get(k) for k in ("to", "message")} for row in value.get("replies", [])]}

    speech_projection = record.get("speech_projection")
    if speech_projection is not None:
        phase = record.get("phase")
        expected = ({"country": record.get("country"), "to": None, "message": ""} if phase == "private"
                    else {"country": record.get("country"), "replies": []} if phase == "reply"
                    else {"country": record.get("country"), "message": ""})
        audit.add(label + ":speech_projection_receipt",
                  speech_projection.get("method") == "noop_message.v1"
                  and speech_projection.get("raw") == original
                  and isinstance(speech_projection.get("reason"), str)
                  and normalized == expected)
        return

    projection = record.get("annotation_projection")
    delivery = record.get("delivery_projection")
    chat_actions = record.get("chat_actions_projection")
    if projection is not None or delivery is not None or chat_actions is not None:
        from communication_annotations import project_commitment_quotes, project_reply_routes
        phase = record.get("phase")
        allowed = ({"country", "replies", "commitments"} if phase == "reply" else
                   {"country", "message", "commitments", "commitments_note"})
        if phase == "private":
            allowed.add("to")
        if phase == "diplomacy":
            allowed.add("public_asides")
        removed = sorted(set(original) - allowed)
        typed = {key: value for key, value in original.items() if key in allowed}
        if "commitments_note" in typed and typed["commitments_note"] is None:
            typed.pop("commitments_note")
            removed.append("null commitments_note")
        if chat_actions is not None:
            audit.add(label + ":chat_actions_projection_receipt",
                      chat_actions.get("method") == "chat_is_not_execution_envelope.v1"
                      and chat_actions.get("rejected_actions") == original.get("actions"))
        payload = json.loads(record["messages"][1]["content"])
        turn_id = payload.get("own_observation", {}).get("identity", {}).get("turn_id")
        routed = typed
        if delivery is not None:
            tag = record.get("country", original.get("country"))
            routed, rejected = project_reply_routes(typed, tag, payload.get("own_private_diplomacy", {}).get("received", []))
            audit.add(label + ":delivery_projection_from_frozen_inbox", phase == "reply" and typed.get("country") == tag
                      and bool(rejected) and delivery == {"method": "current_sender_delivery.v1", "rejected": rejected})
        expected, changes = project_commitment_quotes(routed, turn_id=turn_id) if projection is not None else (routed, [])
        audit.add(label + ":literal_annotation_projection_from_raw",
                  phase in {"diplomacy", "private", "reply"} and (chat_actions is not None or not original.get("actions"))
                  and record.get("annotation_repair") is None and record.get("normalizations", []) == removed
                  and (projection is None or projection == {"method": "literal_commitment_projection.v1", "changes": changes})
                  and normalized == expected and speech(routed) == speech(normalized))
        return

    repair = record.get("annotation_repair")
    if repair is None:
        return
    repaired = calls.get(repair, {})

    def conditions(value):
        return [[{key: promise.get(key) for key in ("to", "due_turn", "due_date_raw", "condition", "predicate", "condition_predicate")}
                 for promise in group.get("commitments", [])] for group in [value] + value.get("replies", [])]

    same_conditions = conditions(original) == conditions(normalized)
    repair_payload = json.loads(repaired.get("messages", [{}, {"content": "{}"}])[1]["content"])
    if repair_payload.get("validation_error") == "A message permits at most three commitments":
        same_conditions = len(normalized.get("commitments", [])) == 3 and all(
            promise in original.get("commitments", []) for promise in normalized["commitments"])
    audit.add(label + ":annotation_preserves_literal_speech", repaired.get("response") == normalized
              and speech(original) == speech(normalized) and same_conditions)


def audit_public_prompt(audit, messages, observation, label, *, host=False):
    """Final prompt must equal the public projection, including nested fields."""
    payload = json.loads(messages[1]["content"])
    reprojection = copy.deepcopy(messages)
    reinput = copy.deepcopy(payload)
    if "public_commitment_memory" in reinput and "own_commitment_memory" not in reinput:
        reinput["own_commitment_memory"] = reinput["public_commitment_memory"]
    reprojection[1]["content"] = json.dumps(reinput, ensure_ascii=False)
    projected = json.loads(public_context(reprojection, observation)[1]["content"])
    if host:
        projected["visibility"] = "public"
        if projected.get("public_host_messages"):
            projected.pop("public_host_messages")
    audit.add(label + ":final_public_allowlist", payload == projected)
    identity = {key: observation[key] for key in ("match_id", "turn_id", "country", "date", "observation_id")}
    audit.add(label + ":public_own_identity", payload.get("own_observation", {}).get("identity") == identity)
    if host:
        direct = projected.get("public_diplomacy", []) + projected.get("public_board", [])
        for history in projected.get("public_history", []):
            direct += history.get("public_diplomacy", [])
        return {row["id"] for row in direct if isinstance(row.get("id"), str)}
    return public_source_ids(messages, observation)


def audit_performance(audit, trial, config, observed, calls, prefix):
    if not config.get("performance", {}).get("enabled"):
        return
    performance = audit.read(trial / "performance.json", prefix + ":performance")
    if performance is None:
        return
    public = read(trial / "public_diplomacy.json")
    tags = config["countries"]
    audit.add(prefix + ":performance_no_writeback", performance.get("schema") == "iron.performance.v1"
              and performance.get("writeback") is False)
    if config["performance"].get("public_host", True):
        call = calls.get("public_host", {})
        expected = public_commentary_messages(observed[tags[0]], public, show=config.get("show"))
        audit.add(prefix + ":public_host_exact_public_sources", call.get("messages") == expected)
        sources = audit_public_prompt(audit, call["messages"], observed[tags[0]], prefix + ":host", host=True)
        from story_channels import check_public_commentary, project_public_commentary
        projected_host, dropped_host = project_public_commentary(call.get("response", {}), sources)
        audit.add(prefix + ":host_projection_receipt", not dropped_host or call.get("host_projection") == {"method": "sourced_public_commentary.v1", "dropped": dropped_host})
        check_public_commentary(projected_host, sources)
        expected_hosts = []
        for index, row in enumerate(projected_host["commentary"]):
            identity = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            expected_hosts.append({"id": "host-" + identity, "visibility": "public", "source_kind": "host_commentary",
                                   "message": row["text"], "refs": [row["ref"]], "index": index})
        audit.add(prefix + ":public_host_literal_response", performance.get("host") == expected_hosts)
    count = config["performance"].get("interviews_per_turn", 1)
    offset = (observed[tags[0]]["turn_id"] - 1) % len(tags)
    interviews = performance.get("interviews", [])
    if config.get("audience_timing") == "postmatch" and not interviews and not any(
            name.startswith("audience_interview_") for name in calls):
        audit.add(prefix + ":audience_interviews_pending", None)
        return
    audit.add(prefix + ":interview_rotation", [row.get("country") for row in interviews]
              == [tags[(offset + index) % len(tags)] for index in range(count)])
    for row in interviews:
        tag = row["country"]
        call = calls.get("audience_interview_" + tag, {})
        expected = interview_messages(observed[tag], read(trial / "private_decisions" / (tag + ".json")), row["question"],
                                      public=public, private=read(trial / "private_diplomacy" / (tag + ".json")),
                                      intent=read(trial / "private_intents" / (tag + ".json")), show=config.get("show"))
        audit.add(prefix + ":interview_exact_own_scope:" + tag, call.get("messages") == expected)
        projected_answer, removed_answer = project_interview_answer(call.get("response", {}), tag)
        audit.add(prefix + ":interview_audience_only:" + tag, row.get("visibility") == "audience_only"
                  and (call.get("response") == {key: row[key] for key in ("country", "answer")}
                       or (call.get("interview_projection") == {"method": "audience_interview_fields.v1", "removed": removed_answer}
                           and projected_answer == {key: row[key] for key in ("country", "answer")})))


def audit_players(audit, trial, config, observed, decisions, prefix, switch_steps=(), *, audience_config=None, completed_at=None):
    tags = config["countries"]
    public = read(trial / "public_diplomacy.json")
    calls = {path.stem: read(path) for path in (trial / "model_calls").glob("*.json")}
    intents = {tag: check_intent(read(trial / "private_intents" / (tag + ".json")), tag) for tag in tags}
    initial = [response(calls["private_" + tag]) for tag in tags]
    required = {"referee"} | {f"{phase}_{tag}" for tag in tags for phase in ("intent", "private", "decision", "diplomacy", "diplomacy_2")}
    audit.add(prefix + ":required_model_calls", required <= set(calls))
    private = list(initial)
    for tag in tags:
        replies = read(trial / "private_replies" / (tag + ".json"))
        check_replies(replies, tag, private_context(tag, initial)["received"], tags, observed[tag]["turn_id"])
        private.extend({"country": tag, **row, "kind": "reply"} for row in replies["replies"])
        if private_context(tag, initial)["received"]:
            audit.add(prefix + ":reply_response:" + tag, response(calls["reply_" + tag]) == replies)
    audit.add(prefix + ":canonical_private_mail", private == read(trial / "private_messages.json"))
    audit.add(prefix + ":literal_commitments", ledger(public, private, observed[tags[0]]["turn_id"]) == read(trial / "commitments.json"))
    audit.add(prefix + ":two_public_rounds", config["communication_rounds"] == 2 and len(public) == 2 * len(tags)
              and all(sum(row["country"] == tag and row["round"] == number for row in public) == 1 for tag in tags for number in (1, 2)))
    response_ids, intent_ends, public_starts = [], [], []
    for label, record in calls.items():
        tag, phase = record.get("country"), record["phase"]
        effective = effective_config_at(switch_steps, config, record.get("metadata", {}).get("started_at"))
        if phase == "audience_interview" and audience_config is not None and completed_at and record.get("metadata", {}).get("started_at", "") > completed_at:
            effective = audience_config
        expected = (effective["show"]["referee_model"] if phase == "referee" else
                    {**effective["model"], **effective["show"].get("models", {}).get(tag, {})})
        cap_phase = label.split("_")[0]
        cap = config["show"].get("phase_max_tokens", {}).get(cap_phase)
        if cap is not None:
            expected = {**expected, "max_tokens": min(expected["max_tokens"], cap)}
        if phase in ("public_host", "audience_interview"):
            expected = {**expected, "max_tokens": 700 if phase == "public_host" else 600}
        start, end = audit_model(audit, record, expected, prefix + ":API:" + label)
        usage = record.get("metadata", {}).get("usage", {})
        audit.add(prefix + ":API_usage:" + label, all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
                  and usage.get("total_tokens") == usage.get("prompt_tokens", -1) + usage.get("completion_tokens", -1)
                  and isinstance(record.get("metadata", {}).get("elapsed_seconds"), (int, float)))
        response_ids.append(record.get("metadata", {}).get("response_id"))
        if phase in ("public_host", "audience_interview"):
            continue
        if phase == "referee":
            payload = json.loads(record["messages"][1]["content"])
            sha_projection = record.get("sha_projection")
            audit.add(prefix + ":referee_sha_projection_receipt", not sha_projection or
                      (sha_projection.get("method") == "referee_sha_echo.v1"
                       and all(fix["corrected"] == decisions["request_hashes"][fix["country"]] for fix in sha_projection.get("corrections", []))
                       and all(fix["recorded"] != fix["corrected"] for fix in sha_projection.get("corrections", []))))
            audit.add(prefix + ":referee_original_response", response(record) == decisions.get("referee"))
            audit.add(prefix + ":referee_sealed_inputs", all(row["request_sha256"] == decisions["request_hashes"][row["country"]]
                      and row["request"] == read(trial / "private_decisions" / (row["country"] + ".json")) for row in payload["countries"]))
            continue
        if phase in ("communication_quote_repair", "communication_annotation_repair"):
            audit.add(prefix + ":repair_is_annotation_only:" + label,
                      json.loads(record["messages"][1]["content"]).get("original_response", {}).get("country") == tag)
            continue
        audit_annotation(audit, record, calls, prefix + ":" + label)
        payload = json.loads(record["messages"][1]["content"])
        own = payload["own_observation"]
        check_history(projected_history(own.get("recent_private_history", [])), tag)
        strict_public = phase == "diplomacy" and config["show"].get("strict_public_context")
        if strict_public:
            sources = audit_public_prompt(audit, record["messages"], observed[tag], prefix + ":" + label)
            check_public_asides(response(record), tags, sources)
        else:
            expected_own = copy.deepcopy(native_brief(observed[tag]))
            if config["show"].get("compact_native_context"):
                expected_own = compact_native_context({"own_observation": expected_own})["own_observation"]
            audit.add(prefix + ":own_native_projection:" + label, own == expected_own)
            audit.add(prefix + ":no_audience_or_admin_payload:" + label,
                      set(payload) <= {"task", "own_observation", "public_diplomacy", "own_private_diplomacy", "match_rules", "game_guide", "own_profile", "participants", "turn_hours", "own_score_context", "own_commitment_memory", "commitment_memory_notice", "own_pre_diplomacy_intent", "history_notice"})
        profile = config["show"]["profiles"].get(tag, {})
        if strict_public:
            profile = {key: value for key, value in profile.items() if key in ("name", "persona")}
        audit.add(prefix + ":own_profile:" + label, payload.get("own_profile") == profile)
        promises = payload.get("own_commitment_memory", [])
        audit.add(prefix + ":own_visible_commitments:" + label, len(visible_promises(promises, tag)) == len(promises))
        if phase == "intent":
            audit.add(prefix + ":intent_inputs:" + tag, payload["public_diplomacy"] == [] and record["response"] == intents[tag])
            if end:
                intent_ends.append(end)
        elif not strict_public:
            audit.add(prefix + ":own_intent:" + label, payload.get("own_pre_diplomacy_intent") == intents[tag])
        if phase == "diplomacy":
            number = 2 if label.startswith("diplomacy_2_") else 1
            expected_public = [row for row in public if row["round"] < number]
            if strict_public:
                projected = public_context([{},{"content": json.dumps({"own_observation": own, "public_diplomacy": expected_public})}], observed[tag])
                expected_public = json.loads(projected[1]["content"])["public_diplomacy"]
            audit.add(prefix + ":frozen_public_round:" + label, payload["public_diplomacy"] == expected_public)
            rows = [row for row in public if row["country"] == tag and row["round"] == number]
            audit.add(prefix + ":literal_public_response:" + label, len(rows) == 1 and {k: v for k, v in rows[0].items() if k not in ("round", "id", "visibility", "source_kind")} == response(record))
            if start:
                public_starts.append(start)
        elif phase != "intent":
            audit.add(prefix + ":complete_public_inputs:" + label, payload["public_diplomacy"] == public)
        if phase in ("decision", "reply"):
            audit.add(prefix + ":addressed_private_inputs:" + label,
                      payload.get("own_private_diplomacy") == (private_context(tag, initial) if phase == "reply"
                      else decision_private_context(trial, tag, private)))
        elif "own_private_diplomacy" in payload:
            audit.add(prefix + ":no_early_inbox:" + label, payload["own_private_diplomacy"] in (None, {"sent": [], "received": []}))
    for tag in tags:
        audit.add(prefix + ":mailbox_routes:" + tag, read(trial / "private_diplomacy" / (tag + ".json")) == decision_private_context(trial, tag, private))
    audit.add(prefix + ":unique_actual_response_ids", all(response_ids) and len(response_ids) == len(set(response_ids)))
    audit.add(prefix + ":intents_precede_all_public_rounds", len(intent_ends) == len(tags) and bool(public_starts) and max(intent_ends) <= min(public_starts))
    audit_performance(audit, trial, config, observed, calls, prefix)
    return calls


def audit_native_execution(audit, directory, execution, plan, native, compatibility, journals, identity, prefix, *, skip_save_bytes):
    facts = [json.loads(line) for line in (execution / "audience_facts.jsonl").read_text(encoding="utf-8").splitlines() if line]
    intents = [row for row in facts if row["event"] == "intent"]
    receipts = {row["request_id"]: row for row in facts if row["event"] == "receipt"}
    audit.add(prefix + ":unique_dispatch_intents", len(intents) == len({row["request_id"] for row in intents}))
    for row in intents:
        rid = row["request_id"]
        record = journals.get(rid, {})
        recorded_intent = record.get("intent", {})
        receipt = record.get("receipt", {})
        audit.add(prefix + ":journal:" + rid, recorded_intent.get("operation") == row["operation"]
                  and recorded_intent.get("payload") == row["payload"] and recorded_intent.get("request_id") == rid
                  and all(recorded_intent.get(k) == v for k, v in identity.items())
                  and receipt.get("ok") is True and receipt.get("id") == rid
                  and receipt.get("thread_id") == receipt.get("main_thread_id")
                  and receipt.get("result") == receipts.get(rid, {}).get("result"))
    effects = [row for row in facts if row["event"] == "action_effect"]
    final = {(row["country"], row["action_id"]): row for row in native["effects"]}
    actual = {(row["country"], row["action"]["id"]): row for row in compatibility["actions"]}
    expected_actions = [(country["tag"], action) for country in plan["countries"] for action in country["actions"]]
    audit.add(prefix + ":every_action_has_one_effect", Counter((row["country"], row["action"]["id"]) for row in effects)
              == Counter((tag, action["id"]) for tag, action in expected_actions))
    audit.add(prefix + ":every_action_has_one_final_effect", Counter((row["country"], row["action_id"]) for row in native["effects"])
              == Counter((tag, action["id"]) for tag, action in expected_actions))
    posts = [row for row in intents if row["payload"].startswith(("post_unit ", "post_declare ", "air_post ", "air_region_post ", "navy_post ", "navy_regions_post "))]
    audit.add(prefix + ":no_extra_native_posts", len(posts) == len(expected_actions))
    for tag, action in expected_actions:
        label = prefix + ":action:" + action["id"]
        operation, payload = NativeTurn.payload(tag, action)
        dispatched = [row for row in posts if row["operation"] == operation and row["payload"] == payload]
        ack = receipts.get(dispatched[0]["request_id"], {}).get("result", {}) if len(dispatched) == 1 else {}
        audit.add(label + ":posted_once_acknowledged", len(dispatched) == 1 and ack.get("queue_accepted") is True and ack.get("posted") is True)
        rows = [row for row in effects if row["country"] == tag and row["action"]["id"] == action["id"]]
        if len(rows) != 1:
            continue
        effect, after, entry = rows[0], final[(tag, action["id"])], actual[(tag, action["id"])]
        verified = NativeTurn.effect(action, effect["observed"])
        pending = action["kind"] == "division.move" and effect["observed"]["province"] != action["target"]
        audit.add(label + ":truthful_dispatch_effect", effect["effect_verified"] == verified
                  and entry["status"] == ("applied" if verified else "unknown")
                  and entry["effect_status"] == ("ordered" if verified and pending else "full" if verified else "unknown")
                  and entry["verification"]["observations"] == effect["observed"])
        audit.add(label + ":truthful_after_clock_effect", after["effect_observed_after_clock"] == NativeTurn.effect(action, after["observed"], after_clock=True))
    steps = [row for row in intents if row["operation"] == "native_clock_v1" and row["payload"].startswith("step ")]
    clock = [row for row in facts if row["event"] == "clock_effect"]
    chunks = [row for row in facts if row["event"] == "clock_chunk"]
    elapsed = native["step_hours"]
    chunk_limit = validate_policy(plan.get("clock_policy", {}))["chunk_hours"]
    chunked = bool(chunks)
    audit.add(prefix + ":single_bounded_clock_effect", len(clock) == 1 and bool(steps)
              and clock[0]["requested_hours"] == plan["step_hours"] and clock[0]["observed_hour_delta"] == elapsed
              and clock[0]["state"]["paused"] is True and clock[0]["state"]["date_raw"] == native["date_raw"])
    if chunked:
        cumulative = 0
        valid = len(steps) == len(chunks)
        for step, chunk in zip(steps, chunks):
            hours = chunk.get("observed_hour_delta")
            valid = valid and type(hours) is int and 0 < hours <= chunk_limit
            if type(hours) is not int:
                continue
            cumulative += hours
            valid = (valid and step["payload"] == f"step {hours}" and chunk.get("requested_hours") == hours
                     and chunk.get("cumulative_hours") == cumulative
                     and chunk.get("remaining_budget_hours") == plan["step_hours"] - cumulative
                     and chunk.get("state", {}).get("paused") is True
                     and chunk.get("state", {}).get("date_raw") == native["date_raw"] - elapsed + cumulative)
        audit.add(prefix + ":every_bounded_clock_chunk", valid and cumulative == elapsed)
    else:
        audit.add(prefix + ":legacy_exact_clock_dispatch", len(steps) == 1 and steps[0]["payload"] == f"step {elapsed}")
    early_events = [row for row in facts if row["event"] == "clock_early_end"]
    early = native.get("early_end")
    audit.add(prefix + ":early_end_matches_native_receipt", len(early_events) == (1 if early else 0)
              and (not early or (clock[0].get("early_end") == early
                   and all(early_events[0].get(key) == value for key, value in early.items()))))
    moves = {action["id"]: action for country in plan["countries"] for action in country["actions"]
             if action["kind"] == "division.move"}
    audit.add(prefix + ":only_actual_move_arrivals", all(row["action_id"] in moves for row in facts if row["event"] == "first_arrival"))
    for effect in native["effects"]:
        if "arrival_observed_during_clock" not in effect:
            continue
        arrivals = [row for row in facts if row["event"] == "first_arrival" and row["action_id"] == effect["action_id"]]
        first = effect.get("first_arrival")
        audit.add(prefix + ":truthful_first_arrival:" + effect["action_id"],
                  effect["arrival_observed_during_clock"] == bool(arrivals) and len(arrivals) <= 1
                  and (first == {key: arrivals[0][key] for key in ("date_raw", "observed")}
                       and first["observed"].get("province") == moves.get(effect["action_id"], {}).get("target")
                       and native["date_raw"] - elapsed <= first["date_raw"] <= native["date_raw"]
                       if arrivals else first is None))
    saved = native["save"]
    path = Path(saved["path"])
    audit.add(prefix + ":save_acknowledged_verified", saved.get("file_verified") is True and saved.get("handler_ack", {}).get("ok") is True
              and compatibility["final_save"].get("sha256") == saved.get("sha256"))
    if not skip_save_bytes:
        audit.add(prefix + ":actual_save_bytes_sha256", path.is_file() and path.stat().st_size == saved["bytes"] and file_hash(path) == saved["sha256"])
    audit.add(prefix + ":native_session_identity", native.get("status") == "completed"
              and all(native.get(k) == v for k, v in identity.items()) and native.get("initial_country_restored") is not None)


def audit_commentary_normalization(audit, call, commentary, base, prefix):
    from episode import normalize_commentary_quotes
    expected, normalizations = normalize_commentary_quotes(call["response"], base)
    audit.add(prefix + ":commentary_audience_only", commentary.get("audience") == call.get("audience") == "audience_full"
              and commentary.get("content") == expected)
    audit.add(prefix + ":commentary_normalization_recomputed_from_raw",
              call.get("normalized_response", call["response"]) == expected
              and call.get("normalizations", []) == normalizations
              and commentary.get("normalizations", []) == normalizations)


def audit_export_sources(audit, output, trial, turn, prefix, *, strict_evidence=True, audience_deferred=False):
    from episode import validate_commentary
    episode = read(output / "episode.json")
    undelivered = [row for path in sorted((trial / "model_calls").glob("reply_*.json"))
                   for row in read(path).get("delivery_projection", {}).get("rejected", [])]
    audience_drafts = [row for row in episode["events"] if row.get("kind") == "undelivered_private"]
    audit.add(prefix + ":undelivered_drafts_are_audience_private", len(undelivered) == len(audience_drafts)
              and all(event.get("visibility") == "private" and event.get("country") == rejected["country"]
                      and event.get("to") == rejected["to"] and event.get("text") == rejected["reply"].get("message", "")
                      and event.get("data") == {"status": "undelivered", "reason": rejected["reason"]}
                      for event, rejected in zip(audience_drafts, undelivered)))
    for index, event in enumerate(episode["events"]):
        source = event.get("source", {})
        path = source.get("file", "").split("/", 1)
        target = ((trial if path[0] == "trial" else turn) / path[1]) if len(path) == 2 and path[0] in ("trial", "turn") else None
        if event.get("kind") == "commentary":
            ids = {row["id"] for row in episode["events"] if row["kind"] != "commentary"}
            audit.add(prefix + f":source_{index}", target is not None and target.is_file()
                      and event.get("visibility") == "audience_only" and bool(source.get("evidence_ids"))
                      and all(value in ids for value in source["evidence_ids"]))
        else:
            audit.add(prefix + f":source_{index}", target is not None and target.is_file() and file_hash(target) == source.get("sha256"))
    if audience_deferred and not (trial / "audience_commentary/commentary.json").exists():
        audit.add(prefix + ":audience_commentary_pending", None)
        return
    commentary = read(trial / "audience_commentary/commentary.json")
    call = read(trial / "audience_commentary/model_call.json")
    base = {**episode, "events": [row for row in episode["events"] if row["kind"] != "commentary"]}
    audit_commentary_normalization(audit, call, commentary, base, prefix)
    evidence = [{key: event.get(key) for key in ("id", "kind", "country", "to", "title", "text", "game_date")}
                for event in base["events"]]
    payload = json.loads(call["messages"][1]["content"])
    recorded = payload.get("events", [])
    # Legacy fixed campaigns were re-exported after the native air/navy decoder
    # correction. Preserve those cached prompts; strict campaigns seal all text.
    same_evidence = recorded == evidence if strict_evidence else (
        len(recorded) == len(evidence) and all(
            row == expected or (expected["kind"] == "outcome" and
                {key: value for key, value in row.items() if key != "text"} ==
                {key: value for key, value in expected.items() if key != "text"})
            for row, expected in zip(recorded, evidence)))
    audit.add(prefix + ":director_exact_audience_evidence", payload.get("turn_id") == base["turn_id"]
              and same_evidence and set(payload) == {"turn_id", "events", "task"}
              and commentary.get("evidence_sha256") == digest(base["events"]))
    validate_commentary(commentary["content"], base)
    from episode import _commentary_events
    audit.add(prefix + ":commentary_export_literal_content", [row for row in episode["events"] if row["kind"] == "commentary"]
              == _commentary_events(base, commentary))
    audit.add(prefix + ":commentary_quotes_current_evidence", True)


def audit_production(audit, output, config, root, prefix):
    if not config.get("production"):
        return
    from production import editing_plan, make_lines
    production = config["production"]
    episode = read(output / "episode.json")
    plan = read(output / "editing_plan.json")
    audit.add(prefix + ":director_editing_evidence", plan == editing_plan(episode, target_seconds=production.get("target_seconds", 180)))
    lines = [json.loads(line) for line in (output / "voice_lines.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assignments = production.get("assignments", {})
    audit.add(prefix + ":verbatim_evidence_voice_lines", lines == make_lines(episode, assignments,
              event_ids=[row["evidence_id"] for row in plan["cuts"]]))
    registry_path = Path(production.get("registry", "configs/characters.example.json"))
    registry = read(registry_path if registry_path.is_absolute() else root / registry_path)
    characters = read(output / "characters.json")
    audit.add(prefix + ":configured_character_assignments", characters.get("assignments") == assignments
              and set(characters.get("characters", {})) == set(assignments.values()))
    for identity, character in characters.get("characters", {}).items():
        source_character = registry["characters"][identity]
        audit.add(prefix + ":character_neutral_png:" + identity, "neutral" in character.get("portraits", {})
                  and character.get("name") == source_character["name"])
        for emotion, filename in character.get("portraits", {}).items():
            path = output / filename
            original = Path(source_character["portraits"][emotion])
            original = original if original.is_absolute() else root / original
            audit.add(prefix + ":character_portrait:" + identity + ":" + emotion,
                      path.is_file() and original.is_file() and path.suffix.lower() == ".png"
                      and file_hash(path) == file_hash(original)
                      and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n")


def verify_native_campaign(name, root=ROOT, write=True, *, skip_save_bytes=False, expected_turns=None):
    name_ok(name)
    root = Path(root)
    directory = root / "runs/campaigns" / name
    audit = Audit(root)
    manifest = audit.read(directory / "manifest.json", "manifest")
    total_actions, total_decisions, complete_turns = 0, 0, 0
    readiness_usage = {}
    if manifest is not None:
        config, entries = manifest["config"], manifest["turns"]
        tags, identity = config["countries"], manifest["game_identity"]
        turn_limit = config["turn_limit"]
        audit.add("frozen_config_sha256", config_digest(config) == manifest.get("config_sha256"))
        switch_steps = audit_model_switches(audit, manifest)
        audit.add("native_five_country_bounded_campaign_contract", manifest.get("schema") == "iron.native-campaign.v1" and len(set(tags)) == len(tags) == 5
                  and config.get("execution_backend") == "native" and type(turn_limit) is int and 1 <= turn_limit <= 50
                  and type(config["step_hours"]) is int and 1 <= config["step_hours"] <= 360)
        if expected_turns is not None:
            audit.add("requested_turn_count", turn_limit == expected_turns)
        audit.add("initial_raw_clock", type(manifest.get("initial_date_raw")) is int)
        complete = manifest.get("status") == "completed" and len(entries) == turn_limit and all(row.get("status") == "completed" for row in entries)
        audit.add("campaign_committed_completion", True if complete else None)
        audit.add("ordered_turn_indices", [row["index"] for row in entries] == list(range(1, len(entries) + 1)))
        journals = {path.stem: read(path) for path in (directory / "native_requests").glob("*.json")}
        audit.add("unique_native_journal_ids", len(journals) == len({row.get("intent", {}).get("request_id") for row in journals.values()}))
        before = audit_capture(audit, directory / "initial_capture", tags, manifest["initial_date_raw"], identity, "initial_capture")
        all_usage, returned_models = Counter(), set()
        for entry in entries:
            index, prefix = entry["index"], "turn_" + str(entry["index"])
            if entry.get("status") != "completed":
                audit.add(prefix + ":committed_completion", None)
                continue

            def turn_audit():
                nonlocal before, total_actions, total_decisions, complete_turns
                trial, turn = directory / "trials" / f"turn_{index:03d}", directory / "turns" / f"turn_{index:03d}"
                execution = directory / "native_turns" / f"turn_{index:03d}"
                result, native = read(turn / "result.json"), read(execution / "result.json")
                start_raw = before["manifest"]["date_raw"]
                elapsed = native["step_hours"]
                selection_path = trial / "clock_selection.json"
                selection = read(selection_path) if selection_path.exists() else {"step_hours": config["step_hours"], "reason": "fixed"}
                if "clock_policy" in config:
                    previous_events = [] if index == 1 else read(directory / "turns" / f"turn_{index-1:03d}" / "public_events.json")
                    expected_selection = select_step_hours(at_war=bool(before["world"].get("public_wars")),
                                                           public_events=previous_events, policy=config["clock_policy"])
                    audit.add(prefix + ":frozen_adaptive_clock_selection", selection_path.is_file() and selection == expected_selection)
                after = audit_capture(audit, directory / "captures" / f"after_{index:03d}", tags, start_raw + elapsed, identity, prefix + ":capture")
                observed = {tag: read(trial / "observations" / (tag + ".json")) for tag in tags}
                for tag, observation in observed.items():
                    own = dict(observation); observation_id = own.pop("observation_id")
                    check_history(observation.get("recent_history", []), tag)
                    audit.add(prefix + ":observation_identity_hash:" + tag, observation_id == observation_digest(own)
                              and observation["country"] == tag and observation["match_id"] == name and observation["turn_id"] == index
                              and observation["date_raw"] == start_raw)
                    snap = before["snapshots"][tag]
                    audit.add(prefix + ":observation_matches_frozen_snapshot:" + tag,
                              observation["divisions"] == [{"id": row["unit_id"], "location": row["province"], "path": row.get("path", [])} for row in snap["divisions"]["units"]]
                              and observation["date_raw"] == before["manifest"]["date_raw"]
                              and observation["source"].get("capture") == before["manifest"])
                    previous = directory / "trials" / f"turn_{index-1:03d}"
                    expected_history = [] if index == 1 else own_history(previous, read(directory / "turns" / f"turn_{index-1:03d}" / "result.json"), tag)
                    if expected_history and "clock_policy" in config:
                        expected_history[-1]["public_events"] = read(directory / "turns" / f"turn_{index-1:03d}" / "result.json").get("public_events", [])
                    audit.add(prefix + ":exact_own_history_carry:" + tag, observation.get("recent_history", []) == expected_history)
                audit.add(prefix + ":compatibility_preserves_native_result", result.get("native_result") == native
                          and result.get("elapsed_game_hours") == elapsed)
                decisions = read(trial / "decision_result.json")
                requests = audit_sealed(audit, trial, observed, decisions, result, prefix, config["action_limit"])
                plan = to_native_plan(requests, observed, step_hours=selection["step_hours"], label=f"{name} turn {index}", save_basename=f"iron_{name}_t{index}")
                if "policy" in selection:
                    plan.update(clock_policy=selection["policy"], clock_selection={"reason": selection["reason"], "shared_clock": True})
                audit.add(prefix + ":unchanged_native_plan", plan == read(execution / "plan.json"))
                audit_clock(audit, manifest["initial_date_raw"], selection["step_hours"], index, native, after["manifest"], entry, prefix, start_raw=start_raw)
                audit_native_execution(audit, directory, execution, plan, native, result, journals, identity, prefix, skip_save_bytes=skip_save_bytes)
                audience_config = config
                for switch in manifest.get("model_switches", []):
                    if switch["first_affected_turn"] <= index:
                        audience_config = apply_model_switch(audience_config, switch["spec"])
                calls = audit_players(audit, trial, config, observed, decisions, prefix, switch_steps,
                                      audience_config=audience_config, completed_at=entry.get("completed_at"))
                usage = model_usage(trial)
                audit.add(prefix + ":actual_provider_usage_totals", usage == entry.get("usage") and usage["calls_without_usage"] == 0)
                all_usage.update(usage)
                returned_models.update(row["metadata"].get("returned_model") for row in calls.values())
                for key in ("model_seconds", "native_seconds", "elapsed_seconds"):
                    audit.add(prefix + ":recorded_" + key, isinstance(entry.get(key), (int, float)) and entry[key] >= 0)
                output = directory / ("episodes_postmatch" if entry.get("audience_completed_at") else "episodes") / f"turn_{index:03d}"
                deferred = config.get("audience_timing") == "postmatch" and not entry.get("audience_completed_at")
                audit_episode(audit, output, result, prefix + ":episode")
                audit_export_sources(audit, output, trial, turn, prefix + ":episode",
                                     strict_evidence=bool(config["show"].get("strict_public_context")), audience_deferred=deferred)
                audit_production(audit, output, config, root, prefix + ":production")
                commentary_call = trial / "audience_commentary/model_call.json"
                if not deferred or commentary_call.exists():
                    call = read(commentary_call)
                    effective = effective_config_at(switch_steps, config, call.get("metadata", {}).get("started_at"))
                    if entry.get("audience_completed_at") and call.get("metadata", {}).get("started_at", "") > entry.get("completed_at", ""):
                        effective = audience_config
                    audit_model(audit, call, effective["commentary_model"], prefix + ":commentary_API")
                before = after
                total_actions += sum(len(row["actions"]) for row in requests.values())
                total_decisions += len(requests)
                complete_turns += 1

            audit.section(prefix + ":record_structure", turn_audit)
        audit.add("campaign_provider_usage_totals", dict(all_usage) == manifest.get("usage"))
        audit.add("all_sealed_decisions", total_decisions == len(tags) * turn_limit if complete else None)
        index_path = directory / "index.html"
        committed_indices = [entry["index"] for entry in entries if entry.get("status") == "completed"]
        audit.add("campaign_index_export", index_path.is_file() and all(
            f'episodes/turn_{index:03d}/index.html' in index_path.read_text(encoding="utf-8")
            and (directory / "episodes" / f"turn_{index:03d}" / "index.html").is_file() for index in
            committed_indices) if committed_indices else None)
        post_prefixes = ("post_unit ", "post_declare ", "air_post ", "air_region_post ", "navy_post ", "navy_regions_post ")
        committed_run_ids = {read(directory / "native_turns" / f"turn_{index:03d}" / "result.json")["run_id"] for index in committed_indices}
        journal_posts = {rid for rid, row in journals.items() if row.get("intent", {}).get("payload", "").startswith(post_prefixes)}
        paths = list((directory / "native_turns").glob("turn_*/audience_facts.jsonl")) if complete else [directory / "native_turns" / f"turn_{index:03d}" / "audience_facts.jsonl" for index in committed_indices]
        event_posts = [row["request_id"] for path in paths
                       for row in [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
                       if row.get("event") == "intent" and row.get("payload", "").startswith(post_prefixes)]
        if not complete:
            journal_posts = {rid for rid in journal_posts if rid.split(".", 1)[0] in committed_run_ids}
        audit.add("committed_native_orders_once", len(event_posts) == len(set(event_posts))
                  and set(event_posts) == journal_posts and (not complete or len(event_posts) == total_actions))
        audit.add("no_unreferenced_or_replayed_native_orders", True if complete and len(event_posts) == len(set(event_posts)) and set(event_posts) == journal_posts and len(event_posts) == total_actions else None if not complete else False)
        ready_path = directory / "provider_check.json"
        if ready_path.exists():
            ready = read(ready_path)
            metadata = ready.get("metadata", {})
            readiness_usage = {"calls": 1, **{key: metadata.get("usage", {}).get(key, 0)
                                             for key in ("prompt_tokens", "completion_tokens", "total_tokens")}}
            audit.add("recorded_provider_readiness", ready.get("value") == {"ready": True}
                      and metadata.get("requested_model") == metadata.get("returned_model") == config["model"]["model"]
                      and bool(metadata.get("response_id")))
    counts = Counter(row["status"] for row in audit.checks)
    status = "failed" if counts["failed"] else "incomplete" if counts["missing"] or counts["unknown"] else "passed"
    report = {"schema": "iron.native-campaign-verification.v1", "name": name, "status": status,
              "checked_at": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
              "turns": complete_turns, "sealed_decisions": total_decisions, "actions": total_actions,
              "usage": dict(all_usage) if manifest is not None else {},
              "provider_readiness_usage": readiness_usage,
              "all_provider_usage": {key: all_usage.get(key, 0) + readiness_usage.get(key, 0)
                                     for key in ("calls", "prompt_tokens", "completion_tokens", "total_tokens")} if manifest is not None else {},
              "returned_models": sorted(returned_models) if manifest is not None else [],
              "elapsed_seconds": manifest.get("elapsed_seconds") if manifest is not None else None,
              "save_bytes_checked": not skip_save_bytes, "checks": audit.checks}
    if write:
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "verification.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(directory / "verification.json")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--skip-save-bytes", action="store_true", help="Skip save-file hashing for cheap partial-run checks; final audit hashes all saves")
    parser.add_argument("--expected-turns", type=int, help="Require the frozen campaign to target this many turns")
    args = parser.parse_args()
    report = verify_native_campaign(args.name, skip_save_bytes=args.skip_save_bytes, expected_turns=args.expected_turns)
    print(json.dumps({key: value for key, value in report.items() if key != "checks"}, ensure_ascii=False))
    issues = [row for row in report["checks"] if row["status"] != "passed"]
    if issues:
        print(json.dumps({"issues": issues}, ensure_ascii=False))
    raise SystemExit({"passed": 0, "failed": 1, "incomplete": 2}[report["status"]])


if __name__ == "__main__":
    main()
