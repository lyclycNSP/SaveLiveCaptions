"""Windows UI Automation stays on one COM worker, away from the Tk thread."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import uiautomation as auto

from .capture import CaptionSession
from .baseline import StartupBaseline
from .sentences import is_incomplete_sentence, split_into_sentences


class CaptionReader:
    def __init__(self) -> None:
        self.context: auto.UIAutomationInitializerInThread | None = None
        self.viewer: auto.Control | None = None

    def open(self) -> None:
        self.context = auto.UIAutomationInitializerInThread()
        auto.SetGlobalSearchTimeout(0.5)
        window = auto.GetRootControl().Control(
            searchDepth=1, ClassName="LiveCaptionsDesktopWindow"
        )
        if not window.Exists(0.2):
            raise RuntimeError("未找到 Windows 实时字幕，请先按 Win + Ctrl + L 打开。")
        self.viewer = window.Control(
            searchDepth=5, AutomationId="CaptionsScrollViewer", ClassName="ScrollViewer"
        )
        if not self.viewer.Exists(0.5):
            raise RuntimeError("未找到字幕文本区域，请等待实时字幕完成初始化后重试。")

    def read(self) -> str:
        if self.viewer is None:
            raise RuntimeError("字幕读取尚未初始化。")
        if not self.viewer.Exists(0):
            raise RuntimeError("Windows 实时字幕已关闭，已保存当前录制内容。")
        return self.viewer.Name.strip()

    def close(self) -> None:
        # Release COM-backed controls before uninitializing this worker.
        self.viewer = None
        if self.context is not None:
            self.context.Uninitialize()
            self.context = None


def lc_detect() -> bool:
    """Detect the window, even when its text area is still initializing."""
    with auto.UIAutomationInitializerInThread():
        window = auto.GetRootControl().Control(
            searchDepth=1, ClassName="LiveCaptionsDesktopWindow"
        )
        return bool(window.Exists(0.2))


async def hook(filename: str, exit_event: asyncio.Event) -> None:
    session = CaptionSession(filename)
    reader = CaptionReader()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="captions-uia")
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(executor, reader.open)
        baseline = StartupBaseline(await loop.run_in_executor(executor, reader.read))
        while not exit_event.is_set():
            text = await loop.run_in_executor(executor, reader.read)
            await session.observe(baseline.filter(text))
            try:
                await asyncio.wait_for(exit_event.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
    finally:
        try:
            await session.finish()
        finally:
            try:
                await loop.run_in_executor(executor, reader.close)
            finally:
                executor.shutdown(wait=False)
