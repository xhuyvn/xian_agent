"""Verify node: keep only citations backed by the evidence and within chapter N."""

from __future__ import annotations

import logging
import re
from typing import Any

from xn.graph.deps import GraphDeps
from xn.schemas.state import Answer, QueryState
from xn.text import normalize_text

logger = logging.getLogger(__name__)

_CITATION = re.compile(r"([ \t]*)\[\s*chương\s+(\d+(?:\s*,\s*\d+)*)\s*\]", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


def _strip_invalid(markdown: str, allowed: set[int]) -> str:
    """Rewrite each citation marker with its valid chapters, removing it when none remain."""

    def rewrite(match: re.Match[str]) -> str:
        chapters = [int(c) for c in re.findall(r"\d+", match.group(2))]
        kept = [str(c) for c in chapters if c in allowed]
        return f"{match.group(1)}[chương {', '.join(kept)}]" if kept else ""

    return _CITATION.sub(rewrite, markdown)


def _grounding_ratio(markdown: str) -> float:
    """Return the share of sentences that carry a citation."""
    sentences = [s for s in _SENTENCE_END.split(markdown) if s.strip()]
    if not sentences:
        return 0.0
    return sum(bool(_CITATION.search(s)) for s in sentences) / len(sentences)


def verify(state: QueryState, deps: GraphDeps) -> dict[str, Any]:
    """Strip citations that are not in the evidence or are above N, and log grounding."""
    chapter_max = state["chapter_max"]
    allowed = {i.chapter_id for i in state["evidence"].items if i.chapter_id <= chapter_max}
    answer = state["answer"]

    markdown = _strip_invalid(normalize_text(answer.markdown), allowed)
    inline = {int(c) for m in _CITATION.finditer(markdown) for c in re.findall(r"\d+", m.group(2))}
    citations = sorted(inline | (set(answer.citations) & allowed))
    ratio = _grounding_ratio(markdown)

    removed = set(answer.citations) - allowed
    if removed:
        logger.warning("Stripped citations outside the evidence: %s", sorted(removed))
    logger.info("Grounding ratio %.2f at chapter %d", ratio, chapter_max)
    return {"answer": Answer(markdown=markdown, citations=citations), "grounding_ratio": ratio}
