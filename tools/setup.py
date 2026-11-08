"""Install the pinned backend and generate machine-local configuration."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REVISION = "f644f9a0c8f86c8d8d635687b93116437870e085"


def run(args):
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True)


def setup(game_dir=None, user_dir=None):
    if sys.platform != "win32":
        raise ValueError("游戏控制需要 Windows；离线测试与视频素材导出可在其他系统运行。")
    dependency = ROOT / ".deps/hoi4-live-tools"
    if not dependency.exists():
        dependency.parent.mkdir(exist_ok=True)
        run(["git", "clone", "https://github.com/klimPaskov/HOI4-Live-Tools.git", dependency])
        run(["git", "-C", dependency, "checkout", REVISION])
    actual = subprocess.check_output(["git", "-C", str(dependency), "rev-parse", "HEAD"], text=True).strip()
    if actual != REVISION:
        raise ValueError("已有后端版本不同，请在独立目录保留它，再安装本项目固定版本。")
    venv_python = ROOT / ".venv/Scripts/python.exe"
    if not venv_python.exists():
        run([sys.executable, "-m", "venv", ROOT / ".venv"])
    missing = subprocess.run([str(venv_python), "-c", "import numpy,PIL,cv2"], capture_output=True).returncode
    if missing:
        run([venv_python, "-m", "pip", "install", "-r", ROOT / "requirements.txt"])
    sys.path.insert(0, str(dependency))
    from hoi4_live.platform.windows import WindowsPlatform
    platform = WindowsPlatform()
    game_dir = Path(game_dir or platform.game_dir() or "")
    user_dir = Path(user_dir or platform.user_dir())
    if not (game_dir / "hoi4.exe").is_file():
        raise ValueError("未找到 hoi4.exe，请用 --game-dir 指定安装目录。")
    config_path = ROOT / "configs/live.local.json"
    if not config_path.exists():
        config = {"game_dir": str(game_dir.resolve()), "user_dir": str(user_dir.resolve()),
                  "artifacts_dir": str(ROOT / "runs/backend/artifacts"), "mode": "test",
                  "window": {"pin_client_to_origin": True},
                  "iron_profile": {"render_size": [1280, 720], "virtual_keys": True},
                  "ocr": {"language": "zh-Hans-CN"}, "call_log": str(ROOT / "runs/backend/calls.jsonl")}
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已生成 configs/live.local.json")
    else:
        print("已保留现有 configs/live.local.json")
    print("安装完成。请在游戏中载入单人非铁人文本存档，暂停，再运行环境检查。")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-dir")
    parser.add_argument("--user-dir")
    args = parser.parse_args()
    try:
        setup(args.game_dir, args.user_dir)
    except (ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
