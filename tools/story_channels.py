"""Explicit public, player-private and audience-only story projections.

Public facts come from frozen world changes; player speech remains an attributed
claim, including when its author is lying. Sealed orders are never a public source.
"""
from __future__ import annotations

import copy
import hashlib
import json


def _id(kind, value):
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return kind + "-" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def assign_public_message_ids(messages, match_id, turn_id):
    """Assign IDs before routing; identical archived input yields identical IDs."""
    result = []
    for index, message in enumerate(messages):
        row = copy.deepcopy(message)
        claim = {key: value for key, value in message.items() if key not in ("id", "visibility", "source_kind")}
        row["id"] = _id("speech", {"match_id": match_id, "turn_id": turn_id,
                                 "index": index, "message": claim})
        row["visibility"] = "public"
        row["source_kind"] = "player_claim"
        result.append(row)
    return result


def saved_scenario_board(state, scenario, map_index, tags, match_id, turn_id):
    from army_actions import _controller
    objectives = (scenario or {}).get("opening_control", {})
    if not objectives:
        return []
    controllers = {p: _controller(state, int(p), map_index) for p in objectives}
    pairs = [sorted([w["first"], w["second"]]) for w in state.get("public_wars", [])
             if w.get("first") in tags and w.get("second") in tags]
    row = {"visibility": "public", "kind": "scenario_map", "observed_date": state["date"],
           "facts": {"province_controllers": controllers, "participating_countries_at_war": sorted(pairs)},
           "source": {"kind": "game_save_scenario_map"}}
    row["id"] = _id("event", {"match_id": match_id, "turn": turn_id, "event": row})
    return [row]


def build_public_events(before_world, after_world, tagset, dates):
    """Project only observable control/war changes, never sealed action targets."""
    tags, events = set(tagset), []
    dates = dates if isinstance(dates, dict) else {"before": dates[0], "after": dates[1]}

    def event(kind, facts):
        row = {"visibility": "public", "kind": kind, "observed_date": dates.get("after"),
               "before_date": dates.get("before"), "facts": facts,
               "source": {"kind": "native_world_diff"}}
        for label in ("before", "after"):
            if type(dates.get(label + "_raw")) is int:
                row["observed_date_raw" if label == "after" else "before_date_raw"] = dates[label + "_raw"]
        row["id"] = _id("event", {"event": row, "match_id": dates.get("match_id"),
                                 "turn_id": dates.get("turn_id")})
        events.append(row)

    for province in sorted(set(before_world.get("provinces", {})) & set(after_world.get("provinces", {})), key=str):
        before = before_world["provinces"][province].get("controller")
        after = after_world["provinces"][province].get("controller")
        if before and after and before != after and ({before, after} & tags):
            event("province_control_changed", {"province_id": int(province), "before_controller": before,
                                               "after_controller": after})

    def wars(world):
        return {tuple(sorted((row["first"], row["second"]))): row
                for row in world.get("public_wars", []) if row.get("first") and row.get("second")}

    old, current = wars(before_world), wars(after_world)
    for pair in sorted(set(current) - set(old)):
        if set(pair) & tags:
            # Relation endpoints do not certify which side initiated the war.
            event("war_started", {"countries": list(pair), "start_date": current[pair].get("start_date")})
    return events


def _public_message(row):
    if row.get("visibility", "public") != "public" or row.get("source_kind", "player_claim") != "player_claim":
        raise ValueError("Private message cannot enter public context")
    fields = ("id", "country", "message", "round", "visibility", "source_kind", "public_asides")
    result = {key: copy.deepcopy(row[key]) for key in fields if key in row}
    # Annotation prose is not speech and may have been derived from private data.
    if "commitments" in row:
        result["commitments"] = copy.deepcopy(row["commitments"])
    return result


