"""Post-seal host and interview sidecars; never feed interviews into decisions."""
import hashlib
import json

from m0_probe import write_json
from model_trial import country_model, recorded_complete
from story_channels import (check_interview_answer, check_public_commentary, interview_messages,
                            project_interview_answer, project_public_commentary,
                            public_commentary_messages, public_source_ids)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def post_seal(trial, observed, model, show, config, *, phase="all"):
    if phase not in ("all", "live", "audience"):
        raise ValueError("unknown performance phase")
    if not config.get("enabled", False):
        return {"host": [], "interviews": []}
    tags = list(observed)
    public = read(trial / "public_diplomacy.json")
    sealed = {tag: read(trial / "private_decisions" / (tag + ".json")) for tag in tags}
    hashes = {tag: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() for tag, value in sealed.items()}
    result = {"schema": "iron.performance.v1", "host": [], "interviews": [], "writeback": False}
    previous = read(trial / "performance.json") if (trial / "performance.json").exists() else {}
    if phase == "audience":
        result["host"] = previous.get("host", [])
    if phase == "live":
        result["interviews"] = previous.get("interviews", [])
    if config.get("public_host", True) and phase != "audience":
        messages = public_commentary_messages(observed[tags[0]], public, show=show)
        record = recorded_complete(trial, "public_host", {**model, "max_tokens": 700}, messages,
                                   "public_host", require_settings=True)
        payload = json.loads(messages[1]["content"])
        direct = payload.get("public_diplomacy", []) + payload.get("public_board", [])
        for history in payload.get("public_history", []):
            direct += history.get("public_diplomacy", [])
        sources = {row["id"] for row in direct if isinstance(row.get("id"), str)}
        value, dropped = project_public_commentary(record["response"], sources)
        if dropped:
            record["host_projection"] = {"method": "sourced_public_commentary.v1", "dropped": dropped}
            write_json(trial / "model_calls" / "public_host.json", record)
        check_public_commentary(value, sources)
        for index, row in enumerate(value["commentary"]):
            identity = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            result["host"].append({"id": "host-" + identity, "visibility": "public", "source_kind": "host_commentary",
                                   "message": row["text"], "refs": [row["ref"]], "index": index})
    count = config.get("interviews_per_turn", 1)
    if type(count) is not int or not 0 <= count <= len(tags):
        raise ValueError("interviews_per_turn exceeds participants")
    offset = (observed[tags[0]]["turn_id"] - 1) % len(tags)
    for index in range(0 if phase == "live" else count):
        tag = tags[(offset + index) % len(tags)]
        private_path = trial / "private_diplomacy" / (tag + ".json")
        intent_path = trial / "private_intents" / (tag + ".json")
        question = "决定已密封，现在只聊感受：哪句已经公开的发言最让你想回应？可以质问、吐槽、表示好感，也可以拒答。"
        messages = interview_messages(observed[tag], sealed[tag], question, public=public,
                                      private=read(private_path) if private_path.exists() else None,
                                      intent=read(intent_path) if intent_path.exists() else None, show=show)
        record = recorded_complete(trial, "audience_interview_" + tag,
                                   {**country_model(model, show, tag), "max_tokens": 600}, messages,
                                   "audience_interview", tag, require_settings=True)
        value, removed = project_interview_answer(record["response"], tag)
        if removed:
            record["interview_projection"] = {"method": "audience_interview_fields.v1", "removed": removed}
            write_json(trial / "model_calls" / ("audience_interview_" + tag + ".json"), record)
        check_interview_answer(value, tag)
        result["interviews"].append({**value, "question": question, "visibility": "audience_only"})
    for tag in tags:
        current = read(trial / "private_decisions" / (tag + ".json"))
        if hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest() != hashes[tag]:
            raise ValueError("Performance phase changed a sealed decision")
    write_json(trial / "performance.json", result)
    return result
