"""Conservative comparison of revisions; never deduplicate an entire transcript."""
import re
from difflib import SequenceMatcher

from .transformation import word_to_number


class Deduplicator:
    def normalize_sentence(self, sentence: str) -> str:
        text = re.sub(r"\s+", " ", sentence.strip()).lower()
        return re.sub(r"\s+([.,!?])", r"\1", word_to_number(text))

    def similarity_ratio(self, first: str, second: str) -> float:
        return SequenceMatcher(
            None, self.normalize_sentence(first), self.normalize_sentence(second)
        ).ratio()

    def is_better_version(self, new_sentence: str, old_sentence: str) -> bool:
        new = new_sentence.strip()
        old = old_sentence.strip()
        if new == old:
            return False
        # Different numbers can be distinct facts, even in near-identical text.
        if re.findall(r"\d+(?:\.\d+)*", new) != re.findall(r"\d+(?:\.\d+)*", old):
            return False
        prefix = old.rstrip(".。!?！？")
        unfinished = old[-1:] not in ".。!?！？"
        return (bool(prefix) and (len(prefix) >= 4 or unfinished)
                and new.startswith(prefix) and len(new) > len(old))




