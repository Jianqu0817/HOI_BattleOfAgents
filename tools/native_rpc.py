"""Windows named-pipe transport for IRON's main-thread native bridge.

A transport timeout has an unknown outcome. Keep the returned request ID: a
repeat with that same ID and identical content retrieves the original receipt.
Never automatically retry a game mutation with a new ID.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import time
import uuid


class NativeRpcError(RuntimeError):
    def __init__(self, message: str, *, request_id: str, outcome: str):
        super().__init__(message)
        self.request_id = request_id
        self.outcome = outcome


def process_creation_time(pid: int) -> int:
    """Exact process lifetime identifier, so journals cannot follow a reused PID."""
    if os.name != "nt":
        raise OSError("native RPC requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "cannot identify game process")
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(value) for value in values]):
            raise OSError(ctypes.get_last_error(), "cannot read process creation time")
        return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def request(pid: int, op: str, payload: str = "", *, request_id: str | None = None,
            timeout: float = 10.0) -> dict:
    """Send exactly once; responses acknowledge dispatch, not observed game effect."""
    if os.name != "nt":
        raise OSError("native RPC requires Windows")
    request_id = request_id or uuid.uuid4().hex
    if not request_id or len(request_id) > 64 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in request_id):
        raise ValueError("invalid request_id")
    if not op or any(c in op for c in "\t\r\n") or any(c in payload for c in "\r\n"):
        raise ValueError("operation and payload must fit one protocol line")
    data = f"{request_id}\t{op}\t{payload}\n".encode("utf-8")
    if len(data) > 65536:
        raise ValueError("request exceeds 65536 bytes")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel.WaitNamedPipeW.restype = wintypes.BOOL
    kernel.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.WriteFile.restype = wintypes.BOOL
    kernel.ReadFile.argtypes = kernel.WriteFile.argtypes
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.PeekNamedPipe.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    name = rf"\\.\pipe\iron-native-{int(pid)}"
    deadline = time.monotonic() + timeout
    handle = kernel.CreateFileW(name, 0xC0000000, 0, None, 3, 0, None)
    invalid = ctypes.c_void_p(-1).value
    # Waiting for an available connection never sends a request.
    while handle == invalid and ctypes.get_last_error() in (2, 231) and time.monotonic() < deadline:
        kernel.WaitNamedPipeW(name, min(100, max(1, int((deadline - time.monotonic()) * 1000))))
        time.sleep(0.01)
        handle = kernel.CreateFileW(name, 0xC0000000, 0, None, 3, 0, None)
    if handle == invalid:
        raise NativeRpcError(f"pipe connection failed ({ctypes.get_last_error()})", request_id=request_id, outcome="not_sent")
    try:
        count = wintypes.DWORD()
        if not kernel.WriteFile(handle, data, len(data), ctypes.byref(count), None) or count.value != len(data):
            raise NativeRpcError("request write failed", request_id=request_id, outcome="unknown")
        chunks = bytearray()
        while time.monotonic() < deadline:
            available = wintypes.DWORD()
            if not kernel.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
                raise NativeRpcError("pipe closed before receipt", request_id=request_id, outcome="unknown")
            if available.value:
                buffer = ctypes.create_string_buffer(min(available.value, 65536))
                if not kernel.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                    raise NativeRpcError("receipt read failed", request_id=request_id, outcome="unknown")
                chunks.extend(buffer.raw[:count.value])
                if b"\n" in chunks:
                    response = json.loads(chunks.split(b"\n", 1)[0])
                    if response.get("id") != request_id:
                        raise NativeRpcError("receipt ID mismatch", request_id=request_id, outcome="unknown")
                    return response
                if len(chunks) > 1048576:
                    raise NativeRpcError("receipt exceeds 1 MiB", request_id=request_id, outcome="unknown")
            time.sleep(0.005)
        raise NativeRpcError("receipt timeout; retain request ID for reconciliation", request_id=request_id, outcome="unknown")
    finally:
        kernel.CloseHandle(handle)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int)
    parser.add_argument("op")
    parser.add_argument("payload", nargs="?", default="")
    parser.add_argument("--request-id")
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    try:
        print(json.dumps(request(args.pid, args.op, args.payload, request_id=args.request_id, timeout=args.timeout), ensure_ascii=False))
    except NativeRpcError as error:
        print(json.dumps({"ok": False, "error": str(error), "request_id": error.request_id, "outcome": error.outcome}))
        raise SystemExit(1)
