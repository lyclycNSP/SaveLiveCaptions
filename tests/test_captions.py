import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from function.capture import CaptionSession
from function.dedup import Deduplicator
from function import save
from function.sentences import is_incomplete_sentence, split_into_sentences
from function.transformation import word_to_number


class ParsingTests(unittest.TestCase):
    def test_decimal_versions_and_dates(self) -> None:
        cases = {
            "The value is 3.14 today.": ["The value is 3.14 today."],
            "Version 2.0 is out. It costs 9.99 euros.": [
                "Version 2.0 is out.", "It costs 9.99 euros."
            ],
            "今天是2026.10.20，价格为3.14元。": ["今天是2026.10.20，", "价格为3.14元。"],
            "Yes. No! 好。对！": ["Yes.", "No!", "好。", "对！"],
            "Contact support@example.com": ["Contact support@example.com"],
            "Visit https://example.com/docs. Next sentence.": [
                "Visit https://example.com/docs.", "Next sentence."
            ],
            "Visit economist.com": ["Visit economist.com"],
            "Dr. Smith is here.": ["Dr. Smith is here."],
            "The U.S. market is open.": ["The U.S. market is open."],
            "   \n  ": [],
            "....": [],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(split_into_sentences(text), expected)

    def test_chinese_sentence_endings(self) -> None:
        for text in ("这是完整的句子。", "对！", "你好，", "Yes."):
            self.assertFalse(is_incomplete_sentence(text))
        self.assertTrue(is_incomplete_sentence("unfinished tail"))

    def test_number_normalization(self) -> None:
        cases = {
            "twenty six": "26", "twenty twenty six": "2026",
            "nineteen eighty": "1980", "twenty oh eight": "2008",
            "two thousand": "2000", "three hundred and five": "305",
            "the cat sat on a mat": "the cat sat on a mat",
            "often someone": "often someone",
            "i have seven apples": "i have 7 apples",
            "twenty first": "21st", "one two three": "1 2 3",
            "five and six": "5 and 6",
            "a hundred": "100", "one second": "1 second",
            "one million two hundred thousand": "1200000",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(word_to_number(text), expected)

    def test_near_identical_numeric_facts_are_distinct(self) -> None:
        self.assertFalse(Deduplicator().is_better_version(
            "The price is 20 dollars today.", "The price is 10 dollars."
        ))


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "captions.txt"
        self.path.touch()
        self.session = CaptionSession(str(self.path))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def transcript(self) -> list[str]:
        return [line.split("] ", 1)[1] for line in self.path.read_text(
            encoding="utf-8"
        ).splitlines()]

    async def stable(self, text: str, now: float = 100) -> None:
        for i in range(3):
            await self.session.observe(text, now + i * 0.25)

    async def test_order_short_sentences_and_identical_occurrences(self) -> None:
        await self.stable("Yes. No! 好。好。")
        await self.session.finish()
        self.assertEqual(self.transcript(), ["Yes.", "No!", "好。", "好。"])

    async def test_unchanged_snapshot_not_written_twice(self) -> None:
        await self.stable("A complete sentence.")
        await self.stable("A complete sentence.")
        await self.session.finish()
        self.assertEqual(self.transcript(), ["A complete sentence."])

    async def test_repeated_speech_after_leaving_screen_is_kept(self) -> None:
        for text in ("The same repeated sentence.", "An intervening sentence.",
                     "The same repeated sentence."):
            await self.stable(text)
        await self.session.finish()
        self.assertEqual(len(self.transcript()), 3)
        self.assertEqual(self.transcript()[0], self.transcript()[2])

    async def test_rolling_snapshot_overlap(self) -> None:
        await self.stable("First sentence. Second sentence.")
        await self.stable("Second sentence. Third sentence.")
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "First sentence.", "Second sentence.", "Third sentence."
        ])

    async def test_unstable_tail_saved_on_stop(self) -> None:
        await self.session.observe("Value 3.14 and support@example.com")
        await self.session.finish()
        self.assertEqual(self.transcript(), ["Value 3.14 and support@example.com"])

    async def test_departing_short_caption_is_saved(self) -> None:
        await self.session.observe("Hi")
        await self.session.observe("Next")
        await self.session.finish()
        self.assertEqual(self.transcript(), ["Hi", "Next"])

    async def test_earlier_unfinished_caption_cannot_be_overtaken(self) -> None:
        await self.stable("Hi")
        await self.session.observe("Hi。Next sentence.")
        await self.session.finish()
        self.assertEqual(self.transcript(), ["Hi。", "Next sentence."])

    async def test_pending_revision_in_multi_sentence_snapshot(self) -> None:
        await self.session.observe("First sentence. This is an unfinished")
        await self.session.observe("First sentence. This is an unfinished caption.")
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "First sentence.", "This is an unfinished caption."
        ])

    async def test_saved_tail_extension_replaces_exact_line(self) -> None:
        await self.stable("First sentence. The caption is being recorded")
        await self.stable("The caption is being recorded now.", now=101)
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "First sentence.", "The caption is being recorded now."
        ])

    async def test_separate_sessions_have_no_shared_state(self) -> None:
        await self.stable("A complete sentence.")
        other_path = Path(self.directory.name) / "other.txt"
        other_path.touch()
        other = CaptionSession(str(other_path))
        await other.observe("A complete sentence.")
        await other.finish()
        self.assertEqual(len(other_path.read_text(encoding="utf-8").splitlines()), 1)

    async def test_numeric_change_is_not_discarded(self) -> None:
        await self.stable("The price is 10 dollars.")
        await self.stable("The price is 20 dollars.")
        await self.session.finish()
        self.assertEqual(len(self.transcript()), 2)

    async def test_failed_append_does_not_mark_as_saved(self) -> None:
        await self.session.observe("This is a sentence.")
        with patch("function.capture.save_txt", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.session.finish()
        self.assertFalse(self.session.active[0].saved)

    async def test_failed_replace_preserves_original(self) -> None:
        await save.save_txt(str(self.path), (100, "Original."))
        with patch("function.save.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                await save.save_replace_txt(
                    str(self.path), (100, "Original."), (100, "Updated.")
                )
        self.assertEqual(self.transcript(), ["Original."])
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])

    async def test_replace_requires_exact_last_line(self) -> None:
        await save.save_txt(str(self.path), (100, "Prefix original."))
        with self.assertRaises(OSError):
            await save.save_replace_txt(str(self.path), (100, "original."), (100, "New."))
        self.assertEqual(self.transcript(), ["Prefix original."])

    def test_file_names_are_unique_and_dialog_uses_parent(self) -> None:
        with patch.object(save, "save_dir", self.directory.name):
            first, second = save.choose_save_dir(), save.choose_save_dir()
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
