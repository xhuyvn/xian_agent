"""Evidence budget for the query graph: split long summaries and trim tiers to the cap.

Pure functions, so the trimming order can be tested without a database (G-28 D2, D4).
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Optional, Sequence

from xn.text import estimate_tokens

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class Unit:
    """Evidence that is kept or dropped as a whole, such as the pieces of one arc summary.

    fallback is a smaller form the unit shrinks to before it is dropped.
    """

    pieces: tuple[Any, ...]
    tokens: int
    fallback: Optional[Unit] = None


def make_unit(pieces: Sequence[Any], size: Callable[[Any], int], fallback: Optional[Unit] = None) -> Unit:
    """Build a unit whose tokens are the sum of its pieces' sizes."""
    return Unit(tuple(pieces), sum(size(p) for p in pieces), fallback)


def split_text(header: str, body: str, limit: int) -> list[str]:
    """Split body at sentence ends into texts that each start with header and fit limit tokens.

    A sentence longer than a whole piece is cut by characters. Every piece keeps the header,
    so each one still says which span it covers.
    """
    # Room for the longest part label too; ceil rounding of a join costs at most 1 token.
    room = limit - estimate_tokens(f"{header}(phần 99/99) ") - 1
    if room <= 0:
        raise ValueError(f"Header alone is over {limit} tokens")
    sentences: list[str] = []
    for sentence in _SENTENCE_END.split(body.strip()):
        while estimate_tokens(sentence) > room:
            cut = int(len(sentence) * room / estimate_tokens(sentence))
            sentences.append(sentence[:cut])
            sentence = sentence[cut:]
        if sentence:
            sentences.append(sentence)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        joined = f"{current} {sentence}" if current else sentence
        if estimate_tokens(joined) <= room:
            current = joined
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)
    if len(pieces) == 1:
        return [f"{header}{pieces[0]}"]
    return [f"{header}(phần {i}/{len(pieces)}) {p}" for i, p in enumerate(pieces, start=1)]


def fit(tiers: Sequence[Sequence[Unit]], budget: int) -> list[list[Unit]]:
    """Trim tiers until their tokens fit budget, and return the kept units of each tier.

    Tiers come in drop order, and each tier's units best first, so the last unit of the first
    tier goes first. Within a tier, units shrink to their fallback, worst first, before any
    unit of that tier is dropped. A later tier is touched only once every earlier one is empty.
    """
    kept = [list(tier) for tier in tiers]
    total = sum(u.tokens for tier in kept for u in tier)
    for tier in kept:
        for i in range(len(tier) - 1, -1, -1):
            if total <= budget:
                return kept
            fallback = tier[i].fallback
            if fallback is not None:
                total -= tier[i].tokens - fallback.tokens
                tier[i] = fallback
        while tier and total > budget:
            total -= tier.pop().tokens
    return kept


def pieces_of(units: Sequence[Unit]) -> list[Any]:
    """Flatten units into their pieces, in order."""
    return [piece for unit in units for piece in unit.pieces]
