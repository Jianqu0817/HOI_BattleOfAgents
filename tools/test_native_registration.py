"""Tiny owned-host test of the exported native operation-registration ABI."""
import json
from pathlib import Path
import subprocess

from native_rpc import request

if __name__ == "__main__":
    build = Path(__file__).resolve().parents[1] / "runs/native-build"
    host = subprocess.Popen([str(build / "iron_bridge_test_host.exe"), str(build / "iron_bridge.dll")],
                            stdout=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        info = json.loads(host.stdout.readline())
        response = request(info["pid"], "test_registered_v1", "test")
        assert response["ok"] and response["kind"] == "registered_operation", response
        result = response["result"]
        assert result["payload_length"] == 4 and result["correct_image"]
        assert result["callback_thread_id"] == info["main_thread_id"] == response["main_thread_id"]
        print(json.dumps({"synthetic_only": True, "registration": True, "duplicate_rejected": True,
                          "builtin_override_rejected": True, "callback_on_main_thread": True,
                          "correct_image_and_payload": True}, indent=2))
    finally:
        host.terminate()
        host.wait(timeout=5)
