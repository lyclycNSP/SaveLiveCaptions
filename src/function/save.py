"""UTF-8 caption storage; writes finish before session state is committed."""
from pathlib import Path
import asyncio
import io
import os
import tempfile
import time
from tkinter import filedialog
from typing import BinaryIO, TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk

save_dir = ""


def choose_save_dir(parent: "tk.Misc | None" = None) -> str:
    global save_dir
    if not save_dir:
        save_dir = filedialog.askdirectory(
            parent=parent, title="选择字幕保存文件夹", initialdir=str(Path.home())
        ) or str(Path.home() / "Documents" / "captions")
    directory = Path(save_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        save_dir = ""
        raise
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    # Reserve a unique file rather than append to a session from the same second.
    suffix = 0
    while True:
        name = f"{timestamp}_captions" + (f"_{suffix}" if suffix else "") + ".txt"
        path = directory / name
        try:
            with path.open("x", encoding="utf-8"):
                pass
            return str(path)
        except FileExistsError:
            suffix += 1
        except OSError:
            save_dir = ""
            raise


def caption_line(caption: tuple[float, str]) -> str:
    timestamp, text = caption
    return f"[{time.strftime('%H:%M:%S', time.localtime(timestamp))}] {text}\n"


class TranscriptStore:
    """An immutable file prefix plus an atomically replaceable visible draft."""
    def __init__(self, filename: str) -> None:
        self.path = Path(filename)
        self.prefix_bytes = 0
        self.draft = b""

    async def publish(
        self, committed: list[tuple[float, str]], draft: list[tuple[float, str]]
    ) -> None:
        committed_bytes = "".join(map(caption_line, committed)).encode("utf-8")
        draft_bytes = "".join(map(caption_line, draft)).encode("utf-8")
        replacement = committed_bytes + draft_bytes
        if replacement != self.draft:
            writer = _append_draft if replacement.startswith(self.draft) else _replace_draft
            await asyncio.to_thread(
                writer, self.path, self.prefix_bytes, self.draft, replacement
            )
        # Commit metadata only after the replacement succeeds. Retain only the
        # current visible draft in memory, never the whole recording.
        self.prefix_bytes += len(committed_bytes)
        self.draft = draft_bytes


def _append_draft(path: Path, prefix_bytes: int, expected: bytes, replacement: bytes) -> None:
    try:
        stream = path.open("r+b")
    except FileNotFoundError:
        if prefix_bytes or expected:
            raise
        stream = path.open("x+b")
    with stream:
        if os.fstat(stream.fileno()).st_size != prefix_bytes + len(expected):
            raise OSError("字幕文件长度与预期不符；请勿在录制中编辑输出文件。")
        stream.seek(prefix_bytes)
        if stream.read(len(expected) + 1) != expected or stream.tell() != prefix_bytes + len(expected):
            raise OSError("字幕文件与预期不符；请勿在录制中编辑输出文件。")
        old_size = stream.tell()
        try:
            stream.write(replacement[len(expected):])
            stream.flush()
            os.fsync(stream.fileno())
        except OSError:
            stream.seek(old_size)
            stream.truncate()
            stream.flush()
            raise


def _replace_draft(path: Path, prefix_bytes: int, expected: bytes, replacement: bytes) -> None:
    temp_path: Path | None = None
    source: BinaryIO
    try:
        try:
            source = path.open("rb")
        except FileNotFoundError:
            if prefix_bytes or expected:
                raise
            source = io.BytesIO()
        with source:
            with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as target:
                temp_path = Path(target.name)
                remaining = prefix_bytes
                while remaining:
                    chunk = source.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise OSError("字幕文件的已确认正文被修改；请勿在录制中编辑输出文件。")
                    target.write(chunk)
                    remaining -= len(chunk)
                if source.read(len(expected) + 1) != expected:
                    raise OSError("字幕文件的草稿与预期不符；请勿在录制中编辑输出文件。")
                target.write(replacement)
                target.flush()
                os.fsync(target.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
