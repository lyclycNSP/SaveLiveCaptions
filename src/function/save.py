"""UTF-8 caption storage; writes finish before session state is committed."""
from pathlib import Path
import asyncio
import os
import tempfile
import time
from tkinter import filedialog
from typing import TYPE_CHECKING

import aiofiles

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


async def save_replace_txt(
    filename: str, old_caption: tuple[float, str], new_caption: tuple[float, str]
) -> None:
    await asyncio.to_thread(_replace_last_line, Path(filename), old_caption, new_caption)


def _replace_last_line(
    path: Path, old_caption: tuple[float, str], new_caption: tuple[float, str]
) -> None:
    # Stream to a sibling file and replace atomically. A failed rewrite leaves
    # the original transcript intact, including on Windows.
    temp_path: Path | None = None
    try:
        with path.open("r", encoding="utf-8") as source:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as target:
                temp_path = Path(target.name)
                previous = source.readline()
                for line in source:
                    target.write(previous)
                    previous = line
                if previous != caption_line(old_caption):
                    raise OSError("待更新的末行与字幕文件不一致；请勿在录制中修改该文件。")
                target.write(caption_line(new_caption))
                target.flush()
                os.fsync(target.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


async def save_txt(filename: str, new_caption: tuple[float, str]) -> None:
    async with aiofiles.open(filename, "a", encoding="utf-8") as stream:
        await stream.write(caption_line(new_caption))
