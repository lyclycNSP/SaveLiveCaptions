from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from function.save import TranscriptStore, caption_line


class StoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "captions.txt"
        self.path.touch()
        self.store = TranscriptStore(str(self.path))

    def tearDown(self) -> None:
        self.directory.cleanup()

    async def test_revise_entire_visible_draft_preserves_committed_prefix(self) -> None:
        first, old, context = (100, "第一句。"), (101, "原来的识别。"), (102, "上下文。")
        new = (101, "更正的识别。")
        await self.store.publish([], [first, old, context])
        await self.store.publish([first], [old, context])
        self.assertEqual(self.store.prefix_bytes, len(caption_line(first).encode("utf-8")))
        await self.store.publish([], [new, context])
        self.assertEqual(self.path.read_text(encoding="utf-8"),
                         "".join(map(caption_line, [first, new, context])))
        self.assertEqual(self.store.draft, "".join(map(caption_line, [new, context])).encode("utf-8"))

    async def test_failed_atomic_revision_preserves_file_and_metadata(self) -> None:
        await self.store.publish([], [(100, "Original."), (101, "Context.")])
        original = self.path.read_bytes()
        with patch("function.save.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                await self.store.publish([], [(100, "Corrected."), (101, "Context.")])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.store.draft, original)
        self.assertEqual(self.store.prefix_bytes, 0)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        await self.store.publish([], [(100, "Corrected."), (101, "Context.")])
        self.assertIn("Corrected.", self.path.read_text(encoding="utf-8"))

    async def test_append_failure_rolls_back_file_and_metadata(self) -> None:
        await self.store.publish([], [(100, "Original.")])
        original = self.path.read_bytes()
        with patch("function.save.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.store.publish([], [(100, "Original."), (101, "Next.")])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.store.draft, original)

    async def test_external_append_and_truncation_are_reported(self) -> None:
        await self.store.publish([(100, "Committed.")], [(101, "Draft.")])
        original = self.path.read_bytes()
        for external in (b"", original + b"external\n"):
            with self.subTest(external=external):
                self.path.write_bytes(external)
                with self.assertRaises(OSError):
                    await self.store.publish([], [(101, "Draft."), (102, "Next.")])
                self.assertEqual(self.path.read_bytes(), external)
        self.path.write_bytes(original)

    async def test_scrolling_without_content_change_does_not_rewrite_file(self) -> None:
        first, second = (100, "First."), (101, "Second.")
        await self.store.publish([], [first, second])
        with patch("function.save._replace_draft") as rewrite, \
                patch("function.save._append_draft") as append:
            await self.store.publish([first], [second])
        rewrite.assert_not_called()
        append.assert_not_called()
        self.assertEqual(self.store.draft, caption_line(second).encode("utf-8"))

    async def test_normal_new_sentences_append_without_copying_recording(self) -> None:
        await self.store.publish([], [(100, "First.")])
        with patch("function.save._replace_draft") as rewrite:
            await self.store.publish([], [(100, "First."), (101, "Second.")])
        rewrite.assert_not_called()
        self.assertEqual(len(self.path.read_text(encoding="utf-8").splitlines()), 2)
