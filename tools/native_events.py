"""Read-only Windows event-window provider with explicit coverage limits.

Only the registered popup collection is qualified for the pinned executable.
It is not a complete queue of all countries' pending script events. Selection
remains unavailable until instance ownership, option eligibility, and command
construction/CanExecute have been qualified together.
"""
from __future__ import annotations

import struct

from native_capture import BUILD, GAME_SHA256, ReadProcess, _array, _frozen, _number
from native_rpc import process_creation_time

SCHEMA = "iron.native-events.v1"
BLOCK = ("Windows event instance ownership/scope and option eligibility are not qualified; "
         "normal SelectEventOption construction and CanExecute have no owned-host proof")


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
        rows, other = [], []
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
            index = _number(reader, window + 0x60, "i")
            options = []
            for option in _array(reader, event + 0x368, maximum=256):
                if _number(reader, option) != base + 0x2A15F68:
                    raise RuntimeError("event option identity mismatch")
                options.append({"index": _number(reader, option + 0x1F0, "i"),
                                "native_key": _string(reader, option + 0x178),
                                "eligibility": "unavailable", "actionable": False})
            rows.append({"event_handle": hex(_number(reader, event + 8)),
                         "window_handle": hex(window), "country_index": index,
                         "country": labels.get(index, f"IDX_{index}"),
                         "native_key": _string(reader, event + 0x20),
                         "native_auxiliary_key": _string(reader, event + 0x40),
                         "title": None, "description": None,
                         "localized_text_available": False, "options": options,
                         "decision_required": True})
        witness()
    if process_creation_time(game.pid) != game.creation_time:
        raise RuntimeError("game process restarted during event capture")
    return {"schema": SCHEMA, "availability": "partial", "capture_complete": False,
            "scope": "registered_event_windows", "all_pending_availability": "unavailable",
            "date_raw": date_raw, "pending": rows, "other_registered_popups": other,
            "choice_api": {"availability": "unavailable", "reason": BLOCK},
            "source": {"kind": "native_read_only_memory", "build": BUILD, "sha256": GAME_SHA256},
            "limitations": ["all-country script event queues are not qualified",
                            "localized event text and eligible option enumeration are not qualified"]}


def select_choice(*_args, **_kwargs):
    raise NotImplementedError(BLOCK)
