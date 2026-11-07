"""L5 spoiler scan: find future names and realms in an answer, in code (§3.4 L5).

The synthesizer is told to use only the evidence, but a cloud model may know the novel.
This scan is the deterministic check behind that rule. A paraphrased spoiler that names
no future alias or realm is not caught (accepted risk R-01).
"""

from __future__ import annotations

import re
from typing import Any

from xn.schemas.state import EvidencePack
from xn.storage.repository import Repository
from xn.text import normalize_text, strip_diacritics

REDACTED = "[đã ẩn để tránh tiết lộ]"

# One-syllable aliases ("Hàn", "lão") match ordinary words too often to be scanned.
MIN_ALIAS_WORDS = 2

_SENTENCE = re.compile(r"((?<=[.!?])[ \t]+|\n+)")
_LINE_MARKER = re.compile(r"^(\s*(?:[-*+]|\d+[.)]|#+|>)\s+)")


def _words(text: str) -> str:
    """Fold text to space-separated words without case or diacritics, padded for matching."""
    folded = strip_diacritics(normalize_text(text)).casefold()
    return f" {' '.join(re.findall(r'\w+', folded))} "


def _contains(words: str, term: str) -> bool:
    """Return whether the folded term occurs in words on word bounds."""
    folded = _words(term)
    return folded.strip() != "" and folded in words


def realm_ranks(ladder: list[str], realms_seen: list[str]) -> int:
    """Return the highest ladder rank named in any realm text seen, or 0 for none.

    tier_or_realm is free text such as "Luyện Khí tầng 3", so a ladder name inside it counts.
    """
    seen = [_words(r) for r in realms_seen]
    best = 0
    for rank, name in enumerate(ladder, start=1):
        if any(_contains(text, name) for text in seen):
            best = rank
    return best


def evidence_words(evidence: EvidencePack | None) -> str:
    """Fold every text in the evidence pack, so a term found there is not called a leak."""
    if evidence is None:
        return " "
    parts = (
        [i.cloud_text() for i in evidence.items]
        + [c.text for c in evidence.chunks]
        + [s.text for s in evidence.summaries]
    )
    return _words("\n".join(parts))


def forbidden_terms(
    repo: Repository,
    *,
    novel_id: str,
    chapter_max: int,
    ladder: list[str],
    evidence: EvidencePack | None = None,
) -> list[str]:
    """List the names and realms an answer bounded at chapter_max must not contain.

    Aliases first seen after chapter_max, unless the same folded name is known by then;
    and ladder realms above the highest realm seen in events up to chapter_max. A term
    that appears in the evidence pack is allowed, since the evidence is all <= N.
    """
    known = {
        _words(row["alias_norm"])
        for row in repo.list_aliases(novel_id=novel_id, chapter_max=chapter_max)
    }
    allowed = evidence_words(evidence)
    terms = [
        alias
        for alias in repo.list_future_aliases(novel_id=novel_id, chapter_max=chapter_max)
        if len(_words(alias).split()) >= MIN_ALIAS_WORDS and _words(alias) not in known
    ]
    top = realm_ranks(ladder, repo.list_realms_seen(novel_id=novel_id, chapter_max=chapter_max))
    terms += ladder[top:]
    return [t for t in terms if not _contains(allowed, t)]


def find_leaks(text: str, terms: list[str]) -> list[str]:
    """Return the terms found in text, in the order given."""
    words = _words(text)
    return [t for t in terms if _contains(words, t)]


def redact(markdown: str, terms: list[str]) -> str:
    """Replace each sentence that names a term with the redaction marker.

    A list or heading marker at the start of a line is kept, so the Markdown stays valid.
    """
    parts = _SENTENCE.split(markdown)
    for i in range(0, len(parts), 2):
        sentence = parts[i]
        if sentence.strip() and find_leaks(sentence, terms):
            marker = _LINE_MARKER.match(sentence)
            parts[i] = (marker.group(1) if marker else "") + REDACTED
    return "".join(parts)


def realm_ladder(config: dict[str, Any]) -> list[str]:
    """Return the realm ladder of a novel config, lowest realm first."""
    return [r for r in config.get("realm_ladder") or [] if r]
