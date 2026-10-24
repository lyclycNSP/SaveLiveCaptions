"""Thread-scoped power requirements, owned only while recording captions."""
import ctypes

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def _set_execution_state(flags: int) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    call = kernel32.SetThreadExecutionState
    call.argtypes = [ctypes.c_uint32]
    call.restype = ctypes.c_uint32
    previous = int(call(flags))
    if previous == 0:
        raise OSError(ctypes.get_last_error(), "无法设置录制期间的屏幕与系统电源请求。")
    return previous


class RecordingPowerGuard:
    def __init__(self) -> None:
        self.previous: int | None = None

    def acquire(self) -> None:
        if self.previous is None:
            self.previous = _set_execution_state(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            )

    def release(self) -> None:
        # Must run on the worker that acquired the per-thread request. Restore
        # its previous requirements rather than modifying the user's power plan.
        if self.previous is not None:
            _set_execution_state(self.previous | ES_CONTINUOUS)
            self.previous = None