def public_context(messages, observed):
    """Select public context at the END of prompt construction, using an allowlist."""
    result = copy.deepcopy(messages)
    payload = json.loads(result[1]["content"])
    selected = {key: copy.deepcopy(payload[key]) for key in
                ("task", "match_rules", "game_guide", "own_profile", "participants", "turn_hours") if key in payload}
    if "own_profile" in selected:
        selected["own_profile"] = {key: value for key, value in selected["own_profile"].items()
                                   if key in ("name", "persona", "scenario_goal")}
    if payload.get("public_cast"):
        from showrunner import public_cast
        selected["public_cast"] = public_cast(payload)
    selected["own_observation"] = {"identity": copy.deepcopy(payload["own_observation"]["identity"])}
    for key in ("date_raw", "execution_backend"):
        if key in observed:
            selected["own_observation"][key] = observed[key]
    selected["public_diplomacy"] = [_public_message(row) for row in payload.get("public_diplomacy", [])]
    history = []
    board = []
    for row in observed.get("recent_history", []):
        history.append({"turn_id": row["turn_id"],
                        "public_diplomacy": [_public_message(item) for item in row.get("public_diplomacy", [])]})
        board.extend(row.get("public_events", []))
    board.extend(observed.get("public_board", observed.get("public_events", [])))
    allowed_events, seen = [], set()
    for row in board:
        if row.get("visibility") != "public" or not isinstance(row.get("id"), str) or row["id"] in seen:
            continue
        # Accept only the structured board produced from world observations.
        if row.get("source", {}).get("kind") not in ("native_world_diff", "game_save_scenario_map"):
            continue
        allowed_events.append({key: copy.deepcopy(row[key]) for key in
                               ("id", "visibility", "kind", "observed_date", "before_date", "observed_date_raw", "before_date_raw", "facts", "source") if key in row})
        seen.add(row["id"])
    selected["public_history"] = history
    selected["public_board"] = allowed_events
    # Host speech must point directly to actual world observations/player claims.
    # Interviews and chains of commentary cannot authenticate a public source.
    sources = {row["id"] for row in selected["public_diplomacy"] + allowed_events if isinstance(row.get("id"), str)}
    for row in history:
        sources.update(item["id"] for item in row["public_diplomacy"] if isinstance(item.get("id"), str))
    hosts = []
    for row in observed.get("public_host_messages", []):
        if (not isinstance(row, dict) or row.get("visibility") != "public" or row.get("source_kind") != "host_commentary"
                or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in sources
                or not isinstance(row.get("message"), str) or not 0 < len(row["message"]) <= 1600
                or not isinstance(row.get("refs"), list) or not 0 < len(row["refs"]) <= 4
                or any(not isinstance(ref, str) or ref not in sources for ref in row["refs"])):
            continue
        if row["id"] not in {item["id"] for item in hosts}:
            hosts.append({key: copy.deepcopy(row[key]) for key in ("id", "visibility", "source_kind", "message", "refs")})
    selected["public_host_messages"] = hosts
    promises = [row for row in payload.get("own_commitment_memory", []) if row.get("visibility") == "public"]
    if promises:
        selected["public_commitment_memory"] = copy.deepcopy(promises)
    selected["public_context_notice"] = ("公开发言仅依据公开事件和各国自行发表的声明；声明是可疑主张，不是已证实的世界事实。"
                                          "主持人评论也是公开解读，不能认证玩家主张。密封理由、计划、私信和私信承诺不在本频道。"
                                          "可以自然质问、澄清或承认不知道。")
    result[1]["content"] = json.dumps(selected, ensure_ascii=False)
    return result


def public_source_ids(messages, observed):
    """Return exactly the public IDs the speaker was supplied, including history."""
    projected = json.loads(public_context(messages, observed)[1]["content"])
    rows = projected["public_diplomacy"] + projected["public_board"] + projected["public_host_messages"]
    for history in projected["public_history"]:
        rows.extend(history["public_diplomacy"])
    return {row["id"] for row in rows if isinstance(row.get("id"), str)}


def check_public_asides(value, tags, source_ids):
    asides = value.get("public_asides", [])
    if not isinstance(asides, list) or len(asides) > 2:
        raise ValueError("At most two public asides are allowed")
    for row in asides:
        if not isinstance(row, dict) or set(row) != {"about", "stance", "ref", "text"}:
            raise ValueError("Invalid public aside schema")
        if row["about"] not in tags or not isinstance(row["ref"], str) or row["ref"] not in source_ids:
            raise ValueError("Public aside must reference a supplied public source ID")
        if not isinstance(row["stance"], str) or not 0 < len(row["stance"]) <= 40:
            raise ValueError("Public aside stance must be bounded text")
        if not isinstance(row["text"], str) or not 0 < len(row["text"]) <= 200:
            raise ValueError("Public aside text must be bounded text")
    return asides


