"""Exercise injection and main-thread dispatch only in an owned synthetic host."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from native_rpc import NativeRpcError, request


def exercise(build: Path, delay: bool = False) -> dict:
    env = os.environ.copy()
    if delay:
        env["IRON_TEST_INITIAL_DELAY_MS"] = "6500"
    host = subprocess.Popen([str(build / "iron_bridge_test_host.exe")], stdout=subprocess.PIPE,
                            text=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        info = json.loads(host.stdout.readline())
        injected = subprocess.run([str(build / "iron_injector.exe"), str(info["pid"]),
                                   str(build / "iron_bridge.dll")], check=True, capture_output=True, text=True)
        assert json.loads(injected.stdout)["loaded"]
        pid = info["pid"]
        ping = request(pid, "ping")
        assert ping["ok"] and ping["kind"] == "transport"
        if delay:
            cancelled = request(pid, "test_echo", "cancel-me", request_id="cancelled", timeout=7)
            assert not cancelled["ok"] and cancelled["kind"] == "not_dispatched", cancelled
            time.sleep(1.7)
            assert request(pid, "test_echo", "cancel-me", request_id="cancelled") == cancelled
            status = request(pid, "main_status")
            assert status["thread_id"] == info["main_thread_id"]
            return {"queue_timeout_cancelled": True, "cancelled_receipt_preserved": True}
        status = request(pid, "main_status", request_id="status")
        assert status["ok"] and status["result"]["synthetic"]
        assert status["thread_id"] == info["main_thread_id"] == status["main_thread_id"]
        assert ping["thread_id"] != status["thread_id"]
        assert request(pid, "main_status", request_id="status") == status
        collision = request(pid, "test_echo", "different", request_id="status")
        assert not collision["ok"] and collision["kind"] == "rejected"
        echo = request(pid, "test_echo", '测试\\"\tcontent')
        assert echo["result"] == '测试\\"\tcontent'
        rejected = request(pid, "native_move", "post 0 0 0 0")
        assert not rejected["ok"]  # Game calls cannot run in a synthetic host.
        try:
            request(pid, "main_status", timeout=0)
        except NativeRpcError as error:
            assert error.outcome == "unknown"
            receipt = request(pid, "main_status", request_id=error.request_id)
            assert receipt["ok"]
        else:
            raise AssertionError("zero timeout should report an unknown outcome")
        return {"injection": True, "main_thread_dispatch": True, "worker_thread_ping": True,
                "idempotent_receipt": True, "request_id_collision_rejected": True,
                "unicode_payload": True, "synthetic_game_call_rejected": True,
                "unknown_timeout_reconciled": True}
    finally:
        host.terminate()
        host.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, default=Path(__file__).resolve().parents[1] / "runs/native-build")
    args = parser.parse_args()
    print(json.dumps({"synthetic_only": True, **exercise(args.build.resolve()),
                      **exercise(args.build.resolve(), delay=True)}, indent=2))
