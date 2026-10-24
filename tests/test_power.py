import asyncio
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from function.power import RecordingPowerGuard, _set_execution_state
from function.texthook import CaptionReader, hook


class PowerTests(unittest.TestCase):
    def test_native_api_failure_is_reported(self) -> None:
        library = Mock()
        library.SetThreadExecutionState.return_value = 0
        with patch("function.power.ctypes.WinDLL", return_value=library), \
                patch("function.power.ctypes.get_last_error", return_value=5):
            with self.assertRaises(OSError) as raised:
                _set_execution_state(0x80000003)
        self.assertEqual(raised.exception.errno, 5)
        library.SetThreadExecutionState.assert_called_once_with(0x80000003)

    def test_request_and_restore_previous_thread_requirements(self) -> None:
        with patch("function.power._set_execution_state", side_effect=[0x80000001, 0x80000003]) as call:
            guard = RecordingPowerGuard()
            guard.acquire()
            guard.acquire()
            guard.release()
            guard.release()
        self.assertEqual([args.args[0] for args in call.call_args_list],
                         [0x80000003, 0x80000001])

    def test_failed_request_is_visible_and_does_not_restore_unowned_state(self) -> None:
        with patch("function.power._set_execution_state", side_effect=OSError("denied")) as call:
            guard = RecordingPowerGuard()
            with self.assertRaisesRegex(OSError, "denied"):
                guard.acquire()
            guard.release()
        self.assertEqual(call.call_count, 1)

    def test_failed_restore_is_visible_and_can_be_retried(self) -> None:
        with patch("function.power._set_execution_state",
                   side_effect=[0x80000000, OSError("restore failed"), 0x80000003]) as call:
            guard = RecordingPowerGuard()
            guard.acquire()
            with self.assertRaisesRegex(OSError, "restore failed"):
                guard.release()
            guard.release()
        self.assertEqual([args.args[0] for args in call.call_args_list],
                         [0x80000003, 0x80000000, 0x80000000])

    def test_com_cleanup_failure_still_releases_power_request(self) -> None:
        reader = CaptionReader()
        reader.context = Mock()
        reader.context.Uninitialize.side_effect = RuntimeError("COM cleanup")
        with patch("function.power._set_execution_state", return_value=0x80000000) as call:
            reader.power.acquire()
            with self.assertRaisesRegex(RuntimeError, "COM cleanup"):
                reader.close()
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args.args[0], 0x80000000)


class PowerHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_and_disconnect_restore_request_and_flush(self) -> None:
        for disconnect in (False, True):
            with self.subTest(disconnect=disconnect):
                event = asyncio.Event()
                loop = asyncio.get_running_loop()
                calls = []

                class Viewer:
                    reads = 0

                    def Exists(self, timeout):
                        return True

                    @property
                    def Name(self):
                        self.reads += 1
                        if self.reads == 1:
                            return ""
                        if disconnect and self.reads > 2:
                            raise RuntimeError("disconnected")
                        if not disconnect:
                            loop.call_soon_threadsafe(event.set)
                        return "最后一句尚未稳定的字幕"

                root = Mock()
                root.Control.return_value.Control.return_value = Viewer()

                def execution_state(flags: int) -> int:
                    calls.append((flags, threading.get_ident()))
                    return 0x80000000

                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "captions.txt"
                    with patch("function.power._set_execution_state", side_effect=execution_state), \
                            patch("function.texthook.auto.UIAutomationInitializerInThread"), \
                            patch("function.texthook.auto.GetRootControl", return_value=root):
                        if disconnect:
                            with self.assertRaisesRegex(RuntimeError, "disconnected"):
                                await hook(str(path), event)
                        else:
                            await hook(str(path), event)
                    self.assertIn("最后一句尚未稳定的字幕", path.read_text(encoding="utf-8"))
                self.assertEqual([flags for flags, _ in calls], [0x80000003, 0x80000000])
                self.assertEqual(calls[0][1], calls[1][1])
                self.assertNotEqual(calls[0][1], threading.get_ident())

    async def test_open_failure_restores_on_the_same_worker(self) -> None:
        calls = []

        def execution_state(flags: int) -> int:
            calls.append((flags, threading.get_ident()))
            return 0x80000000

        with patch("function.power._set_execution_state", side_effect=execution_state), \
                patch("function.texthook.auto.UIAutomationInitializerInThread",
                      side_effect=RuntimeError("COM init")):
            with self.assertRaisesRegex(RuntimeError, "COM init"):
                await hook("unused.txt", asyncio.Event())
        self.assertEqual([flags for flags, _ in calls], [0x80000003, 0x80000000])
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertNotEqual(calls[0][1], threading.get_ident())
