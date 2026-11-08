"""Start the isolated local api_v2 service without a visible console window."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request


REVISION = "04a44eb977586645f33594b72af13a035a2056c6"


def ready(url):
    try:
        with urllib.request.urlopen(url + "/openapi.json", timeout=2) as response:
            return "/tts" in json.load(response).get("paths", {})
    except (OSError, ValueError):
        return False


def start(root, preferred_port=9880, wait_seconds=120):
    root = Path(root).resolve()
    state_path = root / "iron-service.json"
    if state_path.is_file():
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        if ready(previous["base_url"]):
            return {**previous, "readiness": "ready", "reused": True}
    python = root / ".venv/Scripts/python.exe"
    if not python.is_file() or not (root / "iron-tts-cpu.yaml").is_file():
        raise RuntimeError("Run the documented isolated setup first: " + str(root))
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if revision != REVISION:
        raise RuntimeError("Backend revision differs from tested pin: " + revision)
    port = preferred_port
    while True:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                break
            except OSError:
                port += 1
    env = dict(os.environ, PYTHONUTF8="1", OMP_NUM_THREADS="8", MKL_NUM_THREADS="8")
    with (root / "api-iron.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen([str(python), "-u", "api_v2.py", "-a", "127.0.0.1", "-p", str(port),
                                    "-c", "iron-tts-cpu.yaml"], cwd=root, env=env, stdout=log,
                                   stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    state = {"pid": process.pid, "base_url": f"http://127.0.0.1:{port}", "api_version": "api_v2",
             "backend_revision": revision, "device": "cpu", "log": str(root / "api-iron.log")}
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline and process.poll() is None:
        if ready(state["base_url"]):
            return {**state, "readiness": "ready"}
        time.sleep(1)
    return {**state, "readiness": "starting" if process.poll() is None else "failed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="D:/GPT-SoVITS_IRON")
    parser.add_argument("--port", type=int, default=9880)
    parser.add_argument("--wait-seconds", type=int, default=120)
    args = parser.parse_args()
    print(json.dumps(start(args.root, args.port, args.wait_seconds), ensure_ascii=False, indent=2))
