import asyncio
import ctypes
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from main import Dashboard
from function.capture import CaptionSession


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.launcher = patch("main.ensure_live_captions", return_value=None)
        self.launcher.start()
        self.addCleanup(self.launcher.stop)
        self.loop = asyncio.new_event_loop()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = Dashboard(self.root, self.loop)
        self.root.update_idletasks()
        self.pump(lambda: self.app.startup_task is None)

    def tearDown(self) -> None:
        for task in asyncio.all_tasks(self.loop):
            task.cancel()
        self.loop.run_until_complete(asyncio.sleep(0))
        self.loop.run_until_complete(self.loop.shutdown_default_executor())
        try:
            if not self.app.closed and self.app.poll_id is not None:
                self.root.after_cancel(self.app.poll_id)
            self.root.destroy()
        except tk.TclError:
            pass
        self.loop.close()

    def pump(self, predicate, timeout: float = 3) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        self.assertTrue(predicate())

    def test_native_title_bar_and_taskbar_window_style(self) -> None:
        self.root.deiconify()
        self.root.update()
        self.assertFalse(self.root.overrideredirect())
        if sys.platform == "win32":
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -16)
            extended = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            self.assertEqual(style & 0x00C00000, 0x00C00000)  # WS_CAPTION
            self.assertTrue(style & 0x00080000)  # WS_SYSMENU
            self.assertTrue(style & 0x00020000)  # WS_MINIMIZEBOX
            self.assertTrue(style & 0x00010000)  # WS_MAXIMIZEBOX
            self.assertFalse(extended & 0x00000080)  # WS_EX_TOOLWINDOW
            self.assertEqual(ctypes.windll.user32.GetWindow(hwnd, 4), 0)  # no owner

    def test_buttons_do_not_drag_the_window(self) -> None:
        self.assertEqual(self.app.start_btn.bind("<B1-Motion>"), "")
        self.assertEqual(self.app.stop_btn.bind("<B1-Motion>"), "")
        self.assertEqual(self.root.bind("<B1-Motion>"), "")

    def test_startup_failure_is_visible_and_allows_manual_retry(self) -> None:
        with patch("main.ensure_live_captions", side_effect=RuntimeError("not ready")), patch(
            "main.messagebox.showerror"
        ) as error:
            self.app.startup_task = self.loop.create_task(self.app.initialize_captions())
            self.pump(lambda: self.app.startup_task is None)
            error.assert_called_once()
            self.assertEqual(str(self.app.start_btn["state"]), tk.NORMAL)
            self.assertIn("手动", self.app.status["text"])

    def test_cannot_record_while_startup_is_pending(self) -> None:
        async def initializing():
            await asyncio.sleep(0.05)
        self.app.startup_task = self.loop.create_task(initializing())
        with patch("main.choose_save_dir") as choose:
            self.app.start_capture()
            choose.assert_not_called()
        self.app.request_close()
        self.pump(lambda: self.app.closed)
        self.assertTrue(self.app.startup_task.done())

    def test_capture_failure_is_visible_and_can_retry(self) -> None:
        async def failing_hook(filename, event):
            raise RuntimeError("Test capture disconnected")
        with patch("main.choose_save_dir", return_value="test.txt"), patch(
            "main.hook", side_effect=failing_hook
        ), patch("main.messagebox.showerror") as error:
            self.app.start_capture()
            self.pump(lambda: self.app.hook_task is None)
            error.assert_called_once()
            self.assertEqual(str(self.app.start_btn["state"]), tk.NORMAL)
            self.assertEqual(str(self.app.stop_btn["state"]), tk.DISABLED)

    def test_stop_flushes_then_allows_two_independent_recordings(self) -> None:
        sessions = []
        async def fake_hook(filename, event):
            sessions.append(filename)
            session = CaptionSession(filename)
            await session.observe(f"Recording number {len(sessions)}")
            await event.wait()
            await asyncio.sleep(0.02)
            await session.finish()
        with tempfile.TemporaryDirectory() as directory:
            with patch("function.save.save_dir", directory), patch(
                "main.hook", side_effect=fake_hook
            ), patch("main.messagebox.askyesno") as question:
                for _ in range(2):
                    self.app.start_capture()
                    self.app.stop_capture()
                    self.app.stop_capture()
                    self.assertEqual(str(self.app.start_btn["state"]), tk.DISABLED)
                    # A second start cannot clear the stop event during flush.
                    self.app.start_capture()
                    self.pump(lambda: self.app.hook_task is None)
                    self.assertFalse(self.app.closed)
                    self.assertFalse(self.app.closing)
                    self.assertEqual(str(self.app.start_btn["state"]), tk.NORMAL)
                    self.assertEqual(str(self.app.stop_btn["state"]), tk.DISABLED)
                question.assert_not_called()
                self.assertEqual(len(sessions), 2)
                self.assertNotEqual(*sessions)
                for i, filename in enumerate(sessions, 1):
                    self.assertIn(f"Recording number {i}", Path(filename).read_text(encoding="utf-8"))

    def test_minimize_and_restore_keep_recording(self) -> None:
        async def fake_hook(filename, event):
            await event.wait()
        with patch("main.choose_save_dir", return_value="test.txt"), patch(
            "main.hook", side_effect=fake_hook
        ):
            self.root.deiconify()
            self.root.update()
            self.app.start_capture()
            self.root.iconify()
            self.pump(lambda: self.root.state() == "iconic")
            self.assertFalse(self.app.exit_event.is_set())
            self.assertIsNotNone(self.app.hook_task)
            self.root.deiconify()
            self.pump(lambda: self.root.state() == "normal")
            self.app.stop_capture()
            self.pump(lambda: self.app.hook_task is None)

    def test_close_cancel_keeps_recording(self) -> None:
        async def fake_hook(filename, event):
            await event.wait()
        with patch("main.choose_save_dir", return_value="test.txt"), patch(
            "main.hook", side_effect=fake_hook
        ), patch("main.messagebox.askyesno", return_value=False) as question:
            self.app.start_capture()
            self.app.request_close()
            question.assert_called_once()
            self.assertFalse(self.app.exit_event.is_set())
            self.assertFalse(self.app.closing)
            self.assertIsNone(self.app.close_task)
            self.app.stop_capture()
            self.pump(lambda: self.app.hook_task is None)

    def test_close_confirm_waits_for_flush_and_is_idempotent(self) -> None:
        flushed = []
        async def fake_hook(filename, event):
            await event.wait()
            await asyncio.sleep(0.02)
            flushed.append(filename)
        with patch("main.choose_save_dir", return_value="test.txt"), patch(
            "main.hook", side_effect=fake_hook
        ), patch("main.messagebox.askyesno", return_value=True) as question:
            self.app.start_capture()
            self.app.request_close()
            closing_task = self.app.close_task
            self.app.request_close()
            self.assertIs(self.app.close_task, closing_task)
            question.assert_called_once()
            self.pump(lambda: closing_task.done())
            self.assertTrue(self.app.closed)
            self.assertEqual(flushed, ["test.txt"])
            self.assertIsNone(closing_task.exception())

    def test_close_during_save_waits_for_same_session(self) -> None:
        async def fake_hook(filename, event):
            await event.wait()
            await asyncio.sleep(0.02)
        with patch("main.choose_save_dir", return_value="test.txt"), patch(
            "main.hook", side_effect=fake_hook
        ), patch("main.messagebox.askyesno", return_value=True):
            self.app.start_capture()
            self.app.stop_capture()
            self.app.request_close()
            self.pump(lambda: self.app.closed)
            self.assertIsNone(self.app.close_task.exception())

    def test_close_save_failure_leaves_window_open(self) -> None:
        async def fake_hook(filename, event):
            await event.wait()
            raise OSError("disk full")
        with patch("main.choose_save_dir", return_value="test.txt"), patch(
            "main.hook", side_effect=fake_hook
        ), patch("main.messagebox.askyesno", return_value=True), patch(
            "main.messagebox.showerror"
        ) as error:
            self.app.start_capture()
            self.app.request_close()
            self.pump(lambda: self.app.close_task.done())
            error.assert_called_once()
            self.assertFalse(self.app.closed)
            self.assertFalse(self.app.closing)

    def test_idle_close_does_not_prompt(self) -> None:
        with patch("main.messagebox.askyesno") as question:
            self.app.request_close()
            self.pump(lambda: self.app.closed)
            question.assert_not_called()

    def test_idle_stop_does_nothing(self) -> None:
        self.app.stop_capture()
        self.assertIsNone(self.app.close_task)
        self.assertFalse(self.app.closing)
        self.assertEqual(str(self.app.stop_btn["state"]), tk.DISABLED)

    def test_path_error_does_not_start_capture(self) -> None:
        with patch("main.choose_save_dir", side_effect=PermissionError("read only")), patch(
            "main.messagebox.showerror"
        ) as error:
            self.app.start_capture()
            error.assert_called_once()
            self.assertIsNone(self.app.hook_task)
            self.assertFalse(self.app.dialog_open)
            self.assertEqual(str(self.app.start_btn["state"]), tk.NORMAL)


if __name__ == "__main__":
    unittest.main()
