"""Offline audit of frozen campaign records; never operates the game or calls a model."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

from agent_contract import country_fingerprint
from campaign import config_digest, name_ok
from m0_probe import ROOT, game_hours
from model_trial import digest, normalize_request, private_context, validate_trial_request
from showrunner import check_history, check_intent, check_replies, ledger, model_settings, visible_promises


class Audit:
    def __init__(self, root):
        self.root = Path(root)
        self.checks = []

    def add(self, label, passed=None, detail=None, status=None):
        row = {"check": label, "status": status or ("unknown" if passed is None else "passed" if passed else "failed")}
        if detail:
            row["detail"] = str(detail)
        self.checks.append(row)

    def read(self, path, label=None):
        path = Path(path)
        if not path.is_file():
            self.add(label or str(path), status="missing", detail="Record is not available")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            self.add(label or str(path), status="unknown", detail="Record cannot be read as complete JSON")
            return None

    def section(self, label, function):
        try:
            return function()
        except (KeyError, TypeError, ValueError, OSError, IndexError) as error:
            self.add(label, False, type(error).__name__ + ": " + str(error))

    def stamp(self, value, label):
        if not value:
            self.add(label, status="unknown", detail="No recorded API/action UTC timestamp; file mtime is not evidence")
            return None
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
                raise ValueError("Timestamp must have a UTC offset")
            return stamp
        except (AttributeError, ValueError):
            self.add(label, False, "Invalid UTC timestamp")
            return None


def resolved(root, value):
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def same_date(a, b):
    return game_hours(a) == game_hours(b)


def audit_receipt(audit, value, label, date, tags=None):
    if not value:
        audit.add(label, status="missing", detail="Engine receipt unavailable")
        return
    rows = value["receipts"]
    audit.add(label, bool(rows) and all(row["country"] == row["player"] and same_date(row["date"], date) for row in rows)
              and same_date(value["date"], date) and (tags is None or set(row["country"] for row in rows) == set(tags)))


def audit_model(audit, record, expected, label):
    metadata = record.get("metadata", {})
    if "content_normalization" in metadata:
        from model_client import parse_json_content
        receipt = metadata["content_normalization"]
        try:
            parsed, expected_receipt = parse_json_content(receipt["raw_content"])
            valid = expected_receipt == receipt and parsed == record.get("response")
        except (ValueError, KeyError, TypeError):
            valid = False
        check = "whole_json_fence_receipt" if isinstance(receipt, dict) and receipt.get("method") == "whole_json_fence.v1" else "json_content_normalization_receipt"
        audit.add(label + ":" + check, valid)
    audit.add(label + ":requested_model", metadata.get("requested_model") == expected["model"])
    audit.add(label + ":returned_model", True if metadata.get("returned_model") == expected["model"] else None,
              detail=None if metadata.get("returned_model") == expected["model"] else "Provider model identifier is absent or differs; alias equivalence is not proven")
    saved = record.get("request_config", record.get("settings"))
    if saved is None or "base_url" not in saved:
        audit.add(label + ":provider", status="unknown", detail="No provider fingerprint")
    else:
        wanted = model_settings(expected)
        required = {"base_url", "model", "enable_thinking", "temperature", "max_tokens"} | (set(wanted) & {"include_thinking_parameter", "json_mode", "api_format", "omit_temperature", "request_parameters"})
        audit.add(label + ":provider_parameters", saved == wanted if "request_config" in record else
                  required <= set(saved) and all(key in wanted and wanted[key] == value for key, value in saved.items()))
    start = audit.stamp(metadata.get("started_at"), label + ":started_at")
    end = audit.stamp(metadata.get("ended_at"), label + ":ended_at")
    if start is not None and end is not None:
        audit.add(label + ":time_order", start <= end)
    return start, end


def audit_player_payload(audit, record, tag, observation, intent, prefix):
    payload = json.loads(record["messages"][1]["content"])
    own = payload["own_observation"]
    identity = own["identity"]
    audit.add(prefix + ":own_identity", all(identity.get(key) == observation[key] for key in
              ("country", "match_id", "turn_id", "date", "observation_id")))
    check_history(own.get("recent_private_history", []), tag)
    audit.add(prefix + ":structured_history_private_routes", True)
    if "own_pre_diplomacy_intent" in payload:
        check_intent(payload["own_pre_diplomacy_intent"], tag)
        audit.add(prefix + ":own_intent", payload["own_pre_diplomacy_intent"] == intent)
    promises = payload.get("own_commitment_memory", [])
    audit.add(prefix + ":commitment_memory_visibility", len(visible_promises(promises, tag)) == len(promises))
    audit.add(prefix + ":no_administrator_collections", not ({"audience_commentary", "all_private_decisions", "private_decisions", "private_intents"} & set(payload)))
    return payload


def audit_execution(audit, turn, result, requests, observations, hours, prefix):
    from diplomacy_actions import diplomacy_fingerprint, verify_declaration
    from naval_air_actions import naval_air_fingerprint, verify_naval_air_action
    from army_actions import army_activity
    start = next(iter(observations.values()))["date"]
    expected = {(tag, action["id"]): action for tag, request in requests.items() for action in request["actions"]}
    applied, succeeded = [], set()
    before = result.get("before_save")
    previous = audit.read(turn / (before["name"].removeprefix("hoi4live_") + ".json"), prefix + ":before_state") if before else None
    if before:
        audit.add(prefix + ":before_date", same_date(before["parsed_date"], start))
        audit_receipt(audit, result.get("initial_switch"), prefix + ":initial_actors", start, requests)
        if previous and any(row.get("diplomacy", {}).get("baseline_sha256") for row in observations.values()):
            original = {row.get("diplomacy", {}).get("baseline_sha256") for row in observations.values()}
            proof = result.get("diplomacy_baseline_verification")
            audit.add(prefix + ":separate_diplomacy_baseline", original == {diplomacy_fingerprint(previous)}
                      and isinstance(proof, dict) and proof.get("verified") is True)
        if previous and any(row.get("naval_air", {}).get("state_fingerprint") for row in observations.values()):
            proof = result.get("naval_air_baseline_verification")
            audit.add(prefix + ":separate_naval_air_baseline", isinstance(proof, dict) and proof.get("verified") is True
                      and all(row.get("naval_air", {}).get("state_fingerprint") == naval_air_fingerprint(previous, tag) for tag, row in observations.items()))
        if previous and any("army_activity" in row for row in observations.values()):
            proof = result.get("army_activity_baseline_verification")
            audit.add(prefix + ":separate_army_activity_baseline", isinstance(proof, dict) and proof.get("verified") is True
                      and all(row.get("army_activity") == army_activity(previous["countries"][tag]) for tag, row in observations.items()))
    else:
        audit.add(prefix + ":before_save", status="missing")
    for index, entry in enumerate(result.get("actions", [])):
        label = prefix + f":attempt{index}"
        key = entry["country"], entry["action"]["id"]
        audit.add(label + ":original_action", key in expected and entry["action"] == expected.get(key))
        audit.add(label + ":no_attempt_after_success", key not in succeeded)
        audit_receipt(audit, entry.get("actor_receipt"), label + ":actor", start, [entry["country"]])
        status = entry["status"]
        if status in {"applied", "not_applied"}:
            checks = entry.get("verification", {}).get("checks", {})
            audit.add(label + ":reported_paused_peer_checks", checks.get("same_date") is True and checks.get("peers_unchanged") is True)
        if status == "applied":
            applied.append(key)
            succeeded.add(key)
            proof = entry.get("verification", {})
            audit.add(label + ":verified", proof.get("verified") is True and proof.get("checks", {}).get("same_date") is True
                      and proof.get("checks", {}).get("peers_unchanged") is True)
        elif status not in {"not_applied", "rejected"}:
            audit.add(label + ":resolved", status="missing" if result.get("status") != "completed_deterministic_turn" else "failed", detail="Attempt outcome unresolved")
        save = entry.get("save")
        if save:
            after = audit.read(turn / (save["name"].removeprefix("hoi4live_") + ".json"), label + ":after_state")
            if after and previous:
                audit.add(label + ":paused_date", same_date(previous["date"], start) and same_date(after["date"], start))
                audit.add(label + ":peers_independent", all(country_fingerprint(previous, tag) == country_fingerprint(after, tag)
                          for tag in requests if tag != entry["country"]))
                audit.add(label + ":peers_naval_air_independent", all(naval_air_fingerprint(previous, tag) == naval_air_fingerprint(after, tag)
                          for tag in requests if tag != entry["country"]))
                if status == "not_applied":
                    audit.add(label + ":not_applied_has_no_gameplay_effect", all(country_fingerprint(previous, tag) == country_fingerprint(after, tag) for tag in requests)
                              and diplomacy_fingerprint(previous) == diplomacy_fingerprint(after))
                    audit.add(label + ":not_applied_naval_air_unchanged", all(naval_air_fingerprint(previous, tag) == naval_air_fingerprint(after, tag) for tag in requests))
                    audit.add(label + ":not_applied_army_activity_unchanged", all(army_activity(previous["countries"][tag]) == army_activity(after["countries"][tag]) for tag in requests))
                if entry["action"]["type"].startswith(("air.", "navy.")) and status == "applied":
                    checked = verify_naval_air_action(entry["action"], previous, after, entry["country"])
                    audit.add(label + ":naval_air_state_effect", checked["verified"] is True and entry.get("effect_status") == checked["effect_status"])
                if entry["action"]["type"] == "diplomacy.declare_war" and status == "applied":
                    checked = verify_declaration(entry["action"], previous, after, entry["country"])
                    saved = entry.get("verification", {})
                    audit.add(label + ":declaration_state_effect", checked["verified"] is True
                              and entry.get("effect_status") == checked["effect_status"])
                    audit.add(label + ":declaration_new_vs_already_present", all(saved.get("observations", {}).get(key) == checked["observations"][key]
                              for key in ("new_war", "declaration_sent", "war_pairs_added", "target_country")))
                    ui = entry.get("ui_result")
                    if ui is None:
                        audit.add(label + ":declaration_UI_receipt", status="unknown", detail="War state is proven; the UI receipt is unavailable")
                    else:
                        audit.add(label + ":declaration_UI_receipt", ui.get("declaration_sent") is checked["observations"]["declaration_sent"]
                                  and (ui.get("input_path") == "none" if checked["effect_status"] == "already_present" else ui.get("input_path") == "ordinary UI"))
            previous = after
        else:
            audit.add(label + ":after_state", status="missing")
    complete = result.get("status") == "completed_deterministic_turn"
    audit.add(prefix + ":all_original_actions_applied_once", Counter(applied) == Counter({key: 1 for key in expected}) if complete else None,
              detail=None if complete else "Execution has not committed completion")
    if complete:
        audit_receipt(audit, result.get("before_clock"), prefix + ":before_clock_actors", start, requests)
    advanced = 0
    for index, step in enumerate(result.get("clock_steps", [])):
        advanced += step["hours"]
        audit.add(prefix + f":clock_step_{index}", type(step["hours"]) is int and step["hours"] > 0
                  and step["advanced_hours"] == advanced and game_hours(step["receipt"]["date"]) - game_hours(start) == advanced)
        audit_receipt(audit, step["receipt"], prefix + f":clock_receipt_{index}", step["receipt"]["date"])
    audit.add(prefix + ":exact_common_clock", advanced == hours and result.get("elapsed_game_hours") == hours if complete else None)
    final = result.get("final_save")
    if final:
        state = audit.read(turn / (final["name"].removeprefix("hoi4live_") + ".json"), prefix + ":final_state")
        if state:
            audit.add(prefix + ":final_date", same_date(state["date"], final["parsed_date"])
                      and game_hours(state["date"]) - game_hours(start) == hours)
            audit_receipt(audit, result.get("after_clock"), prefix + ":final_actors", state["date"], requests)
            return state
    else:
        audit.add(prefix + ":final_save", status="missing")


def srt_windows(text):
    windows = []
    for line in text.splitlines():
        found = re.fullmatch(r"(\d+):(\d{2}):(\d{2}),(\d{3}) --> (\d+):(\d{2}):(\d{2}),(\d{3})", line)
        if found:
            values = list(map(int, found.groups()))
            windows.append(tuple(h * 3600 + m * 60 + s + ms / 1000 for h, m, s, ms in (values[:4], values[4:])))
    return windows


def audit_episode(audit, output, result, prefix):
    required = ("episode.json", "assets.json", "index.html", "timeline.jsonl", "timeline.csv", "recording_markers.csv",
                "subtitles.srt", "narration.md", "shotlist.md", "README.txt", "export_owner.json")
    for filename in required:
        audit.add(prefix + ":file:" + filename, True if (output / filename).is_file() else None,
                  status="passed" if (output / filename).is_file() else "missing")
    episode = audit.read(output / "episode.json", prefix + ":episode")
    if episode is None:
        return
    audit.add(prefix + ":audience_and_identity", episode.get("audience") == "audience_full"
              and episode["turn_id"] == result["turn_id"] and episode["match_id"] == result["match_id"])
    assets = audit.read(output / "assets.json", prefix + ":assets")
    audit.add(prefix + ":asset_inventory", assets == episode.get("assets"))
    for item in assets or []:
        path = (output / item["file"]).resolve()
        inside = path.is_relative_to(output.resolve())
        audit.add(prefix + ":asset:" + item["file"], inside and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"])
    windows = []
    srt = output / "subtitles.srt"
    if srt.is_file():
        windows = srt_windows(srt.read_text(encoding="utf-8-sig"))
        audit.add(prefix + ":subtitle_order", bool(windows) and windows[0][0] == 0 and all(start < end for start, end in windows)
                  and all(first[1] <= second[0] for first, second in zip(windows, windows[1:])))
    timeline = output / "timeline.jsonl"
    if timeline.is_file():
        rows = [json.loads(line) for line in timeline.read_text(encoding="utf-8").splitlines() if line]
        audit.add(prefix + ":timeline_event_identity", [row["id"] for row in rows] == [row["id"] for row in episode["events"]])
        audit.add(prefix + ":editorial_time_is_relative", all(isinstance(row["editorial_timing"]["start"], (int, float))
                  and 0 <= row["editorial_timing"]["start"] <= row["editorial_timing"]["end"]
                  and (not windows or row["editorial_timing"]["end"] <= windows[-1][1] + .002) for row in rows))
    markers = output / "recording_markers.csv"
    if markers.is_file():
        with markers.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = set(reader.fieldnames or [])
            rows = list(reader)
        audit.add(prefix + ":separate_UTC_and_editorial_columns", {"recorded_at_utc", "suggested_editorial_start", "suggested_editorial_end"} <= columns)
        audit.add(prefix + ":actual_UTC_markers_present", True if rows else None)
        for index, row in enumerate(rows):
            stamp = audit.stamp(row.get("recorded_at_utc"), prefix + f":marker_{index}_UTC")
            if stamp and row.get("epoch"):
                audit.add(prefix + f":marker_{index}_epoch", abs(stamp.timestamp() - float(row["epoch"])) < .2)
            if row.get("kind") == "campaign_phase":
                audit.add(prefix + f":marker_{index}_not_video_time", not row.get("suggested_editorial_start") and not row.get("suggested_editorial_end"))


def audit_auxiliary_models(audit, trial, config, tags, turn_id, public, private, prefix):
    if config.get("summarize_lessons", True):
        from lessons import validate_lessons
        for tag in tags:
            label = prefix + ":lessons:" + tag
            value = audit.read(trial / "private_lessons" / (tag + ".json"), label)
            call = audit.read(trial / "private_lessons/model_calls" / (tag + ".json"), label + ":API")
            if value is None or call is None:
                continue
            expected = {**config["model"], **config["show"].get("models", {}).get(tag, {})}
            audit_model(audit, call, expected, label)
            context = json.loads(call["messages"][1]["content"])
            audit.add(label + ":own_context", context.get("country") == tag and context.get("turn_id") == turn_id
                      and call.get("country") == tag and call.get("audience") == "country_private")
            validate_lessons(value, context)
            audit.add(label + ":original_validated_response", value == call["response"])
            own_mail = private_context(tag, private)
            allowed = {"observation", "intent", "public", "private", "decision", "execution", "news", "outcome", "clock"}
            for index, row in enumerate(context["evidence"]):
                kind, data = row["kind"], row["data"]
                valid = kind in allowed
                if kind in {"observation", "intent", "decision", "execution", "outcome"}:
                    valid = valid and data.get("country") == tag
                elif kind == "private":
                    valid = valid and data in own_mail["sent"] + own_mail["received"]
                elif kind == "public":
                    valid = valid and data in public
                audit.add(label + f":evidence_route_{index}", valid)
    if config.get("model_commentary", True):
        label = prefix + ":audience_commentary"
        call = audit.read(trial / "audience_commentary/model_call.json", label + ":API")
        value = audit.read(trial / "audience_commentary/commentary.json", label)
        if call is not None and value is not None:
            audit_model(audit, call, config["commentary_model"], label)
            audit.add(label + ":audience_only_original_response", call.get("audience") == value.get("audience") == "audience_full"
                      and call["response"] == value.get("content"))


def audit_turn(audit, root, campaign_dir, config, entry):
    prefix = "turn_" + str(entry["index"])
    trial, turn = resolved(root, entry["trial"]), resolved(root, entry["turn"])
    tags = config["countries"]
    observations = {tag: audit.read(trial / "observations" / (tag + ".json"), prefix + ":observation:" + tag) for tag in tags}
    if any(value is None for value in observations.values()):
        return
    first = observations[tags[0]]
    source = first["source"]
    audit.add(prefix + ":same_source_snapshot", all(row["country"] == tag and all(row[key] == first[key] for key in ("match_id", "turn_id", "date"))
              and row["source"]["sha256"] == source["sha256"] for tag, row in observations.items()))
    source_path = resolved(root, source["path"])
    baseline = audit.read(source_path, prefix + ":source_state")
    if baseline:
        audit.add(prefix + ":source_sha256", hashlib.sha256(source_path.read_bytes()).hexdigest() == source["sha256"])
    for tag, row in observations.items():
        own = dict(row)
        observation_id = own.pop("observation_id", None)
        audit.add(prefix + ":observation_id:" + tag, observation_id == digest(own))
        if baseline:
            audit.add(prefix + ":observation_fingerprint:" + tag, row["state_fingerprint"] == country_fingerprint(baseline, tag)
                      and same_date(row["date"], baseline["date"]))
            if row.get("naval_air", {}).get("state_fingerprint"):
                from naval_air_actions import naval_air_fingerprint
                audit.add(prefix + ":naval_air_snapshot_fingerprint:" + tag, row["naval_air"]["state_fingerprint"] == naval_air_fingerprint(baseline, tag))
        check_history(row.get("recent_history", []), tag)
    result = audit.read(turn / "result.json", prefix + ":execution_result")
    decisions = audit.read(trial / "decision_result.json", prefix + ":decision_result")
    public = audit.read(trial / "public_diplomacy.json", prefix + ":public_diplomacy")
    if decisions is None or public is None:
        return
    calls = {path.stem: audit.read(path) for path in (trial / "model_calls").glob("*.json")}
    intents, initial, requests, public_times, intent_times, execution_times = {}, [], {}, [], [], []
    for tag in tags:
        if config["show"].get("intents", True):
            intents[tag] = audit.read(trial / "private_intents" / (tag + ".json"), prefix + ":intent:" + tag)
        initial_call = calls.get("private_" + tag)
        if initial_call:
            initial.append(initial_call["response"])
        required = ["private_" + tag, "decision_" + tag]
        required += ["diplomacy" + ("_" + str(number) if number > 1 else "") + "_" + tag for number in range(1, config["communication_rounds"] + 1)]
        if config["show"].get("intents", True):
            required.append("intent_" + tag)
        for label in required:
            if label not in calls:
                audit.add(prefix + ":required_API:" + label, status="missing")
    if "referee" not in calls:
        audit.add(prefix + ":required_API:referee", status="missing")
    all_private = list(initial)
    for tag in tags:
        reply = audit.read(trial / "private_replies" / (tag + ".json"), prefix + ":reply:" + tag) if config["show"].get("private_replies", True) else None
        if reply:
            check_replies(reply, tag, private_context(tag, initial)["received"], tags, first["turn_id"])
            if private_context(tag, initial)["received"]:
                reply_call = calls.get("reply_" + tag)
                audit.add(prefix + ":original_reply:" + tag, reply_call["response"] == reply if reply_call else None)
            all_private.extend({"country": tag, **row, "kind": "reply"} for row in reply["replies"])
    private_file = audit.read(trial / "private_messages.json", prefix + ":private_messages")
    audit.add(prefix + ":canonical_private_messages", private_file == all_private)
    commitment_file = audit.read(trial / "commitments.json", prefix + ":commitments")
    audit.add(prefix + ":literal_commitment_ledger", commitment_file == ledger(public, all_private, first["turn_id"]))
    audit.add(prefix + ":public_round_membership", all(sum(row["country"] == tag and row["round"] == number for row in public) == 1
              for tag in tags for number in range(1, config["communication_rounds"] + 1)) and len(public) == len(tags) * config["communication_rounds"])
    for tag in tags:
        request = audit.read(trial / "private_decisions" / (tag + ".json"), prefix + ":sealed:" + tag)
        executed = audit.read(trial / "requests" / (tag + ".json"), prefix + ":request:" + tag)
        if request is not None:
            validate_trial_request(request, observations[tag], config["action_limit"])
            requests[tag] = request
        call = calls.get("decision_" + tag)
        if call and request is not None:
            review = next((row for row in decisions.get("referee", {}).get("reviews", []) if row.get("country") == tag), {})
            audit.add(prefix + ":unchanged_approved_request:" + tag, normalize_request(call["response"]) == request == executed
                      and review.get("approved") is True and review.get("request_sha256") == digest(request)
                      and decisions.get("request_hashes", {}).get(tag) == digest(request)
                      and (result is None or result.get("requests", {}).get(tag) == request))
        else:
            audit.add(prefix + ":decision_call:" + tag, status="missing")
        mailbox = audit.read(trial / "private_diplomacy" / (tag + ".json"), prefix + ":mailbox:" + tag)
        audit.add(prefix + ":mailbox_routes:" + tag, mailbox == private_context(tag, all_private))
    response_ids = []
    for label, record in calls.items():
        if record is None:
            continue
        is_referee = label == "referee"
        tag = record.get("country") or (label.rsplit("_", 1)[-1] if not is_referee else None)
        expected = {**config["model"], **config["show"].get("models", {}).get(tag, {})} if not is_referee else config["show"]["referee_model"]
        start, end = audit_model(audit, record, expected, prefix + ":API:" + label)
        if record.get("metadata", {}).get("response_id"):
            response_ids.append(record["metadata"]["response_id"])
        else:
            audit.add(prefix + ":API_response_id:" + label, status="unknown")
        if is_referee:
            audit.add(prefix + ":original_referee_response", record["response"] == decisions.get("referee"))
            continue
        payload = audit_player_payload(audit, record, tag, observations[tag], intents.get(tag), prefix + ":payload:" + label)
        if label.startswith("diplomacy"):
            number = 1 if label == "diplomacy_" + tag else int(label.split("_")[1])
            audit.add(prefix + ":synchronous:" + label, payload["public_diplomacy"] == [row for row in public if row["round"] < number])
            rows = [row for row in public if row["country"] == tag and row["round"] == number]
            audit.add(prefix + ":original_public_response:" + label, len(rows) == 1 and {key: value for key, value in rows[0].items() if key != "round"} == record["response"])
            if start:
                public_times.append(start)
        elif label.startswith("intent_"):
            audit.add(prefix + ":intent_before_diplomacy_inputs:" + tag, payload["public_diplomacy"] == [])
            audit.add(prefix + ":original_intent:" + tag, record["response"] == intents.get(tag))
            if end:
                intent_times.append(end)
        else:
            audit.add(prefix + ":complete_public_inputs:" + label, payload["public_diplomacy"] == public)
        if label.startswith("reply_"):
            audit.add(prefix + ":reply_only_received_mail:" + tag, payload.get("own_private_diplomacy") == private_context(tag, initial))
        elif label.startswith("decision_"):
            audit.add(prefix + ":decision_only_addressed_mail:" + tag, payload.get("own_private_diplomacy") == private_context(tag, all_private))
    audit.add(prefix + ":unique_model_responses", len(response_ids) == len(set(response_ids)))
    if config["show"].get("intents", True):
        audit.add(prefix + ":intent_API_precedes_public_API", max(intent_times) <= min(public_times) if len(intent_times) == len(tags) and public_times else None)
    if result:
        for index, entry_action in enumerate(result.get("actions", [])):
            stamp = audit.stamp(entry_action.get("started_at"), prefix + f":action_{index}_started_at")
            if stamp:
                execution_times.append(stamp)
        production_path = campaign_dir / "production.jsonl"
        if production_path.exists():
            for line in production_path.read_text(encoding="utf-8").splitlines():
                marker = json.loads(line)
                if marker.get("turn") == entry["index"] and marker.get("phase") == "execute.start":
                    stamp = audit.stamp(marker.get("recorded_at"), prefix + ":execution_phase_UTC")
                    if stamp:
                        execution_times.append(stamp)
        if config["show"].get("intents", True):
            audit.add(prefix + ":intent_API_precedes_execution", max(intent_times) <= min(execution_times) if len(intent_times) == len(tags) and execution_times else None)
        if len(requests) == len(tags):
            final = audit_execution(audit, turn, result, requests, observations, config["step_hours"], prefix)
        else:
            final = None
        output = resolved(root, entry.get("export", {}).get("output", campaign_dir / "episodes" / f"turn_{entry['index']:03d}"))
        audit_episode(audit, output, result, prefix + ":episode")
        audit_auxiliary_models(audit, trial, config, tags, first["turn_id"], public, all_private, prefix)
        return baseline, final


def verify_campaign(name, root=ROOT, write=True):
    name_ok(name)
    root = Path(root)
    directory = root / "runs/campaigns" / name
    audit = Audit(root)
    manifest = audit.read(directory / "manifest.json", "manifest")
    if manifest is not None:
        config = manifest["config"]
        audit.add("frozen_config_sha256", manifest.get("config_sha256") == config_digest(config))
        audit.add("manifest_identity", manifest.get("name") == name and 3 <= len(set(config["countries"])) == len(config["countries"]) <= 5)
        entries = manifest.get("turns", [])
        complete = manifest.get("status") == "completed" and len(entries) == config["turn_limit"] and all(row.get("status") == "completed" for row in entries)
        audit.add("campaign_committed_completion", True if complete else None, detail=None if complete else "Campaign or export stages have not all completed")
        previous = None
        for entry in entries:
            states = audit.section("turn_" + str(entry["index"]) + ":record_structure", lambda entry=entry: audit_turn(audit, root, directory, config, entry))
            if states:
                baseline, final = states
                if previous and baseline:
                    audit.add("turn_" + str(entry["index"]) + ":continuous_world_state", same_date(previous["date"], baseline["date"])
                              and all(country_fingerprint(previous, tag) == country_fingerprint(baseline, tag) for tag in config["countries"]))
                previous = final
        for tag in config["countries"]:
            for folder in ("knowledge", "diplomacy"):
                path = directory / folder / (tag + ".json")
                if path.exists():
                    values = audit.read(path)
                    if values is not None:
                        audit.section(folder + ":" + tag, lambda values=values, folder=folder, tag=tag: audit.add(folder + ":" + tag,
                            all(row.get("country") == tag for row in values) if folder == "knowledge" else len(visible_promises(values, tag)) == len(values)))
    counts = Counter(row["status"] for row in audit.checks)
    status = "failed" if counts["failed"] else "incomplete" if counts["missing"] or counts["unknown"] else "passed"
    report = {"schema_version": 1, "name": name, "status": status, "checked_at": datetime.now(timezone.utc).isoformat(),
              "scope": "Offline structural audit of recorded evidence; not a proof of hidden model cognition, semantic truth or OBS synchronization.",
              "counts": dict(counts), "checks": audit.checks}
    if write:
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "verification.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(directory / "verification.json")
    return report


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    report = verify_campaign(args.name)
    print(json.dumps({"name": args.name, "status": report["status"], "counts": report["counts"],
                      "issues": [row for row in report["checks"] if row["status"] != "passed"]}, ensure_ascii=False, indent=2))
    raise SystemExit({"passed": 0, "failed": 1, "incomplete": 2}[report["status"]])


if __name__ == "__main__":
    main()
