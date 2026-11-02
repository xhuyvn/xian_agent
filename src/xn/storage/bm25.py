"""BM25 sparse vectors for `xn_chunks`, shared by S4 and the retriever. Qdrant adds the IDF."""

from __future__ import annotations

from collections import Counter
from hashlib import blake2b
import re

from xn.text import normalize_text

K1 = 1.2
B = 0.75
# Measured on chapters 1-100 of pntt1 with ~450-token chunks.
AVG_DOC_WORDS = 286

_WORD_RE = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    """Split text into lowercase words, keeping diacritics."""
    return _WORD_RE.findall(normalize_text(text).casefold())


def term_id(word: str) -> int:
    """Return a stable 32-bit term ID, the same in every process."""
    return int.from_bytes(blake2b(word.encode("utf-8"), digest_size=4).digest(), "big")


def _term_counts(text: str) -> tuple[Counter[int], int]:
    words = tokenize(text)
    return Counter(term_id(word) for word in words), len(words)


def document_vector(text: str) -> tuple[list[int], list[float]]:
    """Return sorted term IDs and BM25 term weights for a stored chunk."""
    counts, length = _term_counts(text)
    norm = K1 * (1 - B + B * length / AVG_DOC_WORDS)
    indices = sorted(counts)
    return indices, [counts[i] * (K1 + 1) / (counts[i] + norm) for i in indices]


def query_vector(text: str) -> tuple[list[int], list[float]]:
    """Return sorted term IDs with weight 1 for a search query."""
    counts, _ = _term_counts(text)
    indices = sorted(counts)
    return indices, [1.0] * len(indices)
