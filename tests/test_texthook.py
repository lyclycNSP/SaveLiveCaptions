import asyncio
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from function.texthook import hook, lc_detect


class HookTests(unittest.IsolatedAsyncioTestCase):
    async def record_frames(self, initial: str, current: str) -> str:
        event = asyncio.Event()
        loop = asyncio.get_running_loop()
        class FakeReader:
            reads = 0
            def open(self):
                pass
            def read(self):
                self.reads += 1
                if self.reads == 1:
                    return initial
                loop.call_soon_threadsafe(event.set)
                return current
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.txt"
            path.touch()
            with patch("function.texthook.CaptionReader", FakeReader):
                await hook(str(path), event)
            return path.read_text(encoding="utf-8")

    async def test_stop_without_any_new_text_does_not_flush_history(self) -> None:
        self.assertEqual(await self.record_frames("Old captions.", "Old captions."), "")

    async def test_first_poll_only_records_new_suffix(self) -> None:
        text = await self.record_frames("Old captions.", "Old captions. New captions.")
        self.assertNotIn("Old captions", text)
        self.assertIn("New captions.", text)

    async def test_new_recording_has_a_fresh_baseline(self) -> None:
        first = await self.record_frames("Old captions.", "Old captions. First recording.")
        second = await self.record_frames(
            "Old captions. First recording.", "Old captions. First recording. Second recording."
        )
        self.assertIn("First recording.", first)
        self.assertNotIn("First recording.", second)
        self.assertNotIn("Old captions.", second)
        self.assertIn("Second recording.", second)

    async def test_empty_initial_frame_does_not_drop_first_new_sentence(self) -> None:
        self.assertIn("First new sentence.",
                      await self.record_frames("", "First new sentence."))

    def test_autostart_detection_only_requires_the_window(self) -> None:
        window = MagicMock()
        window.Exists.return_value = True
        with patch("function.texthook.auto.UIAutomationInitializerInThread"), patch(
            "function.texthook.auto.GetRootControl"
        ) as root:
            root.return_value.Control.return_value = window
            self.assertTrue(lc_detect())
            # Do not probe the text area: an existing initializing window must
            # not be closed by sending the toggle shortcut again.
            window.Control.assert_not_called()

    async def test_reads_and_com_cleanup_use_one_worker_and_flush_on_stop(self) -> None:
        calls = []
        class FakeReader:
            reads = 0
            def open(self):
                calls.append(("open", threading.get_ident()))
            def read(self):
                calls.append(("read", threading.get_ident()))
                self.reads += 1
                if self.reads == 1:
                    return "开始前已有的字幕。"
                return "最后的短句"
            def close(self):
                calls.append(("close", threading.get_ident()))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.txt"
            event = asyncio.Event()
            asyncio.get_running_loop().call_later(0.1, event.set)
            with patch("function.texthook.CaptionReader", FakeReader):
                await hook(str(path), event)
            self.assertIn("最后的短句", path.read_text(encoding="utf-8"))
            self.assertNotIn("开始前已有", path.read_text(encoding="utf-8"))
        self.assertEqual(len({identity for _, identity in calls}), 1)
        self.assertNotEqual(calls[0][1], threading.get_ident())
        self.assertEqual(calls[-1][0], "close")

    async def test_disconnect_reports_error_and_flushes_tail(self) -> None:
        closed = []
        class FakeReader:
            reads = 0
            def open(self):
                pass
            def read(self):
                self.reads += 1
                if self.reads == 1:
                    return "Old history."
                if self.reads > 2:
                    raise RuntimeError("disconnected")
                return "An unfinished tail"
            def close(self):
                closed.append(True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.txt"
            with patch("function.texthook.CaptionReader", FakeReader):
                with self.assertRaisesRegex(RuntimeError, "disconnected"):
                    await hook(str(path), asyncio.Event())
            self.assertIn("An unfinished tail", path.read_text(encoding="utf-8"))
        self.assertEqual(closed, [True])

    async def test_open_failure_closes_worker_and_does_not_create_text(self) -> None:
        closed = []
        class FakeReader:
            def open(self):
                raise RuntimeError("not found")
            def close(self):
                closed.append(True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.txt"
            with patch("function.texthook.CaptionReader", FakeReader):
                with self.assertRaisesRegex(RuntimeError, "not found"):
                    await hook(str(path), asyncio.Event())
            self.assertFalse(path.exists())
        self.assertEqual(closed, [True])
