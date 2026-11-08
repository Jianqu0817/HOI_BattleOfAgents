"""Test clock sequencing in an owned synthetic process, never in HOI4."""
import json
from pathlib import Path
import subprocess
import time

from native_rpc import request

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    core = root / "runs/native-build"
    clock = root / "runs/native-clock-v1/iron_clock_v1.dll"
    host = subprocess.Popen([str(core / "iron_bridge_test_host.exe")], stdout=subprocess.PIPE,
                            text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        info = json.loads(host.stdout.readline())
        pid = info["pid"]
        for dll in [core / "iron_bridge.dll", clock]:
            subprocess.run([str(core / "iron_injector.exe"), str(pid), str(dll)], check=True,
                           capture_output=True, text=True)
            if dll.name == "iron_bridge.dll":
                assert request(pid, "ping")["ok"]
        def call(payload, **kwargs):
            response = request(pid, "native_clock_v1", payload, **kwargs)
            assert response["ok"], response
            assert response["thread_id"] == info["main_thread_id"]
            return response["result"]
        deadline = time.monotonic() + 3
        while True:
            response = request(pid, "native_clock_v1", "status")
            if response["ok"]:
                break
            assert time.monotonic() < deadline, response
            time.sleep(0.02)
        assert response["result"]["state"]["paused"]
        assert call("build_pause")["constructed_verified"]
        assert not call("arm 0")["ok"]
        assert not call("pause 0 0")["ok"]  # Guard mismatch must not toggle.
        assert call("pause 1 1")["already_desired"]
        step = call("step 24", request_id="clock-step")
        assert step["ok"] and step["queue_accepted"] and step["armed_hours"] == 24
        state = call("status")["state"]
        assert not state["paused"] and state["remaining_hours"] == 24
        assert not call("arm 1")["ok"]
        assert call("pause 0 1")["queue_accepted"]
        assert call("status")["state"]["paused"]
        assert call("step 24", request_id="clock-step") == step
        assert call("status")["state"]["paused"]  # Receipt reuse did not toggle again.
        assert call("arm 12")["state"]["remaining_hours"] == 12
        print(json.dumps({"synthetic_only": True, "clock_sidecar_registration": True,
                          "callback_main_thread": True, "construction_only": True,
                          "expected_pause_guard": True, "already_desired_no_toggle": True,
                          "invalid_hours_rejected": True, "arm_before_unpause": True,
                          "arm_requires_paused": True, "ordinary_pause_post": True,
                          "step_receipt_does_not_replay": True}, indent=2))
    finally:
        host.terminate()
        host.wait(timeout=5)
