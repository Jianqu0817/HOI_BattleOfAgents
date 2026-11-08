"""Exercise typed country/AI/save control only in an owned synthetic host."""
import json
from pathlib import Path
import subprocess
import time
from native_rpc import request

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    core = root / "runs/native-build"
    control = root / "runs/native-control-v1/iron_control_v1.dll"
    host = subprocess.Popen([str(core / "iron_bridge_test_host.exe")], stdout=subprocess.PIPE,
                            text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        info = json.loads(host.stdout.readline()); pid = info["pid"]
        for dll in [core / "iron_bridge.dll", control]:
            subprocess.run([str(core / "iron_injector.exe"), str(pid), str(dll)], check=True, capture_output=True, text=True)
            if dll.name == "iron_bridge.dll": assert request(pid, "ping")["ok"]
        def call(payload, **kwargs):
            result = request(pid, "native_control_v1", payload, **kwargs)
            assert result["ok"] and result["thread_id"] == info["main_thread_id"], result
            return result["result"]
        deadline = time.monotonic() + 3
        while True:
            first = request(pid, "native_control_v1", "status AUS")
            if first["ok"]: break
            assert time.monotonic() < deadline; time.sleep(0.02)
        assert first["result"]["state"]["date"] == "1936-05-22T02:00:00"
        assert not call("tag ITA GER")["ok"]
        assert not call("tag AUS invalid")["ok"]
        previous = "AUS"
        for target in ["GER", "ITA", "HUN", "CZE", "AUS"]:
            changed = call(f"tag {previous} {target}")
            assert changed["ok"] and changed["effect_verified"]
            disabled = call(f"ai {target} 1 0", request_id=f"disable-{target}")
            assert disabled["ok"] and not disabled["state"]["ai_enabled"]
            assert call(f"ai {target} 1 0")["already_desired"]
            assert call(f"ai {target} 1 0", request_id=f"disable-{target}") == disabled
            previous = target
        for target in ["AUS", "GER", "ITA", "HUN", "CZE"]:
            state = call(f"status {target}")["state"]
            assert not state["ai_enabled"] and state["paused"] and state["played_index"] == 11
        assert not call("save ../bad")["ok"]
        assert not call("save bad.hoi4")["ok"]
        saved = call("save iron_snapshot_001")
        assert saved["ok"] and not saved["file_verified"]
        print(json.dumps({"synthetic_only": True, "main_thread_control_registration": True,
                          "played_country_expected_guard": True, "five_country_switch_and_readback": True,
                          "five_country_ai_disable_and_readback": True, "already_desired_no_toggle": True,
                          "ai_receipt_does_not_replay": True, "date_codec": True,
                          "save_basename_restriction": True, "save_ack_separate_from_file": True}, indent=2))
    finally:
        host.terminate(); host.wait(timeout=5)
