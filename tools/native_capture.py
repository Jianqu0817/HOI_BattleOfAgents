"""Frozen native observations and bounded read-only ownership capture for HOI4."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
from types import SimpleNamespace
import uuid

from native_rpc import process_creation_time

BUILD = "6ae9f874_35ea878"
GAME_SHA256 = "bccc62909b6a1ec2c1d5a46ef18608e4d0c7cdc89ad341b7402a23c40f7a8037"
ROOT = Path(__file__).resolve().parents[1]


class ReadProcess:
    """Attach with VM_READ and QUERY_INFORMATION only; never request write access."""
    def __init__(self, pid):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.api.OpenProcess.restype = wt.HANDLE
        self.api.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                              ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.api.ReadProcessMemory.restype = wt.BOOL
        self.api.CloseHandle.argtypes = [wt.HANDLE]
        self.api.CloseHandle.restype = wt.BOOL
        self.handle = self.api.OpenProcess(0x10 | 0x400, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def read(self, address, size):
        if not 0 < address < 0x7fffffffffff or not 0 < size <= 2_000_000:
            raise ValueError("invalid bounded memory read")
        buffer, got = ctypes.create_string_buffer(size), ctypes.c_size_t()
        if not self.api.ReadProcessMemory(self.handle, address, buffer, size, ctypes.byref(got)) or got.value != size:
            raise OSError(f"incomplete read at {address:#x}: {got.value}/{size}")
        return buffer.raw

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _number(reader, address, fmt="Q"):
    return struct.unpack("<" + fmt, reader.read(address, struct.calcsize(fmt)))[0]


def _array(reader, address, *, maximum):
    # CPdxArray: pointer +0, capacity +8, count +0xC. The CState
    # province array starts at +0x18, hence its count is exactly +0x24.
    data, capacity, count = struct.unpack("<Qii", reader.read(address, 16))
    if not 0 <= count <= capacity <= maximum or (count and not data):
        raise RuntimeError(f"invalid native array at {address:#x}")
    return struct.unpack("<" + "Q" * count, reader.read(data, count * 8)) if count else ()


def _frozen(control, *, date_raw=None):
    if control.get("paused") is not True or type(control.get("date_raw")) is not int:
        raise RuntimeError("capture requires the paused native clock")
    if date_raw is not None and control["date_raw"] != date_raw:
        raise RuntimeError("native date changed during capture")


def read_world(game, main_status, controls, *, reader_factory=ReadProcess):
    """Read province controllers without parsing a save or changing player control.

    Layout is pinned to the bridge's verified executable. Unknown country indices
    retain IDX_n labels; only native diplomacy supplies the selected-country wars.
    """
    if (main_status.get("synthetic") is not False or main_status.get("build") != BUILD
            or main_status.get("sha256") != GAME_SHA256):
        raise RuntimeError("ownership reader requires the pinned native game build")
    base = int(main_status["image_base"], 16)
    if not controls:
        raise ValueError("country control witnesses are required")
    date_raw = next(iter(controls.values()))["date_raw"]
    by_index = {}
    for tag, control in controls.items():
        _frozen(control, date_raw=date_raw)
        index = control["country_index"]
        if index in by_index:
            raise ValueError("country indices must be distinct")
        by_index[index] = tag
    if process_creation_time(game.pid) != game.creation_time:
        raise RuntimeError("game process restarted before ownership capture")
    label = lambda index: by_index.get(index, f"IDX_{index}")
    states, provinces = {}, {}
    with reader_factory(game.pid) as reader:
        state = _number(reader, base + 0x33DD6B0)
        idler = _number(reader, base + 0x33DDB10)

        def witness():
            if (_number(reader, state + 0x468, "i") != date_raw
                    or reader.read(idler + 0x6C1, 1) != b"\x01"
                    or _number(reader, base + 0x33DD6B0) != state):
                raise RuntimeError("native world changed or unpaused during capture")

        witness()
        countries = _array(reader, state + 0x310, maximum=4096)
        seen_states = set()
        for index, country in enumerate(countries):
            if not country:
                continue
            header = reader.read(country, 16)
            if (struct.unpack_from("<Q", header)[0] != base + 0x2864730
                    or struct.unpack_from("<i", header, 8)[0] != index):
                raise RuntimeError("country table identity mismatch")
            for address in _array(reader, country + 0x460, maximum=4096):
                if address in seen_states:
                    continue
                seen_states.add(address)
                block = reader.read(address, 0xD0)
                if struct.unpack_from("<Q", block)[0] != base + 0x29B4D58:
                    raise RuntimeError("state object identity mismatch")
                sid = struct.unpack_from("<i", block, 0x58)[0]
                owner, controller = struct.unpack_from("<ii", block, 0xC8)
                # This country list also includes occupied states. Read the
                # actual owner/controller fields rather than infer from index.
                if sid <= 0 or str(sid) in states or not 0 <= owner < len(countries) or not 0 <= controller < len(countries):
                    raise RuntimeError("state identity/index mismatch")
                states[str(sid)] = {"owner": label(owner), "controller": label(controller)}
                for province in _array(reader, address + 0x18, maximum=20000):
                    block = reader.read(province, 0x190)
                    if (struct.unpack_from("<Q", block)[0] != base + 0x29EE358
                            or struct.unpack_from("<Q", block, 0xC0)[0] != address):
                        raise RuntimeError("province object identity mismatch")
                    pid = struct.unpack_from("<i", block, 0xA4)[0]
                    controller = struct.unpack_from("<i", block, 0x188)[0]
                    if pid <= 0 or str(pid) in provinces:
                        raise RuntimeError("duplicate or invalid province ID")
                    provinces[str(pid)] = {"controller": label(controller)}
        witness()
    if process_creation_time(game.pid) != game.creation_time:
        raise RuntimeError("game process restarted during ownership capture")
    return {"date_raw": date_raw, "states": states, "provinces": provinces,
            "countries": {tag: {} for tag in controls}, "public_wars": [],
            "source": {"kind": "native_read_only_memory", "pid": game.pid,
                       "creation_time": game.creation_time, "build": BUILD,
                       "sha256": GAME_SHA256, "unknown_country_labels": "IDX_<index>"}}


@lru_cache(maxsize=1)
def static_context():
    """Reuse static map/adjacency/passability caches without a GUI session."""
    dependency = str(ROOT / ".deps" / "hoi4-live-tools")
    if dependency not in sys.path:
        sys.path.insert(0, dependency)
    from hoi4_live.config import load
    from hoi4_live.gamedata import mapdata, scriptdb
    from army_actions import land_graph, static_passability
    session = SimpleNamespace(cfg=load())
    mods = scriptdb.mods_for(session)
    replaced = [path for path in scriptdb.effective_files("map", session.cfg.game_dir, mods, ext=(".bmp", ".csv"))
                if Path(path).name.lower() in ("provinces.bmp", "definition.csv")
                and Path(path).resolve().parent != (Path(session.cfg.game_dir) / "map").resolve()]
    if replaced:
        raise RuntimeError("loaded map replacement needs separate native map acceptance")
    return {"map": mapdata.for_session(session), "graph": land_graph(session),
            "passability": static_passability(session)}


def _save_date(control):
    # Native control emits ISO time with zero-based hour. Existing save-backed
    # agent observations use the save's one-based hour (00:00 -> .1).
    date, clock = control["date"].split("T")
    year, month, day = map(int, date.split("-"))
    hour = int(clock.split(":")[0]) + 1
    return f"{year}.{month}.{day}.{hour}"


def capture(game, tags, outputdir, matchid, turnid, history=None, *, map_context=None):
    """Capture selected countries at one date, restoring original player in finally.

    NativeGame owns durable call intents/receipts. This collector changes only the
    selected player temporarily; it does not toggle AI, advance time, or save.
    """
    from native_observation import build_observation
    tags = list(tags)
    if not tags or len(set(tags)) != len(tags) or any(not re.fullmatch(r"[A-Z]{3}", tag) for tag in tags):
        raise ValueError("distinct three-letter country tags are required")
    output = Path(outputdir)
    output.mkdir(parents=True, exist_ok=True)
    prefix, sequence, calls = "capture-" + uuid.uuid4().hex[:16], 0, []

    def call(method, *args):
        nonlocal sequence
        sequence += 1
        request_id = f"{prefix}.{sequence:04d}.{method}"
        result = getattr(game, method)(*args, request_id=request_id)
        calls.append({"request_id": request_id, "method": method, "args": args})
        return result

    main = call("status")
    controls = {tag: call("country_status", tag)["state"] for tag in tags}
    date_raw = controls[tags[0]]["date_raw"]
    for control in controls.values():
        _frozen(control, date_raw=date_raw)
        if control.get("ai_enabled") is not False:
            raise RuntimeError("disable native country AI before observation capture")
    played = {control["played_index"] for control in controls.values()}
    if len(played) != 1:
        raise RuntimeError("played country changed during initial capture")
    original = next((tag for tag, control in controls.items() if control["country_index"] in played), None)
    if original is None:
        raise RuntimeError("include the original played country in capture tags")
    world = read_world(game, main, controls)
    current, snapshots = original, {}
    try:
        for tag in tags:
            if current != tag:
                call("control", current, tag)
                current = tag
            control = call("country_status", tag)["state"]
            _frozen(control, date_raw=date_raw)
            if control["country_index"] != control["played_index"]:
                raise RuntimeError("capture player switch was not observed")
            snapshots[tag] = {"control": control, "divisions": call("units", tag),
                              "air": call("air", tag), "navy": call("navy", tag),
                              "goals": call("war_goals", tag), "diplomacy": {},
                              "state_date_raw": date_raw, "date": _save_date(control)}
            for other in tags:
                if other == tag:
                    continue
                relation = call("war_status", tag, other)
                snapshots[tag]["diplomacy"][other] = relation
                if relation.get("at_war"):
                    pair = sorted((tag, other))
                    row = {"first": pair[0], "second": pair[1]}
                    if row not in world["public_wars"]:
                        world["public_wars"].append(row)
    finally:
        if current != original:
            call("control", current, original)
    final = call("country_status", original)["state"]
    _frozen(final, date_raw=date_raw)
    if final["played_index"] != controls[original]["country_index"]:
        raise RuntimeError("original played country was not restored")
    world["date"] = snapshots[original]["date"]
    context = static_context() if map_context is None else map_context
    source = {"kind": "native_capture", "pid": game.pid, "creation_time": game.creation_time,
              "capture_id": prefix, "world": world["source"]}
    observations = {tag: build_observation(tag, snapshots[tag], matchid, turnid, source,
                                          state=world, map_context=context,
                                          history=(history or {}).get(tag, []) if isinstance(history, dict) else history)
                    for tag in tags}
    manifest = {"schema": "iron.native-capture.v1", **source, "date_raw": date_raw,
                "restored_country": original, "calls": calls, "files": {}}
    for name, value in (("snapshots.json", snapshots), ("world.json", world), ("observations.json", observations)):
        content = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        (output / name).write_bytes(content)
        manifest["files"][name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    (output / "capture.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"snapshots": snapshots, "state": world, "observations": observations,
            "map_context": context, "manifest": manifest}
