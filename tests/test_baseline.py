from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from function.baseline import StartupBaseline
from function.capture import CaptionSession


class BaselineTests(unittest.TestCase):
    def test_unchanged_history_is_always_excluded(self) -> None:
        baseline = StartupBaseline("Old first. Old second.")
        for _ in range(5):
            self.assertEqual(baseline.filter("Old first. Old second."), "")

    def test_append_preserves_only_new_content_in_every_snapshot(self) -> None:
        baseline = StartupBaseline("Old sentence.")
        for _ in range(5):
            self.assertEqual(
                baseline.filter("Old sentence. New sentence."), "New sentence."
            )

    def test_rolling_suffix_of_history_is_excluded(self) -> None:
        baseline = StartupBaseline("First old sentence. Second old sentence.")
        self.assertEqual(
            baseline.filter("Second old sentence. New sentence."), "New sentence."
        )
        self.assertEqual(
            baseline.filter("Second old sentence. New sentence. Another sentence."),
            "New sentence. Another sentence.",
        )
        self.assertEqual(baseline.filter("New sentence. Another sentence."),
                         "New sentence. Another sentence.")

    def test_old_tail_extension_only_saves_appended_words(self) -> None:
        baseline = StartupBaseline("旧内容。这是一段尚未结束的")
        self.assertEqual(
            baseline.filter("旧内容。这是一段尚未结束的字幕。新的句子。"),
            "字幕。新的句子。",
        )
        self.assertEqual(
            baseline.filter("这是一段尚未结束的字幕。新的句子。"),
            "字幕。新的句子。",
        )

    def test_english_tail_extension(self) -> None:
        baseline = StartupBaseline("This was already on screen")
        self.assertEqual(
            baseline.filter("This was already on screen before the next sentence."),
            "before the next sentence.",
        )

    def test_empty_baseline_records_first_new_text(self) -> None:
        baseline = StartupBaseline("")
        self.assertEqual(baseline.filter("First new sentence."), "First new sentence.")

    def test_cleared_history_allows_identical_new_utterance(self) -> None:
        baseline = StartupBaseline("A repeated sentence.")
        self.assertEqual(baseline.filter(""), "")
        self.assertEqual(baseline.filter("A repeated sentence."), "A repeated sentence.")

    def test_history_not_blacklisted_after_scrolling_out(self) -> None:
        baseline = StartupBaseline("A repeated sentence.")
        baseline.filter("An unrelated new sentence.")
        self.assertEqual(baseline.filter("A repeated sentence."), "A repeated sentence.")

    def test_does_not_remove_shared_last_letter_from_new_word(self) -> None:
        baseline = StartupBaseline("Old captions")
        self.assertEqual(baseline.filter("something new"), "something new")

    def test_whitespace_changes_do_not_reintroduce_history(self) -> None:
        baseline = StartupBaseline("Old  sentence.\nStill old.")
        self.assertEqual(baseline.filter("Old sentence. Still old. New."), "New.")

    def test_partial_snapshot_does_not_restore_removed_history(self) -> None:
        baseline = StartupBaseline("Old first. Old second.")
        self.assertEqual(baseline.filter("Old first."), "")
        self.assertEqual(baseline.filter("Old first. New."), "New.")

    def test_full_history_after_partial_layout_remains_excluded(self) -> None:
        baseline = StartupBaseline("Old first. Old second.")
        self.assertEqual(baseline.filter("Old first."), "")
        self.assertEqual(baseline.filter("Old first. Old second. New."), "New.")


class BaselineSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_new_text_produces_empty_file_even_on_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "captions.txt"
            path.touch()
            session = CaptionSession(str(path))
            baseline = StartupBaseline("旧字幕。最后一句未结束")
            for _ in range(4):
                await session.observe(baseline.filter("旧字幕。最后一句未结束"))
            await session.finish()
            self.assertEqual(path.read_text(encoding="utf-8"), "")

    async def test_only_new_text_is_saved_and_flushed_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "captions.txt"
            path.touch()
            session = CaptionSession(str(path))
            baseline = StartupBaseline("第一段旧字幕。第二段旧字幕。")
            for _ in range(3):
                await session.observe(baseline.filter(
                    "第一段旧字幕。第二段旧字幕。第一段新字幕。"
                ))
            await session.observe(baseline.filter(
                "第二段旧字幕。第一段新字幕。新的未完成尾句"
            ))
            await session.finish()
            contents = path.read_text(encoding="utf-8")
            self.assertNotIn("旧字幕", contents)
            lines = [line.split("] ", 1)[1] for line in contents.splitlines()]
            self.assertEqual(lines, ["第一段新字幕。", "新的未完成尾句"])
