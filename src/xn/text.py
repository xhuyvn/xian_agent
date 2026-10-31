"""Text utilities for normalization and token estimation."""

from __future__ import annotations

import math
import unicodedata


def normalize_text(text: str) -> str:
    """Normalize text using Unicode Normalization Form C (NFC) only."""
    return unicodedata.normalize("NFC", text)


def estimate_tokens(text: str) -> int:
    """Estimate token count for text using math.ceil(len(text) / 1.6)."""
    return math.ceil(len(text) / 1.6)
