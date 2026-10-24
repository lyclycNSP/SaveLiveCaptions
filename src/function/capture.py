"""Ordered, session-local state for rolling Live Captions snapshots."""
from dataclasses import dataclass
from bisect import bisect_left
from collections import Counter
import time

from .config import MIN_LENGTH, SIMILARITY, STABLE_THRESHOLD
from .dedup import Deduplicator
from .save import TranscriptStore
from .sentences import is_incomplete_sentence, split_into_sentences


def _sentence_overlap(previous: list[str], current: list[str]) -> int:
    """Longest previous suffix matching a current prefix, in linear time."""
    if not previous or not current:
        return 0
    prefix = [0] * len(current)
    for i in range(1, len(current)):
        matched = prefix[i - 1]
        while matched and current[i] != current[matched]:
            matched = prefix[matched - 1]
        if current[i] == current[matched]:
            matched += 1
        prefix[i] = matched
    matched = 0
    for sentence in previous:
        while matched and (matched == len(current) or sentence != current[matched]):
            matched = prefix[matched - 1]
        if sentence == current[matched]:
            matched += 1
    return matched


def _sentence_matches(previous: list[str], current: list[str]) -> dict[int, int]:
    """Align unchanged occurrences around edits, without a transcript blacklist.

    Unique sentences anchor an increasing sequence of old positions. Match
    equal gap boundaries too, so repeated short utterances remain occurrences.
    Work is O(n log n), including histories containing many identical lines.
    """
    old_counts, new_counts = Counter(previous), Counter(current)
    positions = {text: i for i, text in enumerate(previous) if old_counts[text] == 1}
    candidates = [(positions[text], j) for j, text in enumerate(current)
                  if new_counts[text] == 1 and text in positions]
    ends: list[int] = []
    end_nodes: list[int] = []
    parents: list[int] = []
    for node, (old, _) in enumerate(candidates):
        slot = bisect_left(ends, old)
        parents.append(end_nodes[slot - 1] if slot else -1)
        if slot == len(ends):
            ends.append(old)
            end_nodes.append(node)
        else:
            ends[slot] = old
            end_nodes[slot] = node
    anchors: list[tuple[int, int]] = []
    node = end_nodes[-1] if end_nodes else -1
    while node >= 0:
        anchors.append(candidates[node])
        node = parents[node]
    anchors.reverse()
    matches: dict[int, int] = {}
    old_start = new_start = 0
    for old_end, new_end in anchors + [(len(previous), len(current))]:
        left_old, left_new = old_start, new_start
        while (left_old < old_end and left_new < new_end
               and previous[left_old] == current[left_new]):
            matches[left_new] = left_old
            left_old += 1
            left_new += 1
        right_old, right_new = old_end - 1, new_end - 1
        while (right_old >= left_old and right_new >= left_new
               and previous[right_old] == current[right_new]):
            matches[right_new] = right_old
            right_old -= 1
            right_new -= 1
        if old_end < len(previous):
            matches[new_end] = old_end
        old_start, new_start = old_end + 1, new_end + 1
    # Scrolling may cut inside the first visible sentence. An exact following
    # anchor identifies its occurrence; retain the full text already observed.
    if 0 not in matches and 1 in matches and matches[1] > 0:
        old = matches[1] - 1
        if previous[old].endswith(current[0]):
            matches[0] = old
    return matches


@dataclass
class PendingCaption:
    text: str
    timestamp: float
    observations: int = 1


class CaptionSession:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.active: list[PendingCaption] = []
        self.deduper = Deduplicator()
        self.store = TranscriptStore(filename)

    def _extends_tail(self, old: str, new: str) -> bool:
        if len(new) <= len(old):
            return False
        # Exact growth is the same visible occurrence, even after many seconds
        # or when new numbers arrive. Changed existing numbers never match here.
        if new.startswith(old):
            return True
        return (self.deduper.is_better_version(new, old)
                and self.deduper.similarity_ratio(new, old) >= SIMILARITY)

    async def observe(self, text: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        sentences = split_into_sentences(text)
        previous = [item.text for item in self.active]
        if (sentences and len(sentences) < len(previous)
                and sentences[:-1] == previous[:len(sentences) - 1]
                and previous[len(sentences) - 1].startswith(sentences[-1])):
            # A temporary prefix during layout is not evidence that the rest
            # scrolled away. Keep the draft until a full update arrives.
            return
        overlap = _sentence_overlap(previous, sentences)
        head_overlap = _sentence_overlap(previous[:-1], sentences)
        if head_overlap > overlap:
            matches = {j: len(previous) - head_overlap - 1 + j
                       for j in range(head_overlap)}
        else:
            matches = {j: len(previous) - overlap + j for j in range(overlap)}
        aligned = _sentence_matches(previous, sentences)
        # One incidental phrase inside otherwise unrelated windows cannot
        # justify dropping the old draft as a recognition correction.
        if len(aligned) >= 2 and len(aligned) > len(matches):
            matches = aligned

        tail_position: int | None = None
        if self.active and len(previous) - 1 not in matches.values():
            candidate = max(matches, default=-1) + 1
            if (candidate < len(sentences)
                    and self._extends_tail(previous[-1], sentences[candidate])):
                tail_position = candidate
                matches[candidate] = len(previous) - 1

        # Only the old prefix before the retained window has scrolled away.
        # Unmatched text inside/after that window is a revision, not new speech.
        first_retained = min(matches.values(), default=len(previous))
        if matches and min(matches) > 0:
            # New leading text before an exact anchor can revise the preceding
            # visible sentence (including a clipped first line). Keep that
            # boundary in the draft instead of committing its obsolete version.
            first_retained = max(0, first_retained - 1)
        committed = self.active[:first_retained]
        active: list[PendingCaption] = []
        ordered_matches = sorted(matches.items())
        anchor = 0
        old_start = first_retained
        for j, sentence in enumerate(sentences):
            if j in matches:
                old = self.active[matches[j]]
                if j == tail_position:
                    active.append(PendingCaption(sentence, old.timestamp))
                else:
                    active.append(PendingCaption(old.text, old.timestamp, old.observations + 1))
                old_start = matches[j] + 1
                anchor += 1
            else:
                next_old = (ordered_matches[anchor][1] if anchor < len(ordered_matches)
                            else len(previous))
                # A rewritten span inherits its occurrence time, including
                # punctuation resegmentation and changes to recognized numbers.
                timestamp = (self.active[old_start].timestamp
                             if matches and old_start < next_old else now)
                active.append(PendingCaption(sentence, timestamp))
        draft: list[tuple[float, str]] = []
        for item in active:
            if (item.observations < max(1, STABLE_THRESHOLD)
                    or (is_incomplete_sentence(item.text) and len(item.text) < MIN_LENGTH)):
                break
            draft.append((item.timestamp, item.text))
        await self.store.publish([(item.timestamp, item.text) for item in committed], draft)
        self.active = active

    async def finish(self) -> None:
        await self.store.publish([], [(item.timestamp, item.text) for item in self.active])
