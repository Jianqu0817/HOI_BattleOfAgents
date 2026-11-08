"""Offline audit of the recorded five-country native campaign; no game/API calls."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from campaign import ROOT, config_digest, model_usage, name_ok
from model_trial import digest, native_brief, normalize_request, own_history, private_context, validate_trial_request
from native_capture import BUILD, GAME_SHA256
from native_observation import _digest as observation_digest, to_native_plan
from native_turn import NativeTurn
from showrunner import check_history, check_intent, check_replies, ledger, visible_promises
from verify_campaign import Audit, audit_episode, audit_model


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def response(record):
    return record.get("normalized_response", record["response"])


def audit_clock(audit, initial, hours, index, native, captured, entry, prefix):
    expected = initial + hours * index
    audit.add(prefix + ":exact_cumulative_raw_clock", native.get("step_hours") == hours
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
    repair = record.get("annotation_repair")
    if repair is None:
        return
    repaired = calls.get(repair, {})
    original, normalized = record["response"], response(record)

    def speech(value):
        return {k: value.get(k) for k in ("country", "to", "message")} | {
            "replies": [{k: row.get(k) for k in ("to", "message")} for row in value.get("replies", [])]}

    def conditions(value):
        return [[{key: promise.get(key) for key in ("to", "due_turn", "condition")}
                 for promise in group.get("commitments", [])] for group in [value] + value.get("replies", [])]

    same_conditions = conditions(original) == conditions(normalized)
    repair_payload = json.loads(repaired.get("messages", [{}, {"content": "{}"}])[1]["content"])
    if repair_payload.get("validation_error") == "A message permits at most three commitments":
        same_conditions = len(normalized.get("commitments", [])) == 3 and all(
            promise in original.get("commitments", []) for promise in normalized["commitments"])
    audit.add(label + ":annotation_preserves_literal_speech", repaired.get("response") == normalized
              and speech(original) == speech(normalized) and same_conditions)


def audit_players(audit, trial, config, observed, decisions, prefix):
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
        expected = (config["show"]["referee_model"] if phase == "referee" else
                    {**config["model"], **config["show"].get("models", {}).get(tag, {})})
        cap_phase = label.split("_")[0]
        cap = config["show"].get("phase_max_tokens", {}).get(cap_phase)
        if cap is not None:
            expected = {**expected, "max_tokens": min(expected["max_tokens"], cap)}
        start, end = audit_model(audit, record, expected, prefix + ":API:" + label)
        usage = record.get("metadata", {}).get("usage", {})
        audit.add(prefix + ":API_usage:" + label, all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
                  and usage.get("total_tokens") == usage.get("prompt_tokens", -1) + usage.get("completion_tokens", -1)
                  and isinstance(record.get("metadata", {}).get("elapsed_seconds"), (int, float)))
        response_ids.append(record.get("metadata", {}).get("response_id"))
        if phase == "referee":
            payload = json.loads(record["messages"][1]["content"])
            audit.add(prefix + ":referee_original_response", record["response"] == decisions.get("referee"))
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
        audit.add(prefix + ":own_native_projection:" + label, own == native_brief(observed[tag]))
        audit.add(prefix + ":own_profile:" + label, payload.get("own_profile") == config["show"]["profiles"].get(tag, {}))
        audit.add(prefix + ":no_audience_or_admin_payload:" + label,
                  set(payload) <= {"task", "own_observation", "public_diplomacy", "own_private_diplomacy", "match_rules", "game_guide", "own_profile", "participants", "turn_hours", "own_score_context", "own_commitment_memory", "commitment_memory_notice", "own_pre_diplomacy_intent"})
        promises = payload.get("own_commitment_memory", [])
        audit.add(prefix + ":own_visible_commitments:" + label, len(visible_promises(promises, tag)) == len(promises))
        if phase == "intent":
            audit.add(prefix + ":intent_inputs:" + tag, payload["public_diplomacy"] == [] and record["response"] == intents[tag])
            if end:
                intent_ends.append(end)
        else:
            audit.add(prefix + ":own_intent:" + label, payload.get("own_pre_diplomacy_intent") == intents[tag])
        if phase == "diplomacy":
            number = 2 if label.startswith("diplomacy_2_") else 1
            audit.add(prefix + ":frozen_public_round:" + label, payload["public_diplomacy"] == [row for row in public if row["round"] < number])
            rows = [row for row in public if row["country"] == tag and row["round"] == number]
            audit.add(prefix + ":literal_public_response:" + label, len(rows) == 1 and {k: v for k, v in rows[0].items() if k != "round"} == response(record))
            if start:
                public_starts.append(start)
        elif phase != "intent":
            audit.add(prefix + ":complete_public_inputs:" + label, payload["public_diplomacy"] == public)
        if phase in ("decision", "reply"):
            audit.add(prefix + ":addressed_private_inputs:" + label,
                      payload.get("own_private_diplomacy") == private_context(tag, initial if phase == "reply" else private))
        elif "own_private_diplomacy" in payload:
            audit.add(prefix + ":no_early_inbox:" + label, payload["own_private_diplomacy"] in (None, {"sent": [], "received": []}))
    for tag in tags:
        audit.add(prefix + ":mailbox_routes:" + tag, read(trial / "private_diplomacy" / (tag + ".json")) == private_context(tag, private))
    audit.add(prefix + ":unique_actual_response_ids", all(response_ids) and len(response_ids) == len(set(response_ids)))
    audit.add(prefix + ":intents_precede_all_public_rounds", len(intent_ends) == len(tags) and bool(public_starts) and max(intent_ends) <= min(public_starts))
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
    audit.add(prefix + ":single_bounded_clock_effect", len(steps) == len(clock) == 1 and steps[0]["payload"] == f"step {plan['step_hours']}"
              and clock[0]["requested_hours"] == clock[0]["observed_hour_delta"] == plan["step_hours"]
              and clock[0]["state"]["paused"] is True and clock[0]["state"]["date_raw"] == native["date_raw"])
    saved = native["save"]
    path = Path(saved["path"])
    audit.add(prefix + ":save_acknowledged_verified", saved.get("file_verified") is True and saved.get("handler_ack", {}).get("ok") is True
              and compatibility["final_save"].get("sha256") == saved.get("sha256"))
    if not skip_save_bytes:
        audit.add(prefix + ":actual_save_bytes_sha256", path.is_file() and path.stat().st_size == saved["bytes"] and file_hash(path) == saved["sha256"])
    audit.add(prefix + ":native_session_identity", native.get("status") == "completed"
              and all(native.get(k) == v for k, v in identity.items()) and native.get("initial_country_restored") is not None)


def audit_export_sources(audit, output, trial, turn, prefix):
    from episode import validate_commentary
    episode = read(output / "episode.json")
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
    commentary = read(trial / "audience_commentary/commentary.json")
    call = read(trial / "audience_commentary/model_call.json")
    audit.add(prefix + ":commentary_audience_only", commentary.get("audience") == call.get("audience") == "audience_full" and commentary.get("content") == call.get("response"))
    base = {**episode, "events": [row for row in episode["events"] if row["kind"] != "commentary"]}
    validate_commentary(commentary["content"], base)
    audit.add(prefix + ":commentary_quotes_current_evidence", True)


def verify_native_campaign(name, root=ROOT, write=True, *, skip_save_bytes=False):
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
        audit.add("frozen_config_sha256", config_digest(config) == manifest.get("config_sha256"))
        audit.add("native_five_country_five_turn_contract", manifest.get("schema") == "iron.native-campaign.v1" and len(set(tags)) == len(tags) == 5
                  and config.get("execution_backend") == "native" and config["turn_limit"] == 5 and config["step_hours"] == 360)
        audit.add("initial_raw_clock", manifest.get("initial_date_raw") == 60762795)
        complete = manifest.get("status") == "completed" and len(entries) == 5 and all(row.get("status") == "completed" for row in entries)
        audit.add("campaign_committed_completion", True if complete else None)
        audit.add("ordered_turn_indices", [row["index"] for row in entries] == list(range(1, len(entries) + 1)))
        journals = {path.stem: read(path) for path in (directory / "native_requests").glob("*.json")}
        audit.add("unique_native_journal_ids", len(journals) == len({row.get("intent", {}).get("request_id") for row in journals.values()}))
        before = audit_capture(audit, directory / "initial_capture", tags, manifest["initial_date_raw"], identity, "initial_capture")
        all_usage, returned_models = Counter(), set()
        for entry in entries:
            index, prefix = entry["index"], "turn_" + str(entry["index"])
            if entry.get("status") != "completed":
                audit.add(prefix + ":committed_completion", status="missing")
                continue

            def turn_audit():
                nonlocal before, total_actions, total_decisions, complete_turns
                trial, turn = directory / "trials" / f"turn_{index:03d}", directory / "turns" / f"turn_{index:03d}"
                execution = directory / "native_turns" / f"turn_{index:03d}"
                after = audit_capture(audit, directory / "captures" / f"after_{index:03d}", tags, manifest["initial_date_raw"] + index * config["step_hours"], identity, prefix + ":capture")
                observed = {tag: read(trial / "observations" / (tag + ".json")) for tag in tags}
                for tag, observation in observed.items():
                    own = dict(observation); observation_id = own.pop("observation_id")
                    check_history(observation.get("recent_history", []), tag)
                    audit.add(prefix + ":observation_identity_hash:" + tag, observation_id == observation_digest(own)
                              and observation["country"] == tag and observation["match_id"] == name and observation["turn_id"] == index
                              and observation["date_raw"] == manifest["initial_date_raw"] + (index - 1) * config["step_hours"])
                    snap = before["snapshots"][tag]
                    audit.add(prefix + ":observation_matches_frozen_snapshot:" + tag,
                              observation["divisions"] == [{"id": row["unit_id"], "location": row["province"], "path": row.get("path", [])} for row in snap["divisions"]["units"]]
                              and observation["date_raw"] == before["manifest"]["date_raw"]
                              and observation["source"].get("capture") == before["manifest"])
                    previous = directory / "trials" / f"turn_{index-1:03d}"
                    expected_history = [] if index == 1 else own_history(previous, read(directory / "turns" / f"turn_{index-1:03d}" / "result.json"), tag)
                    audit.add(prefix + ":exact_own_history_carry:" + tag, observation.get("recent_history", []) == expected_history)
                result, native = read(turn / "result.json"), read(execution / "result.json")
                audit.add(prefix + ":compatibility_preserves_native_result", result.get("native_result") == native
                          and result.get("elapsed_game_hours") == config["step_hours"])
                decisions = read(trial / "decision_result.json")
                requests = audit_sealed(audit, trial, observed, decisions, result, prefix, config["action_limit"])
                plan = to_native_plan(requests, observed, step_hours=config["step_hours"], label=f"{name} turn {index}", save_basename=f"iron_{name}_t{index}")
                audit.add(prefix + ":unchanged_native_plan", plan == read(execution / "plan.json"))
                audit_clock(audit, manifest["initial_date_raw"], config["step_hours"], index, native, after["manifest"], entry, prefix)
                audit_native_execution(audit, directory, execution, plan, native, result, journals, identity, prefix, skip_save_bytes=skip_save_bytes)
                calls = audit_players(audit, trial, config, observed, decisions, prefix)
                usage = model_usage(trial)
                audit.add(prefix + ":actual_provider_usage_totals", usage == entry.get("usage") and usage["calls_without_usage"] == 0)
                all_usage.update(usage)
                returned_models.update(row["metadata"].get("returned_model") for row in calls.values())
                for key in ("model_seconds", "native_seconds", "elapsed_seconds"):
                    audit.add(prefix + ":recorded_" + key, isinstance(entry.get(key), (int, float)) and entry[key] >= 0)
                output = directory / "episodes" / f"turn_{index:03d}"
                audit_episode(audit, output, result, prefix + ":episode")
                audit_export_sources(audit, output, trial, turn, prefix + ":episode")
                call = read(trial / "audience_commentary/model_call.json")
                audit_model(audit, call, config["commentary_model"], prefix + ":commentary_API")
                before = after
                total_actions += sum(len(row["actions"]) for row in requests.values())
                total_decisions += len(requests)
                complete_turns += 1

            audit.section(prefix + ":record_structure", turn_audit)
        audit.add("campaign_provider_usage_totals", dict(all_usage) == manifest.get("usage"))
        audit.add("twenty_five_sealed_decisions", total_decisions == 25 if complete else None)
        index_path = directory / "index.html"
        audit.add("campaign_index_export", index_path.is_file() and all(
            f'episodes/turn_{index:03d}/index.html' in index_path.read_text(encoding="utf-8")
            and (directory / "episodes" / f"turn_{index:03d}" / "index.html").is_file() for index in range(1, 6)))
        post_prefixes = ("post_unit ", "post_declare ", "air_post ", "air_region_post ", "navy_post ", "navy_regions_post ")
        journal_posts = {rid for rid, row in journals.items() if row.get("intent", {}).get("payload", "").startswith(post_prefixes)}
        event_posts = [row["request_id"] for path in (directory / "native_turns").glob("turn_*/audience_facts.jsonl")
                       for row in [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
                       if row.get("event") == "intent" and row.get("payload", "").startswith(post_prefixes)]
        audit.add("no_unreferenced_or_replayed_native_orders", len(event_posts) == len(set(event_posts))
                  and set(event_posts) == journal_posts and (not complete or len(event_posts) == total_actions))
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
    parser.add_argument("--skip-save-bytes", action="store_true", help="Offline fixtures only; real campaign audit hashes all five saves")
    args = parser.parse_args()
    report = verify_native_campaign(args.name, skip_save_bytes=args.skip_save_bytes)
    print(json.dumps({key: value for key, value in report.items() if key != "checks"}, ensure_ascii=False))
    issues = [row for row in report["checks"] if row["status"] != "passed"]
    if issues:
        print(json.dumps({"issues": issues}, ensure_ascii=False))
    raise SystemExit({"passed": 0, "failed": 1, "incomplete": 2}[report["status"]])


if __name__ == "__main__":
    main()
