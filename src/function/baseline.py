"""Exclude text already present in the first successful caption snapshot."""
import re


class StartupBaseline:
    def __init__(self, text: str) -> None:
        self.remaining = self._normalize(text)
        self.partial = ""

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def filter(self, text: str) -> str:
        """Keep only text after the surviving suffix of the startup snapshot.

        Track the baseline only while it remains on screen. Never blacklist old
        sentences globally, since a later occurrence is a new utterance.
        """
        current = self._normalize(text)
        if not self.remaining:
            return current
        if not current:
            self.remaining = ""
            self.partial = ""
            return ""
        if self.remaining.startswith(current):
            # UIA may temporarily expose only a prefix during a layout update.
            self.partial = current
            return ""
        overlap = self._overlap(self.remaining, current)
        if not overlap and self.partial and current.startswith(self.partial):
            overlap = len(self.partial)
        self.partial = ""
        if not overlap:
            self.remaining = ""
            return current
        self.remaining = current[:overlap]
        return current[overlap:].strip()

    @staticmethod
    def _overlap(previous: str, current: str) -> int:
        # KMP: longest suffix of previous matching a prefix of current, in
        # linear time even for a long caption history with repeated phrases.
        prefix = [0] * len(current)
        for i in range(1, len(current)):
            matched = prefix[i - 1]
            while matched and current[i] != current[matched]:
                matched = prefix[matched - 1]
            if current[i] == current[matched]:
                matched += 1
            prefix[i] = matched
        matched = 0
        for character in previous:
            while matched and (matched == len(current) or character != current[matched]):
                matched = prefix[matched - 1]
            if character == current[matched]:
                matched += 1
        # A shared final letter is not a retained English word. Chinese
        # captions can scroll at a character boundary, so keep that alignment.
        while matched:
            start = len(previous) - matched
            if (start == 0 or not previous[start - 1].isalnum()
                    or "\u4e00" <= previous[start] <= "\u9fff"):
                break
            matched = prefix[matched - 1]
        return matched
