"""Ordered, session-local state for rolling Live Captions snapshots."""
from dataclasses import dataclass
import time

from .config import MIN_LENGTH, SIMILARITY, STABLE_THRESHOLD
from .dedup import Deduplicator
from .save import save_replace_txt, save_txt
from .sentences import is_incomplete_sentence, split_into_sentences


@dataclass
class PendingCaption:
    text: str
    timestamp: float
    observations: int = 1
    saved: bool = False


class CaptionSession:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.active: list[PendingCaption] = []
        self.deduper = Deduplicator()

    async def _write(self, item: PendingCaption) -> None:
        if item.saved:
            return
        await save_txt(self.filename, (item.timestamp, item.text))
        item.saved = True

    async def observe(self, text: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        sentences = split_into_sentences(text)
        # Align the previous suffix with the new prefix. Repeated identical
        # sentences within a frame remain separate occurrences.
        overlap = 0
        revised: PendingCaption | None = None
        for size in range(min(len(self.active), len(sentences)), 0, -1):
            previous = self.active[-size:]
            same_prefix = [item.text for item in previous[:-1]] == sentences[:size - 1]
            old = previous[-1]
            new = sentences[size - 1]
            may_revise = (
                same_prefix
                and (not old.saved or now - old.timestamp <= 3.0)
                and self.deduper.is_better_version(new, old.text)
                and (not old.saved or self.deduper.similarity_ratio(new, old.text) >= SIMILARITY)
            )
            if same_prefix and (old.text == new or may_revise):
                overlap = size
                if may_revise:
                    revised = old
                break
        retained = self.active[-overlap:] if overlap else []
        departed = self.active[:-overlap] if overlap else self.active[:]

        # Only the changing final caption may be a revision, never another
        # sentence in the same snapshot or an old historical occurrence.
        for item in departed:
            await self._write(item)
        if revised is not None:
            new = sentences[overlap - 1]
            if revised.saved:
                await save_replace_txt(
                    self.filename, (revised.timestamp, revised.text),
                    (revised.timestamp, new),
                )
            revised.text = new
            revised.observations = 0
        for item in retained:
            item.observations += 1
        consumed = overlap
        self.active = retained + [PendingCaption(s, now) for s in sentences[consumed:]]
        # Commit only an ordered prefix; a stable later sentence must not
        # overtake an earlier unfinished caption.
        for item in self.active:
            if (item.observations < max(1, STABLE_THRESHOLD)
                    or (is_incomplete_sentence(item.text) and len(item.text) < MIN_LENGTH)):
                break
            await self._write(item)

    async def finish(self) -> None:
        for item in self.active:
            await self._write(item)
