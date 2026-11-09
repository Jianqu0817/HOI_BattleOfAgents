"""Record a native campaign with the existing OBS game-capture scene."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from threading import Event, Thread
import time
import uuid

from campaign import load_config, name_ok, ROOT
from m0_probe import write_json
from native_campaign import run_campaign


def now():
    return datetime.now(timezone.utc).isoformat()


class OBS:
    def __init__(self, path):
        import websocket
        config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        self.socket = websocket.create_connection(f"ws://127.0.0.1:{config['server_port']}", timeout=10)
        hello = json.loads(self.socket.recv())["d"]
        identify = {"rpcVersion": 1, "eventSubscriptions": 0}
        if "authentication" in hello:
            auth = hello["authentication"]
            secret = base64.b64encode(hashlib.sha256((config["server_password"] + auth["salt"]).encode()).digest()).decode()
            identify["authentication"] = base64.b64encode(hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
        self.socket.send(json.dumps({"op": 1, "d": identify}))
        if json.loads(self.socket.recv()).get("op") != 2:
            raise RuntimeError("OBS authentication failed")

    def call(self, operation, data=None):
        identity = uuid.uuid4().hex
        self.socket.send(json.dumps({"op": 6, "d": {"requestType": operation, "requestId": identity, "requestData": data or {}}}))
        while True:
            message = json.loads(self.socket.recv())
            if message.get("op") == 7 and message["d"]["requestId"] == identity:
                result = message["d"]
                if not result["requestStatus"]["result"]:
                    raise RuntimeError(f"OBS {operation}: {result['requestStatus']}")
                return result.get("responseData", {})

    def close(self):
        self.socket.close()


def preview_loop(path, directory, scene, stop):
    client = None
    try:
        client = OBS(path)
        while not stop.is_set():
            response = client.call("GetSourceScreenshot", {"sourceName": scene, "imageFormat": "png", "imageWidth": 1280, "imageHeight": 720})
            target = directory / "recording_preview.png"
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(base64.b64decode(response["imageData"].split(",", 1)[1]))
            temporary.replace(target)
            stop.wait(10)
    except Exception as error:
        write_json(directory / "preview_error.json", {"at": now(), "error": str(error)})
    finally:
        if client:
            client.close()


def record(name, config_path, pid, save_dir, obs_config, *, resume=False, through_turn=50):
    directory = ROOT / "runs/campaigns" / name_ok(name)
    directory.mkdir(parents=True, exist_ok=True)
    client, stop = OBS(obs_config), Event()
    started, recorder = False, None
    state = {"active": False, "status": "正在检查OBS", "prepared_at": now()}
    start = time.monotonic()
    try:
        if client.call("GetProfileList")["currentProfileName"] != "IRON_VERIFY":
            raise RuntimeError("Select the existing IRON_VERIFY OBS profile first")
        if client.call("GetSceneCollectionList")["currentSceneCollectionName"] != "IRON_VERIFY":
            raise RuntimeError("Select the existing IRON_VERIFY OBS scene collection first")
        if client.call("GetRecordStatus")["outputActive"]:
            raise RuntimeError("OBS is already recording; existing recording was left untouched")
        scene = client.call("GetSceneList")["currentProgramSceneName"]
        video = client.call("GetVideoSettings")
        if any(video[key] != value for key, value in {"baseWidth": 1280, "baseHeight": 720, "outputWidth": 1280, "outputHeight": 720}.items()):
            raise RuntimeError("OBS dimensions differ from the verified 1280x720 game capture")
        output = ROOT / "recordings" / name
        output.mkdir(parents=True, exist_ok=True)
        client.call("SetRecordDirectory", {"recordDirectory": str(output)})
        client.call("StartRecord")
        started = True
        state.update(active=True, status="正在录制", started_at=now(), scene=scene, video=video, recording_directory=str(output))
        write_json(directory / "recording.json", state)
        recorder = Thread(target=preview_loop, args=(obs_config, directory, scene, stop), daemon=True)
        recorder.start()

        def require_recording(index):
            current = client.call("GetRecordStatus")
            state.update(last_checked_at=now(), round=index, timecode=current.get("outputTimecode"),
                         recorded_bytes=current.get("outputBytes"), active=current["outputActive"])
            write_json(directory / "recording.json", state)
            if not current["outputActive"] or current.get("outputPaused"):
                raise RuntimeError("Recording stopped or paused; no new campaign turn was started")

        result = run_campaign(name, config_path, pid, save_dir, resume=resume, through_turn=through_turn,
                              before_turn=require_recording)
        state["status"] = "本轮录制完成" if result["status"] == "completed" else "阶段录制完成"
        return result
    except Exception as error:
        state.update(status="录制已停止，需检查", error=str(error))
        raise
    finally:
        stop.set()
        if recorder:
            recorder.join(timeout=12)
        try:
            if started:
                response = client.call("StopRecord")
                state.update(active=False, output_path=response.get("outputPath"), ended_at=now(),
                             elapsed_seconds=round(time.monotonic() - start, 3))
        finally:
            write_json(directory / "recording.json", state)
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--save-dir", required=True, type=Path)
    parser.add_argument("--obs-config", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--turns", type=int, default=50)
    parser.add_argument("--key-file", type=Path, help="Existing ignored key file; loaded into process environment only")
    args = parser.parse_args()
    if args.key_file:
        if args.resume:
            from campaign import effective_resumed_config
            manifest = json.loads((ROOT / "runs/campaigns" / name_ok(args.name) / "manifest.json").read_text(encoding="utf-8"))
            config = effective_resumed_config(manifest)
        else:
            config = load_config(args.config)
        os.environ[config["model"]["api_key_env"]] = args.key_file.read_text(encoding="utf-8").strip()
    record(args.name, args.config, args.pid, args.save_dir, args.obs_config, resume=args.resume, through_turn=args.turns)


if __name__ == "__main__":
    main()