def private_feedback(review, country, *, own_only=False):
    """Global referee prose may contain other seals; project only its verdict."""
    if review.get("country") != country:
        raise ValueError("Feedback belongs to another player")
    fields = ("country", "request_sha256", "approved")
    result = {key: copy.deepcopy(review[key]) for key in fields if key in review}
    if own_only:
        result.update({key: copy.deepcopy(review[key]) for key in ("reason", "concerns") if key in review})
    return result


def interview_messages(observed, locked_decision, question, *, public=None, private=None, intent=None, show=None):
    """Audience-only interview: own-visible evidence, no tools or state writeback."""
    from model_trial import brief
    from showrunner import check_history
    tag = observed["country"]
    check_history(observed.get("recent_history", []), tag)
    if locked_decision.get("country") != tag:
        raise ValueError("Interview decision must belong to its interviewee")
    if intent is not None and intent.get("country") != tag:
        raise ValueError("Interview intent must belong to its interviewee")
    if private is not None and (any(row.get("country") != tag for row in private.get("sent", []))
                                or any(row.get("to") != tag for row in private.get("received", []))):
        raise ValueError("Interview contains an unaddressed letter")
    if not isinstance(question, str) or not 0 < len(question) <= 600:
        raise ValueError("Interview question must be bounded text")
    materials = {"own_observation": brief(observed), "public_diplomacy": [_public_message(row) for row in public or []],
                 "own_private_diplomacy": copy.deepcopy(private), "own_intent": copy.deepcopy(intent),
                 "locked_decision": copy.deepcopy(locked_decision)}
    sources = [{"id": _id("interview-source", {"country": tag, "field": key, "value": value}),
                "field": key, "visibility": "public" if key == "public_diplomacy" else "player_private"}
               for key, value in materials.items() if value is not None]
    payload = {"country": tag, "visibility": "audience_only", "question": question,
               "materials": materials, "sources": sources,
               "task": "决定已密封。回答一次赛后采访，可质疑问题或拒答，只输出{country,answer}。不提交动作、不修改决定。"}
    if show and show.get("public_cast"):
        from showrunner import public_cast
        payload["public_cast"] = public_cast(show)
        payload["own_profile"] = {key: show["profiles"].get(tag, {})[key] for key in ("name", "persona")
                                  if key in show["profiles"].get(tag, {})}
        payload["match_rules"] = show.get("common_rules", "")
    return [{"role": "system", "content": ("这是仅供观众的采访频道，回答绝不写回玩家决策历史。导演可以追问或挑动情绪，"
              "不能提供策略、指定战争或泄露第三方私密事实；采访材料只限受访国本来能看到的内容。无游戏工具。")},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def check_interview_answer(value, country):
    if (not isinstance(value, dict) or set(value) != {"country", "answer"} or value["country"] != country
            or not isinstance(value["answer"], str) or not 0 < len(value["answer"]) <= 1000):
        raise ValueError("Interview answer must be bounded audience-only speech")
    return value


def public_commentary_messages(observed, public, question="概括刚才公开发生的变化与公开说法之间的张力。", *, show=None):
    """A public host sees the same allowlisted sources as public diplomacy."""
    from model_trial import country_messages
    if not isinstance(question, str) or not 0 < len(question) <= 600:
        raise ValueError("Public host question must be bounded text")
    messages = public_context(country_messages(observed, "diplomacy", public), observed)
    messages[0]["content"] = ("你是IRON的公开主持人，只能使用给出的公开来源。玩家发言是可疑主张，可以指出矛盾、"
                               "提出问题，但不能把主张认证为事实，不能透露或猜作已知的密封理由与私信。"
                               "不提供战略建议、不指挥战争、不替玩家行动。只输出JSON。")
    payload = json.loads(messages[1]["content"])
    if show and show.get("public_cast"):
        from showrunner import public_cast
        payload["public_cast"] = public_cast(show)
        payload["own_profile"] = {key: show.get("host_profile", {})[key] for key in ("name", "persona")
                                  if key in show.get("host_profile", {})}
    if show and show.get("table_host"):
        from host_skills import host_guidance
        messages[0]["content"] += host_guidance(show)
        if show.get("public_opening"):
            payload["match_rules"] = show["public_opening"]
        question = ("你是智谱娘大狐狸，五国刚说完第一轮，正等你插话。挑一两句桌上原话，"
                    "抓一个口径差异、吹嘘或暧昧的保证。可以故意拱火：点名问另一个当事人敢不敢认，"
                    "也可以拿一句旧话逗她。每次围绕一个话头，留个能让人回嘴的空隙。"
                    "你的推断要让人听出是你在逗或追问；逐字引语只用对方真说过的话。")
        cue = show.get("public_host_cues", {}).get(str(observed["turn_id"]))
        if cue:
            question += "\n本轮已定的公开环节：" + cue + "。环节说明不能作为事实引用，若现场无依据可跳过。"
    if payload.get("public_host_messages"):
        # Host comments cite direct player/world evidence, never host chains.
        payload.pop("public_host_messages")
    payload["task"] = (question + " 输出{commentary:[{ref:给出的公开来源真实id,text:简短评论}]}，最多四条；"
                       "每条必须绑定真实来源，没有合适材料可输出空数组，不强求笑点。")
    payload["visibility"] = "public"
    messages[1]["content"] = json.dumps(payload, ensure_ascii=False)
    return messages


def check_public_commentary(value, source_ids):
    if not isinstance(value, dict) or set(value) != {"commentary"} or not isinstance(value["commentary"], list) or len(value["commentary"]) > 4:
        raise ValueError("Public commentary must contain at most four sourced lines")
    for row in value["commentary"]:
        if (not isinstance(row, dict) or set(row) != {"ref", "text"} or not isinstance(row["ref"], str)
                or row["ref"] not in source_ids or not isinstance(row["text"], str) or not 0 < len(row["text"]) <= 400):
            raise ValueError("Public commentary must cite a supplied public source ID")
    return value


def project_public_commentary(value, source_ids):
    """Drop host lines that cite no supplied public source; the raw response stays in the call record."""
    candidate = copy.deepcopy(value)
    dropped = []
    rows = candidate.get("commentary") if isinstance(candidate, dict) else None
    if isinstance(rows, list):
        kept = []
        for index, row in enumerate(rows):
            if (isinstance(row, dict) and set(row) == {"ref", "text"} and isinstance(row["ref"], str)
                    and row["ref"] in source_ids and isinstance(row["text"], str) and 0 < len(row["text"]) <= 400):
                kept.append(row)
            else:
                dropped.append({"index": index, "row": copy.deepcopy(row)})
        candidate["commentary"] = kept
    return candidate, dropped


def project_interview_answer(value, country):
    """Keep only the contracted interview fields; unrecoverable content still fails validation."""
    if (not isinstance(value, dict) or not isinstance(value.get("country"), str) or value["country"] != country
            or not isinstance(value.get("answer"), str) or not 0 < len(value["answer"]) <= 1000):
        return value, []
    removed = sorted(set(value) - {"country", "answer"})
    if not removed:
        return value, []
    return {key: value[key] for key in ("country", "answer")}, removed


def settle_commitments(entries, public_events, date_raw, turn_id):
    """Settle only explicit predicates against public facts at their deadline.

    A control change before/after the deadline does not prove deadline control.
    Absence of a war event does not prove peace. Prose (including conditions) is
    never interpreted, and a player's public claim is never observation evidence.
    """
    if type(date_raw) is not int or type(turn_id) is not int:
        raise ValueError("Commitment settlement needs integer game time and turn")
    events = [row for row in public_events if row.get("visibility") == "public"
              and row.get("source", {}).get("kind") == "native_world_diff" and isinstance(row.get("id"), str)]

    def evaluate(predicate, at_date):
        if not isinstance(predicate, dict):
            return None, []
        kind = predicate.get("kind")
        if kind == "province_control":
            if (set(predicate) != {"kind", "province_id", "controller"} or type(predicate["province_id"]) is not int
                    or not isinstance(predicate["controller"], str)):
                return None, []
        elif kind == "war_relation":
            countries = predicate.get("countries")
            if (set(predicate) != {"kind", "countries", "at_war"} or not isinstance(countries, list)
                    or len(countries) != 2 or any(not isinstance(tag, str) for tag in countries)
                    or len(set(countries)) != 2 or type(predicate["at_war"]) is not bool):
                return None, []
        else:
            return None, []
        matches = []
        for event in events:
            observed = event.get("observed_date_raw", event.get("observed_date"))
            if type(observed) is not int or observed != at_date:
                continue
            facts = event.get("facts", {})
            if kind == "province_control" and event.get("kind") == "province_control_changed":
                if facts.get("province_id") == predicate["province_id"] and isinstance(facts.get("after_controller"), str):
                    matches.append((facts["after_controller"] == predicate["controller"], event["id"]))
            if kind == "war_relation" and event.get("kind") in ("war_started", "war_ended"):
                pair = facts.get("countries")
                if isinstance(pair, list) and len(pair) == 2 and set(pair) == set(predicate["countries"]):
                    matches.append(((event["kind"] == "war_started") == predicate["at_war"], event["id"]))
        if not matches or len({value for value, _ in matches}) != 1:
            return None, []
        return matches[0][0], sorted({source for _, source in matches})

    settled = []
    for entry in entries:
        row = copy.deepcopy(entry)
        deadline = row.get("due_date_raw")
        turn_deadline = row.get("due_turn")
        at_date = deadline if type(deadline) is int else (
            date_raw if deadline is None and type(turn_deadline) is int and turn_deadline == turn_id else None)
        not_due = (type(deadline) is int and date_raw < deadline) or (
            deadline is None and type(turn_deadline) is int and turn_id < turn_deadline)
        status, evidence = "not_due" if not_due else "unverifiable", []
        if not not_due and at_date is not None:
            condition = row.get("condition_predicate")
            condition_value, condition_sources = evaluate(condition, at_date) if condition is not None else (None if row.get("condition") else True, [])
            if condition_value is False:
                status, evidence = "condition_unmet", condition_sources
            elif condition_value is True:
                value, evidence = evaluate(row.get("predicate"), at_date)
                if value is not None:
                    status = "fulfilled" if value else "observably_violated"
                    evidence = sorted(set(evidence + condition_sources))
        row["status"] = status
        row["settlement"] = {"method": "explicit_public_predicate", "date_raw": date_raw,
                             "turn_id": turn_id, "evidence_ids": evidence}
        settled.append(row)
    return settled


def compress_own_memory(history, country, *, max_turns=12, max_letters=24, compact=False):
    """Bound older own records deterministically, retaining evidence and scope.

    This is a private evidence digest, not a second narrator. No inferred belief,
    global judge explanation or third-party private record enters the digest.
    """
    from showrunner import check_history
    check_history(history, country)
    if type(max_turns) is not int or not 1 <= max_turns <= 48 or type(max_letters) is not int or not 0 <= max_letters <= 96:
        raise ValueError("Own memory bounds are invalid")
    records, remaining = [], max_letters
    for original in reversed(history[-max_turns:]):
        row = {"turn_id": original["turn_id"], "visibility": "player_private", "country": country,
               "source": {"kind": "own_history", "turn_id": original["turn_id"]}}
        for key in ("private_decision", "private_intent", "execution", "commitments", "public_events", "public_diplomacy"):
            if compact and key in ("commitments", "public_diplomacy"):
                continue  # Recent speech and the active commitment ledger are supplied separately.
            if key in original:
                row[key] = copy.deepcopy(original[key])
        if original.get("referee_feedback"):
            row["referee_feedback"] = [private_feedback(item, country) for item in original["referee_feedback"]]
        private = original.get("private_diplomacy", {})
        letters = []
        for direction in ("sent", "received"):
            for index, letter in enumerate(private.get(direction, [])):
                letters.append({"direction": direction, "message": copy.deepcopy(letter),
                                "source_id": letter.get("id") or _id("letter", {"country": country,
                                    "turn_id": original["turn_id"], "direction": direction, "index": index, "letter": letter})})
        if letters and remaining:
            row["private_letters"] = letters[-remaining:]
            remaining -= len(row["private_letters"])
        row["id"] = original.get("id") or _id("own-memory", row)
        records.append(row)
    return {"schema": "iron.own-memory.v1", "country": country, "visibility": "player_private",
            "records": list(reversed(records)), "source_turn_count": len(history),
            "retained_turn_count": len(records), "retained_letter_count": max_letters - remaining}
