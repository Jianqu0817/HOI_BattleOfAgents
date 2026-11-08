"""Run the pinned HOI4 Live Tools CLI with project-local configuration."""
from pathlib import Path
import os
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
DEPENDENCY = ROOT / ".deps" / "hoi4-live-tools"
if not (DEPENDENCY / "hoi4_live" / "__main__.py").is_file():
    raise SystemExit("Missing .deps/hoi4-live-tools; see README.md.")
os.environ.setdefault("HOI4_LIVE_CONFIG", str(ROOT / "configs" / "live.local.json"))
sys.path.insert(0, str(DEPENDENCY))
from live_compat import install
install()
runpy.run_module("hoi4_live", run_name="__main__")
