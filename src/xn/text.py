"""Text utilities for normalization and token estimation."""

from __future__ import annotations

import math
import re
import unicodedata

_ALIAS_STRIP_CHARS = " \t\n\r\f\v\"'“”‘’.,:;!?()[]"


def normalize_text(text: str) -> str:
    """Normalize text using Unicode Normalization Form C (NFC) only."""
    return unicodedata.normalize("NFC", text)


def estimate_tokens(text: str) -> int:
    """Estimate token count for text using math.ceil(len(text) / 1.6)."""
    return math.ceil(len(text) / 1.6)


def normalize_alias(name: str) -> str:
    """Normalize alias name: NFC, collapsed whitespace, stripped surrounding punctuation, casefolded."""
    text = normalize_text(name)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(_ALIAS_STRIP_CHARS)
    return text.casefold()


def strip_diacritics(text: str) -> str:
    """Remove Vietnamese tone and vowel marks, map đ/Đ to d/D, and return NFC text."""
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(
        "d" if c == "đ" else "D" if c == "Đ" else c
        for c in nfd
        if unicodedata.category(c) != "Mn"
    )
    return unicodedata.normalize("NFC", stripped)
