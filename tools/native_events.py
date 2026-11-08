"""Read-only Windows event-window provider with explicit coverage limits.

Registered windows are reconciled with the native shared pending-record list.
Coverage of all country event queues remains unavailable. Native choice support
is restricted to the qualified informational news.66 event.
"""
from __future__ import annotations

import struct

from native_capture import BUILD, GAME_SHA256, ReadProcess, _array, _frozen, _number
from native_rpc import process_creation_time

SCHEMA = "iron.native-events.v1"
BLOCK = "general event choice API is unavailable; native choice support is restricted to news.66"


def unavailable(reason=BLOCK):
    # pending=null deliberately cannot be mistaken for a verified empty queue.
    return {"schema": SCHEMA, "availability": "unavailable", "capture_complete": False,
            "scope": "all_pending_country_and_news_events", "pending": None,
            "choice_api": {"availability": "unavailable", "reason": BLOCK}, "reason": reason}


def _string(reader, address):
    block = reader.read(address, 32)
    size, capacity = struct.unpack_from("<QQ", block, 16)
    if not 0 <= size <= capacity <= 65536:
        raise RuntimeError("event string is outside the qualified bound")
    value = reader.read(struct.unpack_from("<Q", block)[0], size) if capacity > 15 and size else block[:size]
    return value.decode("utf-8", errors="strict")


def _records(reader, state, base):
    data = _number(reader, state + 0x560)
    capacity = _number(reader, state + 0x568, "i")
    count = _number(reader, state + 0x56C, "i")
    if not 0 <= count <= capacity <= 65536 or count > 4096 or (count and not data):
        raise RuntimeError("native pending-record collection outside qualified bound")
    result = {}
    for offset in range(count):
        address = data + offset * 0x38
        identifier = _number(reader, address, "i")
        event = _number(reader, address + 8)
        origin = _number(reader, address + 0x18, "i")
        if identifier <= 0 or identifier in result or origin <= 0 or _number(reader, event) != base + 0x2A16008:
            raise RuntimeError("native pending-record identity mismatch")
        result[identifier] = (event, origin)
    return result


