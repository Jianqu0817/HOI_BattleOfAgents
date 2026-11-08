"""Launcher routes setup and guide choices without touching the game."""
from unittest.mock import MagicMock, patch
import unittest

import launcher


class LauncherTests(unittest.TestCase):
    def test_europe_setup_and_guides_dispatch_without_extra_confirmation(self):
        with patch("builtins.input", side_effect=["7", "europe_setup_01", "8", "0"]), \
                patch.object(launcher, "call", return_value=0) as call, \
                patch.object(launcher.os, "startfile", create=True) as startfile, \
                patch.object(launcher.sys, "stdout", MagicMock()):
            launcher.main()
        call.assert_called_once_with(launcher.ROOT / "tools/prepare_europe.py", "--name", "europe_setup_01")
        self.assertEqual([item.args[0].name for item in startfile.call_args_list],
                         ["ARMY_CONTROL.md", "DIPLOMACY_CONTROL.md", "NAVAL_AIR.md"])

    def test_invalid_setup_name_does_not_dispatch(self):
        with patch("builtins.input", side_effect=["7", "../unsafe", "0"]), \
                patch.object(launcher, "call") as call, patch.object(launcher.sys, "stdout", MagicMock()):
            launcher.main()
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
