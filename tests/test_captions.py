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

    async def test_long_lived_tail_does_not_replay_saved_paragraph(self) -> None:
        await self.stable("First sentence. Second sentence. A long unfinished caption")
        await self.session.observe(
            "First sentence. Second sentence. A long unfinished caption continues.", now=110
        )
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "First sentence.", "Second sentence.", "A long unfinished caption continues."
        ])

    async def test_repeated_growth_over_many_polls_keeps_one_history(self) -> None:
        for step in range(20):
            tail = "A long evolving sentence" + " with more detail" * step
            if step == 19:
                tail += "."
            await self.stable("First sentence. Second sentence. " + tail, now=100 + step * 5)
        await self.session.finish()
        self.assertEqual(self.transcript(), ["First sentence.", "Second sentence.", tail])

    async def test_new_number_in_growing_tail_does_not_replay_history(self) -> None:
        await self.stable("Old context. We will discuss chapter")
        await self.session.observe(
            "Old context. We will discuss chapter 9.", now=101
        )
        await self.session.finish()
        self.assertEqual(self.transcript(), ["Old context.", "We will discuss chapter 9."])

    async def test_large_tail_extension_is_not_a_new_paragraph(self) -> None:
        await self.stable("Old context. A short partial caption")
        await self.session.observe(
            "Old context. A short partial caption now contains many additional details "
            "that were recognized between polls.", now=120
        )
        await self.session.finish()
        self.assertEqual(len(self.transcript()), 2)
        self.assertEqual(self.transcript()[0], "Old context.")
        self.assertTrue(self.transcript()[1].endswith("between polls."))

    async def test_arbitrary_tail_revision_does_not_replay_unchanged_head(self) -> None:
        await self.stable("First sentence. Second sentence. An original unfinished caption")
        await self.session.observe(
            "First sentence. Second sentence. A corrected final caption.", now=101
        )
        await self.session.finish()
        self.assertEqual(self.transcript().count("First sentence."), 1)
        self.assertEqual(self.transcript().count("Second sentence."), 1)
        self.assertIn("A corrected final caption.", self.transcript())

    async def test_interior_revision_does_not_replay_history(self) -> None:
        await self.stable("Opening sentence. The price is 10 dollars. Stable context. A growing tail")
        await self.stable(
            "Opening sentence. The price is 20 dollars. Stable context. A growing tail continues.",
            now=110,
        )
        await self.session.finish()
        self.assertEqual(self.transcript().count("Opening sentence."), 1)
        self.assertEqual(self.transcript().count("Stable context."), 1)
        self.assertNotIn("The price is 10 dollars.", self.transcript())
        self.assertIn("The price is 20 dollars.", self.transcript())

    async def test_revision_alternation_only_repeats_changed_sentence(self) -> None:
        for step in range(6):
            number = 10 if step % 2 else 20
            await self.stable(
                f"Opening sentence. The price is {number} dollars. Stable context. "
                f"The final sentence has detail {step}.", now=100 + step * 10,
            )
        await self.session.finish()
        self.assertEqual(self.transcript().count("Opening sentence."), 1)
        self.assertEqual(self.transcript().count("Stable context."), 1)
        self.assertEqual(len(self.transcript()), 4)
        self.assertIn("The price is 10 dollars.", self.transcript())

    async def test_clipped_first_sentence_does_not_replay_visible_history(self) -> None:
        await self.stable("A complete opening sentence. Stable context. Another sentence. A growing tail")
        await self.stable(
            "opening sentence. Stable context. Another sentence. A growing tail continues.",
            now=110,
        )
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "A complete opening sentence.", "Stable context.", "Another sentence.",
            "A growing tail continues.",
        ])

    async def test_partial_prefix_snapshot_does_not_discard_visible_history(self) -> None:
        await self.stable("Opening sentence. Stable context. Another sentence. A growing tail")
        await self.session.observe("Opening sentence. Stable", now=110)
        await self.stable(
            "Opening sentence. Stable context. Another sentence. A growing tail continues.",
            now=111,
        )
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "Opening sentence.", "Stable context.", "Another sentence.",
            "A growing tail continues.",
        ])

    async def test_changed_punctuation_updates_draft_without_keeping_old_segments(self) -> None:
        await self.stable("Opening sentence. 这是一句，中途断开。Stable context.")
        await self.stable("Opening sentence. 这是一句中途断开。Stable context.", now=110)
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "Opening sentence.", "这是一句中途断开。", "Stable context.",
        ])

    async def test_genuine_new_numeric_fact_inside_window_is_preserved(self) -> None:
        await self.stable("Opening sentence. The price is 10 dollars. Stable context.")
        await self.stable(
            "Opening sentence. The price is 10 dollars. The price is 20 dollars. Stable context.",
            now=110,
        )
        await self.session.finish()
        self.assertEqual(len(self.transcript()), 4)
        self.assertIn("The price is 10 dollars.", self.transcript())
        self.assertIn("The price is 20 dollars.", self.transcript())

    async def test_first_visible_sentence_revision_stays_in_draft(self) -> None:
        await self.stable("The price is 10 dollars. Stable context. Another sentence.")
        await self.stable("The price is 20 dollars. Stable context. Another sentence.", now=110)
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "The price is 20 dollars.", "Stable context.", "Another sentence.",
        ])

    async def test_one_incidental_anchor_does_not_drop_unrelated_speech(self) -> None:
        await self.stable("Earlier first fact. Shared phrase. Earlier last fact.")
        await self.stable("New first fact. Shared phrase. New last fact.", now=110)
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "Earlier first fact.", "Shared phrase.", "Earlier last fact.",
            "New first fact.", "Shared phrase.", "New last fact.",
        ])

    async def test_unrelated_new_window_keeps_its_own_timestamp(self) -> None:
        await self.stable("First unrelated window.", now=100)
        await self.stable("Second unrelated window.", now=200)
        await self.session.finish()
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[1].startswith(save.caption_line((200, "")).split("]")[0] + "]"))

    async def test_failed_revision_can_retry_without_losing_previous_window(self) -> None:
        await self.stable("Opening sentence. Original words. Stable context.")
        original = self.path.read_bytes()
        with patch("function.save.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                await self.session.observe("Opening sentence. Corrected words. Stable context.", 110)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.session.active[1].text, "Original words.")
        await self.stable("Opening sentence. Corrected words. Stable context.", now=111)
        await self.session.finish()
        self.assertEqual(self.transcript(), ["Opening sentence.", "Corrected words.", "Stable context."])

    async def test_scroll_and_late_tail_growth_keep_order(self) -> None:
        await self.stable("First sentence. Second sentence. An unfinished tail")
        await self.session.observe(
            "Second sentence. An unfinished tail extends here. Third sentence.", now=110
        )
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "First sentence.", "Second sentence.",
            "An unfinished tail extends here.", "Third sentence."
        ])

    async def test_changing_tail_isolated_from_rolling_history(self) -> None:
        await self.stable("First sentence. An unfinished tail")
        await self.session.observe("An unfinished tail extends here.", now=110)
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "First sentence.", "An unfinished tail extends here."
        ])

    async def test_unmatched_tail_cannot_overtake_unsaved_head(self) -> None:
        await self.session.observe("Hi. An original unfinished tail", now=100)
        await self.session.observe("Hi. A corrected final caption.", now=101)
        await self.session.finish()
        self.assertEqual(self.transcript(), [
            "Hi.", "A corrected final caption."
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

    async def test_failed_publish_keeps_draft_for_retry(self) -> None:
        await self.session.observe("This is a sentence.")
        with patch("function.save._append_draft", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await self.session.finish()
        self.assertEqual(self.session.store.draft, b"")
        self.assertEqual(self.session.active[0].text, "This is a sentence.")
        await self.session.finish()
        self.assertEqual(self.transcript(), ["This is a sentence."])

    def test_file_names_are_unique_and_dialog_uses_parent(self) -> None:
        with patch.object(save, "save_dir", self.directory.name):
            first, second = save.choose_save_dir(), save.choose_save_dir()
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
