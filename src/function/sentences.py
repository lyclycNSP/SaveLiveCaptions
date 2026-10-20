"""Sentence boundaries shared by live capture and final flushing."""
import re

# Protect dots inside addresses, versions and common English abbreviations.
_PROTECTED = re.compile(
    r"[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}"
    r"|https?://[^\s，。！？；<>]+"
    r"|\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s，。！？；<>]*)?"
    r"|\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|etc)\."
    r"|\b(?:[A-Za-z]\.){2,}", re.IGNORECASE
)


def split_into_sentences(text: str) -> list[str]:
    """Keep short utterances and original text; inspect the full lookahead."""
    text = re.sub(r"\s+", " ", text).strip()
    protected: set[int] = set()
    for match in _PROTECTED.finditer(text):
        end = match.end()
        # A URL's trailing sentence punctuation is outside the address.
        while end > match.start() and text[end - 1] in ".!?;":
            if not match.group().lower().startswith("http") and "/" not in match.group():
                break
            end -= 1
        protected.update(range(match.start(), end))

    sentences: list[str] = []
    start = 0
    chinese = False
    for i, char in enumerate(text):
        chinese = chinese or "\u4e00" <= char <= "\u9fff"
        if i in protected:
            continue
        boundary = char in "。！？!?；;" or (char == "，" and chinese)
        if char == ".":
            previous = text[i - 1:i]
            following = text[i + 1:i + 2]
            boundary = not (previous.isdigit() and following.isdigit())
        if boundary:
            part = text[start:i + 1].strip()
            if any(c.isalnum() for c in part):
                sentences.append(part)
            start = i + 1
            chinese = False
    tail = text[start:].strip()
    if any(c.isalnum() for c in tail):
        sentences.append(tail)
    return sentences


def is_incomplete_sentence(sentence: str) -> bool:
    return not sentence.rstrip().endswith((".", "。", "!", "！", "?", "？", ";", "；", "，"))
