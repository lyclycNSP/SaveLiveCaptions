"""Open Windows Live Captions when needed; never toggle an existing window."""
import uiautomation as auto


def ensure_live_captions(timeout: float = 8.0) -> None:
    """Run on a worker thread, with COM initialization owned by that thread."""
    with auto.UIAutomationInitializerInThread():
        window = auto.GetRootControl().Control(
            searchDepth=1, ClassName="LiveCaptionsDesktopWindow"
        )
        try:
            if window.Exists(0.2):
                return
            auto.SendKeys("{Ctrl}{Win}l")
            if not window.Exists(timeout, 0.2):
                raise RuntimeError(
                    "未能自动打开 Windows 实时字幕。请按 Win + Ctrl + L 手动打开，"
                    "完成语言设置后再开始录制。"
                )
        finally:
            del window
