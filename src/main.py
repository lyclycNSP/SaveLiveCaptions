"""Taskbar dashboard; Tk and asyncio share only the UI thread."""
import asyncio
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox

from function.save import choose_save_dir
from function.texthook import hook
from function.livecaptions import ensure_live_captions

BACKGROUND = "#F5F7FB"
INK = "#18243B"
MUTED = "#69758B"
BLUE = "#315EE8"


class Dashboard:
    def __init__(self, window: tk.Tk, loop: asyncio.AbstractEventLoop) -> None:
        self.window = window
        self.loop = loop
        self.exit_event = asyncio.Event()
        self.hook_task: asyncio.Task[bool] | None = None
        self.startup_task: asyncio.Task[None] | None = None
        self.close_task: asyncio.Task[None] | None = None
        self.closing = False
        self.stopping = False
        self.dialog_open = False
        self.closed = False
        self.poll_id: str | None = None
        window.title("Save Live Captions")
        resources = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        icon = resources / "assets" / "SaveLC.ico"
        if icon.is_file():
            window.iconbitmap(default=str(icon))
        # A decorated root has the native title bar and a taskbar entry.
        window.attributes("-topmost", True)
        window.configure(bg="#DCE2EE")
        window.protocol("WM_DELETE_WINDOW", self.request_close)

        self.panel = tk.Frame(window, bg=BACKGROUND, padx=16, pady=12)
        self.panel.pack(fill="both", expand=True, padx=1, pady=1)
        self.title = tk.Label(
            self.panel, text="字幕记录", bg=BACKGROUND, fg=INK,
            font=("Microsoft YaHei UI", 11, "bold"), anchor="w",
        )
        self.title.pack(fill="x")
        self.status = tk.Label(
            self.panel, text="● 正在打开系统实时字幕…", bg=BACKGROUND, fg=MUTED,
            font=("Microsoft YaHei UI", 9), anchor="w",
        )
        self.status.pack(fill="x", pady=(6, 10))

        buttons = tk.Frame(self.panel, bg=BACKGROUND)
        buttons.pack(fill="x")
        self.start_btn = tk.Button(
            buttons, text="●  开始录制", command=self.start_capture,
            bg=BLUE, fg="white", activebackground="#244BC4", activeforeground="white",
            disabledforeground="#BFCDF8", relief="flat", bd=0,
            font=("Microsoft YaHei UI", 10, "bold"), cursor="hand2", pady=8,
            state=tk.DISABLED,
        )
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.stop_btn = tk.Button(
            buttons, text="■  停止并保存", command=self.stop_capture,
            bg="#E6EBF4", fg=INK, activebackground="#D7DFED",
            relief="flat", bd=0, font=("Microsoft YaHei UI", 10),
            cursor="hand2", pady=8, state=tk.DISABLED,
        )
        self.stop_btn.pack(side="left", fill="x", expand=True)
        self.path_label = tk.Label(
            self.panel, text="保存后可继续下一段 · 最小化不影响录制", bg=BACKGROUND, fg=MUTED,
            font=("Microsoft YaHei UI", 8), anchor="w",
        )
        self.path_label.pack(fill="x", pady=(10, 0))
        window.update_idletasks()
        width = max(312, window.winfo_reqwidth())
        height = max(176, window.winfo_reqheight())
        window.minsize(width, height)
        window.geometry(f"{width}x{height}+32+64")
        self.poll_id = window.after(10, self.poll_loop)
        self.startup_task = loop.create_task(self.initialize_captions())

    async def initialize_captions(self) -> None:
        try:
            await asyncio.to_thread(ensure_live_captions)
        except Exception as error:
            if not self.closing:
                self.status.configure(text="● 请手动打开系统实时字幕", fg="#B44444")
                self.show_error("实时字幕未就绪", str(error))
        else:
            if not self.closing:
                self.status.configure(text="● 准备就绪", fg=MUTED)
        finally:
            self.startup_task = None
            if not self.closing:
                self.start_btn.configure(state=tk.NORMAL)

    def show_error(self, title: str, detail: str) -> None:
        self.dialog_open = True
        try:
            messagebox.showerror(title, detail, parent=self.window)
        finally:
            self.dialog_open = False

    def start_capture(self) -> None:
        if (self.closing or self.dialog_open or self.startup_task is not None
                or self.hook_task is not None):
            return
        self.start_btn.configure(state=tk.DISABLED)
        self.dialog_open = True
        try:
            filename = choose_save_dir(self.window)
        except OSError as error:
            self.show_error("无法创建字幕文件", str(error))
            self.start_btn.configure(state=tk.NORMAL)
            return
        finally:
            self.dialog_open = False
        self.exit_event.clear()
        self.stopping = False
        self.stop_btn.configure(state=tk.NORMAL)
        self.status.configure(text="● 正在录制", fg=BLUE)
        self.path_label.configure(text=Path(filename).name)
        self.hook_task = self.loop.create_task(self.capture(filename))

    async def capture(self, filename: str) -> bool:
        try:
            await hook(filename, self.exit_event)
        except Exception as error:
            # Background failures must be visible in a windowed executable.
            self.status.configure(text="● 录制或保存失败，请检查文件", fg="#B44444")
            self.show_error(
                "字幕录制或保存失败", f"{error}\n\n字幕文件：{filename}"
            )
            return False
        else:
            self.status.configure(text="● 已保存，可以开始下一段", fg=MUTED)
            return True
        finally:
            self.hook_task = None
            self.stopping = False
            if not self.closing:
                self.start_btn.configure(state=tk.NORMAL)
                self.stop_btn.configure(state=tk.DISABLED)

    def stop_capture(self) -> None:
        if self.closing or self.dialog_open or self.stopping or self.hook_task is None:
            return
        self.stopping = True
        self.exit_event.set()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.DISABLED)
        self.status.configure(text="● 正在保存…", fg=MUTED)

    def request_close(self) -> None:
        if self.closing or self.dialog_open:
            return
        if self.hook_task is not None:
            self.dialog_open = True
            try:
                confirmed = messagebox.askyesno(
                    "退出字幕记录",
                    "当前仍在录制或保存字幕。是否保存当前内容并退出？",
                    parent=self.window,
                )
            finally:
                self.dialog_open = False
            if not confirmed:
                return
        self.closing = True
        self.exit_event.set()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.DISABLED)
        self.status.configure(text="● 正在保存并退出…", fg=MUTED)
        self.close_task = self.loop.create_task(self.close(self.hook_task))

    async def close(self, capture_task: asyncio.Task[bool] | None) -> None:
        if self.startup_task is not None:
            await self.startup_task
        if capture_task is not None:
            succeeded = await capture_task
            if not succeeded:
                # The capture coroutine already displayed the failure. Keep the
                # dashboard open so a write error cannot silently close the app.
                self.closing = False
                self.start_btn.configure(state=tk.NORMAL)
                self.stop_btn.configure(state=tk.DISABLED)
                return
        if self.poll_id is not None:
            self.window.after_cancel(self.poll_id)
        self.closed = True
        self.window.destroy()

    def poll_loop(self) -> None:
        self.loop.call_soon(self.loop.stop)
        self.loop.run_forever()
        if not self.closed:
            self.poll_id = self.window.after(10, self.poll_loop)


def dashboard(loop: asyncio.AbstractEventLoop) -> None:
    window = tk.Tk()
    Dashboard(window, loop)
    window.mainloop()


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        dashboard(loop)
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