def read_pending(game, main_status, controls, *, reader_factory=ReadProcess):
    """Enumerate registered CEventWindow objects without UI or process writes.

    Event fields below are raw native keys. Localized title/description and
    eligibility are explicitly unavailable, so these rows are not executable
    decisions. A positive row still interrupts pacing for review.
    """
    if (main_status.get("synthetic") is not False or main_status.get("build") != BUILD
            or main_status.get("sha256") != GAME_SHA256):
        return unavailable("event-window reader requires the pinned native executable")
    date_raw = next(iter(controls.values()))["date_raw"]
    for control in controls.values():
        _frozen(control, date_raw=date_raw)
    if process_creation_time(game.pid) != game.creation_time:
        raise RuntimeError("game process restarted before event capture")
    base = int(main_status["image_base"], 16)
    labels = {value["country_index"]: tag for tag, value in controls.items()}
    with reader_factory(game.pid) as reader:
        state = _number(reader, base + 0x33DD6B0)
        idler = _number(reader, base + 0x33DDB10)
        manager = _number(reader, idler + 0x4F8)

        def witness():
            if (_number(reader, base + 0x33DD6B0) != state
                    or _number(reader, state + 0x468, "i") != date_raw
                    or reader.read(idler + 0x6C1, 1) != b"\x01"
                    or _number(reader, idler + 0x4F8) != manager):
                raise RuntimeError("native world changed during event capture")

        witness()
        if _number(reader, manager) != base + 0x29C7580:
            return unavailable("registered-popup manager identity mismatch")
        records = _records(reader, state, base)
        rows, resolved, other, represented = [], [], [], set()
        for popup in _array(reader, manager + 0xD48, maximum=4096):
            vtable = _number(reader, popup)
            if vtable != base + 0x2A20858:
                other.append({"vtable_rva": hex(vtable - base)})
                continue
            window = popup - 0x28
            if _number(reader, window) != base + 0x2A20830:
                raise RuntimeError("event window primary identity mismatch")
            event = _number(reader, window + 0x68)
            if _number(reader, event) != base + 0x2A16008:
                raise RuntimeError("event object identity mismatch")
            # +0x60 is the pending record ID, not a country. Major news may
            # originate in BEL while its copied display scope targets AUS.
            pending_id = _number(reader, window + 0x60, "i")
            origin_index = _number(reader, window + 0x70, "i")
            if _number(reader, window + 0x78) != base + 0x2852168:
                raise RuntimeError("event scope identity mismatch")
            index = _number(reader, window + 0x80, "i")
            options = []
            for option in _array(reader, event + 0x368, maximum=256):
                if _number(reader, option) != base + 0x2A15F68:
                    raise RuntimeError("event option identity mismatch")
                options.append({"index": _number(reader, option + 0x1F0, "i"),
                                "native_key": _string(reader, option + 0x178),
                                "eligibility": "unavailable", "actionable": False})
            record = records.get(pending_id)
            if record is not None and record != (event, origin_index):
                raise RuntimeError("event window and native pending record disagree")
            unresolved = record is not None
            represented.add(pending_id)
            row = {"event_handle": hex(_number(reader, event + 8)),
                         "window_handle": hex(window), "country_index": index,
                         "pending_instance_id": pending_id,
                         "origin_country_index": origin_index,
                         "origin_country": labels.get(origin_index, f"IDX_{origin_index}"),
                         "country": labels.get(index, f"IDX_{index}"),
                         "native_key": _string(reader, event + 0x20),
                         "native_auxiliary_key": _string(reader, event + 0x40),
                         "title": None, "description": None,
                         "localized_text_available": False, "options": options,
                         "decision_required": unresolved,
                         "pending_record_present": unresolved,
                         "window_active": reader.read(window + 0x338, 1) == b"\x01",
                         "status": "pending" if unresolved else "resolved_window"}
            (rows if unresolved else resolved).append(row)
        # A hidden or missing window cannot conceal an unresolved native record.
        for identifier, (event, origin) in records.items():
            if identifier in represented:
                continue
            rows.append({"event_handle": hex(_number(reader, event + 8)),
                         "window_handle": None, "country_index": None, "country": None,
                         "pending_instance_id": identifier, "origin_country_index": origin,
                         "origin_country": labels.get(origin, f"IDX_{origin}"),
                         "native_key": _string(reader, event + 0x20), "options": None,
                         "decision_required": True, "pending_record_present": True,
                         "status": "pending_record_without_registered_window"})
        witness()
        if _records(reader, state, base) != records:
            raise RuntimeError("native pending records changed during event capture")
    if process_creation_time(game.pid) != game.creation_time:
        raise RuntimeError("game process restarted during event capture")
    return {"schema": SCHEMA, "availability": "partial", "capture_complete": False,
            "scope": "registered_event_windows", "all_pending_availability": "unavailable",
            "date_raw": date_raw, "pending": rows, "resolved_windows": resolved,
            "native_pending_record_count": len(records), "other_registered_popups": other,
            "choice_api": {"availability": "unavailable", "reason": BLOCK},
            "source": {"kind": "native_read_only_memory", "build": BUILD, "sha256": GAME_SHA256},
            "limitations": ["all-country script event queues are not qualified",
                            "localized event text and eligible option enumeration are not qualified"]}


def select_choice(game=None, event=None, option_index=None, *, date_raw=None,
                  played_index=None, post=False, request_id=None):
    """Probe, or explicitly post, the qualified informational news.66 command.

    A probe constructs and destroys a native command and checks native option
    eligibility and CanExecute. A posted receipt is not proof of dismissal;
    callers must recapture pending records/windows before resuming time.
    """
    if not isinstance(event, dict) or event.get("native_key") != "news.66":
        raise NotImplementedError(BLOCK)
    if (option_index not in (0, 1) or isinstance(option_index, bool)
            or not isinstance(date_raw, int) or not isinstance(played_index, int)
            or event.get("country_index") != played_index):
        raise ValueError("exact date, display scope country and news option are required")
    if post and not request_id:
        raise ValueError("posting a news choice requires an explicit durable request ID")
    import uuid
    payload = (f"{'select' if post else 'probe'} {date_raw} {played_index} "
               f"{int(event['window_handle'], 16):x} {int(event['event_handle'], 16):x} {option_index}")
    return game.call("native_events_v1", payload, request_id=request_id or "event-probe-" + uuid.uuid4().hex)


def close_resolved_window(game, event, *, date_raw, played_index, post=False, request_id=None):
    """Close a resolved news.66 window through the normal native lifecycle only."""
    if event.get("native_key") != "news.66" or event.get("country_index") != played_index:
        raise NotImplementedError(BLOCK)
    if post and not request_id:
        raise ValueError("closing a resolved window requires an explicit durable request ID")
    import uuid
    payload = (f"{'close_resolved' if post else 'probe_close'} {date_raw} {played_index} "
               f"{int(event['window_handle'], 16):x} {int(event['event_handle'], 16):x} {event['pending_instance_id']}")
    return game.call("native_events_v2", payload, request_id=request_id or "event-close-probe-" + uuid.uuid4().hex)
