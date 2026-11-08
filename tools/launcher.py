"""Small Windows console launcher; API credentials stay in process memory."""
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/Scripts/python.exe"


def call(*args):
    python = str(PYTHON) if PYTHON.exists() else sys.executable
    return subprocess.run([python, *map(str, args)], cwd=ROOT).returncode


def config_prompt():
    return input("比赛配置 [configs/campaign.example.json]：").strip() or "configs/campaign.example.json"


def enter_keys(config_path):
    from campaign import load_config, key_envs
    for env in key_envs(load_config(config_path)):
        if not os.environ.get(env):
            value = getpass.getpass(f"请输入 {env}（不回显，不保存到文件）：")
            if not value:
                raise ValueError("未输入密钥")
            os.environ[env] = value


def main():
    os.chdir(ROOT)
    sys.stdout.reconfigure(encoding="utf-8")
    print("IRON · AI 国家对战与视频素材工作台")
    while True:
        print("\n1 安装/初始化   2 环境检查   3 开始对局   4 恢复对局\n5 打开回看     6 查看制作指南   0 退出")
        choice = input("选择：").strip()
        try:
            if choice == "0":
                return
            if choice == "1":
                call(ROOT / "tools/setup.py")
            elif choice == "2":
                call(ROOT / "tools/campaign.py", "doctor", "--config", config_prompt())
            elif choice == "3":
                config = config_prompt()
                name = input("新对局名称（字母数字下划线）：").strip()
                enter_keys(config)
                call(ROOT / "tools/campaign.py", "run", "--name", name, "--config", config)
            elif choice == "4":
                name = input("对局名称：").strip()
                manifest = ROOT / "runs/campaigns" / name / "manifest.json"
                from campaign import name_ok, key_envs
                name_ok(name)
                config = json.loads(manifest.read_text(encoding="utf-8"))["config"]
                for env in key_envs(config):
                    if not os.environ.get(env):
                        os.environ[env] = getpass.getpass(f"{env}（不回显）：")
                call(ROOT / "tools/campaign.py", "resume", "--name", name)
            elif choice == "5":
                name = input("对局名称：").strip()
                from campaign import name_ok
                name_ok(name)
                index = ROOT / "runs/campaigns" / name / "index.html"
                if not index.exists():
                    raise ValueError("尚未导出完成回合")
                webbrowser.open(index.as_uri())
            elif choice == "6":
                os.startfile(ROOT / "docs/QUICKSTART.md")
                os.startfile(ROOT / "docs/VIDEO_WORKFLOW.md")
        except (ValueError, FileNotFoundError, KeyError) as error:
            print("无法继续：", error)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已退出。正在执行的回合可通过恢复入口核验，不要重新提交同一批命令。")
