"""The normal launcher may show API help without opening the game or the desktop."""
import os
from pathlib import Path
import subprocess
import unittest

from m0_probe import Probe

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows launcher")
    def test_default_launcher_only_prints_native_help(self):
        result = subprocess.run(["cmd", "/d", "/c", "Start-IRON.cmd"], cwd=ROOT,
                                capture_output=True, timeout=10, check=True)
        self.assertIn(b"native_campaign.py", result.stdout)
        self.assertIn(b"--save-dir", result.stdout)

    def test_archived_probe_fails_before_opening_a_session(self):
        with self.assertRaisesRegex(RuntimeError, "native_campaign"):
            Probe(None, [])
