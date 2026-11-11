"""Native recording-view registration and guards in the owned synthetic host."""
import json
from pathlib import Path
import subprocess
import time

from native_rpc import request


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    core = root / "runs/native-build"
    host = subprocess.Popen([str(core / "iron_bridge_test_host.exe")], stdout=subprocess.PIPE,
                            text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        info = json.loads(host.stdout.readline())
        pid = info["pid"]
        for dll in (core / "iron_bridge.dll", root / "runs/native-view-v3/iron_view_v3.dll"):
            subprocess.run([str(core / "iron_injector.exe"), str(pid), str(dll)], check=True, capture_output=True)
            if dll.name == "iron_bridge.dll":
                assert request(pid, "ping")["ok"]
        deadline = time.monotonic() + 3
        while True:
            response = request(pid, "native_view_v3", "status")
            if response["ok"]:
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)

        def call(payload, **kwargs):
            receipt = request(pid, "native_view_v3", payload, **kwargs)
            assert receipt["ok"] and receipt["thread_id"] == info["main_thread_id"]
            return receipt["result"]

        assert not call("status")["state"]["hud_hidden"]
        assert not call("hud 0 2")["ok"]
        assert not call("hud 1 1")["ok"]
        hidden = call("hud 0 1", request_id="view-hide")
        assert hidden["ok"] and hidden["effect_verified"] and hidden["state"]["hud_hidden"]
        assert call("hud 0 1")["already_desired"]
        assert hidden == call("hud 0 1", request_id="view-hide")
        assert not call("lock_camera 1 1")["ok"]
        locked = call("lock_camera 0 1", request_id="view-lock")
        assert locked["effect_verified"] and locked["state"]["camera_locked"]
        assert call("lock_camera 0 1")["already_desired"]
        assert locked == call("lock_camera 0 1", request_id="view-lock")
        for province in ("0", "-1", "20000", "671junk"):
            assert not call("center_province " + province)["ok"]
        centered = call("center_province 671")
        assert centered["ok"] and centered["camera_request_accepted"]
        assert centered["state"]["camera_target"][0] == 671
        assert centered["state"]["date_raw"] == hidden["state"]["date_raw"]
        assert call("frame_province 671 90")["state"]["camera_target"][1] == 90
        for height in ("0", "49", "1201", "NaN"):
            assert not call("frame_province 671 " + height)["ok"]
        assert call("hud 1 0")["state"]["hud_hidden"] is False
        assert call("lock_camera 1 0")["state"]["camera_locked"] is False
        assert not call("execute anything")["ok"]
        print(json.dumps({"synthetic_only": True, "main_thread": True, "hud_readback": True,
                          "idempotent_hud": True, "expected_state_guard": True,
                          "province_bounds": True, "date_preserved": True}))
    finally:
        host.terminate()
        host.wait(timeout=5)
