from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from function.livecaptions import ensure_live_captions


class LiveCaptionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context_patch = patch("function.livecaptions.auto.UIAutomationInitializerInThread")
        self.context = self.context_patch.start()
        self.addCleanup(self.context_patch.stop)
        self.root_patch = patch("function.livecaptions.auto.GetRootControl")
        self.root = self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.send_patch = patch("function.livecaptions.auto.SendKeys")
        self.send = self.send_patch.start()
        self.addCleanup(self.send_patch.stop)
        self.window = MagicMock()
        self.root.return_value.Control.return_value = self.window

    def test_existing_window_is_never_toggled_or_probed_for_text(self) -> None:
        self.window.Exists.return_value = True
        ensure_live_captions()
        self.send.assert_not_called()
        self.window.Control.assert_not_called()
        self.context.return_value.__exit__.assert_called_once()

    def test_missing_window_is_opened_once_and_waited_for(self) -> None:
        self.window.Exists.side_effect = [False, True]
        ensure_live_captions(timeout=3)
        self.send.assert_called_once_with("{Ctrl}{Win}l")
        self.window.Exists.assert_any_call(3, 0.2)

    def test_timeout_reports_actionable_error(self) -> None:
        self.window.Exists.return_value = False
        with self.assertRaisesRegex(RuntimeError, "手动打开"):
            ensure_live_captions(timeout=0)
        self.send.assert_called_once()
        self.context.return_value.__exit__.assert_called_once()

    def test_shortcut_failure_is_propagated_and_com_is_released(self) -> None:
        self.window.Exists.return_value = False
        self.send.side_effect = OSError("shortcut failed")
        with self.assertRaisesRegex(OSError, "shortcut failed"):
            ensure_live_captions()
        self.context.return_value.__exit__.assert_called_once()
