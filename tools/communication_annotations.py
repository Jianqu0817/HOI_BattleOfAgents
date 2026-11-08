"""Project only literal promise annotations; preserve every spoken message."""
import copy


def project_reply_routes(value, country, received):
    """Deliver at most one unchanged reply to each actual sender; retain rejections."""
    candidate = copy.deepcopy(value)
    if candidate.get("country") != country or not isinstance(candidate.get("replies"), list):
        return candidate, []
    allowed = {row["country"] for row in received if row.get("to") == country}
    replies, rejected, seen = [], [], set()
    for index, reply in enumerate(candidate["replies"]):
        if not isinstance(reply, dict) or not isinstance(reply.get("to"), str):
            replies.append(reply)  # Other malformed envelopes still fail validation.
            continue
        target = reply["to"]
        reason = "not_a_current_sender" if target not in allowed else "duplicate_reply" if target in seen else None
        if reason:
            rejected.append({"index": index, "country": country, "to": target, "status": "undelivered",
                             "reason": reason, "reply": copy.deepcopy(reply)})
        else:
            replies.append(reply)
            seen.add(target)
    candidate["replies"] = replies
    return candidate, rejected


def project_commitment_quotes(value, *, turn_id=None):
    candidate = copy.deepcopy(value)
    changes = []
    groups = [("message", candidate)] + [(f"replies/{index}", row) for index, row in enumerate(candidate.get("replies", []))]
    for location, group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("message"), str) or not isinstance(group.get("commitments", []), list):
            continue
        kept = []
        for index, promise in enumerate(group.get("commitments", [])):
            if not isinstance(promise, dict) or not isinstance(promise.get("text"), str):
                kept.append(promise)  # Other malformed fields still fail validation.
                continue
            text = promise["text"]
            change = {"location": location, "index": index, "original": copy.deepcopy(promise)}
            deadline = promise.get("due_turn")
            if turn_id is not None and (type(deadline) is not int or not turn_id <= deadline <= turn_id + 12):
                changes.append({**change, "kind": "omit_annotation_deadline_out_of_window"})
                continue
            recipient = group.get("to")
            if recipient is not None and promise.get("to") != recipient:
                changes.append({**change, "kind": "omit_annotation_misaddressed_recipient"})
                continue
            if text and text not in group["message"]:
                if text.endswith(("。", ".")) and len(text) > 1 and text[:-1] in group["message"]:
                    promise["text"] = text[:-1]
                    changes.append({**change, "kind": "trim_added_terminal_period", "text": promise["text"]})
                else:
                    changes.append({**change, "kind": "omit_nonliteral_annotation"})
                    continue
            if len(kept) >= 3:
                changes.append({**change, "kind": "omit_annotation_over_limit"})
            else:
                kept.append(promise)
        if "commitments" in group:
            group["commitments"] = kept
    return candidate, changes
