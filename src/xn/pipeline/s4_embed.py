"""Stage S4: paragraph chunks and micro-summaries, embedded into `xn_chunks`."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
import re
import textwrap

from xn.storage.bm25 import tokenize
from xn.text import CHARS_PER_TOKEN, estimate_tokens

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+")
_JOIN = "\n\n"


def _units(text: str, target: int) -> Iterator[str]:
    """Yield paragraphs, falling back to sentences, then words, for anything over target."""
    for paragraph in _PARAGRAPH_BREAK.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if estimate_tokens(paragraph) <= target:
            yield paragraph
            continue
        for sentence in _SENTENCE_BREAK.split(paragraph):
            if estimate_tokens(sentence) <= target:
                yield sentence
            else:
                yield from textwrap.wrap(sentence, width=int(target * CHARS_PER_TOKEN))


def _tail(text: str, overlap: int) -> str:
    """Return the last whole words of text that fit in the overlap."""
    tail = ""
    for word in reversed(text.split()):
        candidate = f"{word} {tail}".rstrip()
        if estimate_tokens(candidate) > overlap:
            break
        tail = candidate
    return tail


def split_chunks(text: str, *, target: int, overlap: int) -> list[str]:
    """Pack paragraphs into chunks of at most target tokens, each opening with the last one's tail."""
    chunks: list[str] = []
    current: list[str] = []
    for unit in _units(text, target):
        if current and estimate_tokens(_JOIN.join([*current, unit])) > target:
            chunks.append(_JOIN.join(current))
            tail = _tail(chunks[-1], overlap)
            current = [tail] if tail and estimate_tokens(tail + _JOIN + unit) <= target else []
        current.append(unit)
    if current:
        chunks.append(_JOIN.join(current))
    return chunks


class AliasIndex:
    """Whole-word alias lookup that sees only the aliases visible at a chapter."""

    def __init__(self, rows: Iterable[Mapping[str, object]]) -> None:
        self._aliases: dict[tuple[str, ...], list[tuple[int, int]]] = defaultdict(list)
        for row in rows:
            words = tuple(tokenize(str(row["alias_norm"])))
            if words:
                self._aliases[words].append((int(row["first_chapter"]), int(row["entity_id"])))
        self._lengths = sorted({len(words) for words in self._aliases})

    def entities_in(self, text: str, *, chapter_id: int) -> list[int]:
        """Return the sorted IDs of every entity with an alias in text, as of chapter_id."""
        words = tokenize(text)
        found: set[int] = set()
        for length in self._lengths:
            for start in range(len(words) - length + 1):
                for first_chapter, entity_id in self._aliases.get(
                    tuple(words[start : start + length]), ()
                ):
                    if first_chapter <= chapter_id:
                        found.add(entity_id)
        return sorted(found)
