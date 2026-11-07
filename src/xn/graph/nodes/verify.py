"""Verify node: keep only citations backed by the evidence and within chapter N."""

from __future__ import annotations

import logging
import re
from typing import Any

from xn.graph.deps import GraphDeps
from xn.graph.nodes.spoiler_scan import find_leaks, forbidden_terms, realm_ladder, redact
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
    """Strip citations that are not in the evidence or are above N, scan for spoilers, log grounding.

    A leak of a future name or realm (§3.4 L5) sends the answer back to synthesize once,
    with the leaked terms forbidden. If the retry still leaks, each leaking sentence is
    replaced with the redaction marker.
    """
    chapter_max = state["chapter_max"]
    evidence = state["evidence"]
    chapters = (
        {i.chapter_id for i in evidence.items}
        | {c.chapter_id for c in evidence.chunks}
        | {s.chapter_id for s in evidence.summaries}
    )
    allowed = {c for c in chapters if c <= chapter_max}
    answer = state["answer"]

    markdown = _strip_invalid(normalize_text(answer.markdown), allowed)
    terms = forbidden_terms(
        deps.repo,
        novel_id=state["novel_id"],
        chapter_max=chapter_max,
        ladder=realm_ladder(deps.config),
        evidence=evidence,
    )
    leaks = find_leaks(markdown, terms)
    retries = state.get("spoiler_retries") or 0
    if leaks and retries < 1:
        logger.warning("Spoiler scan hit %s at chapter %d; regenerating", leaks, chapter_max)
        return {"forbidden": leaks, "spoiler_retries": retries + 1, "regenerate": True}
    if leaks:
        logger.warning("Spoiler scan hit %s again at chapter %d; redacting", leaks, chapter_max)
        markdown = redact(markdown, leaks)

    inline = {int(c) for m in _CITATION.finditer(markdown) for c in re.findall(r"\d+", m.group(2))}
    # A redacted sentence takes its citations with it, so only the text's own markers count.
    listed = set() if leaks else set(answer.citations) & allowed
    citations = sorted(inline | listed)
    ratio = _grounding_ratio(markdown)

    removed = set(answer.citations) - allowed
    if removed:
        logger.warning("Stripped citations outside the evidence: %s", sorted(removed))
    logger.info("Grounding ratio %.2f at chapter %d", ratio, chapter_max)
    return {
        "answer": Answer(markdown=markdown, citations=citations),
        "grounding_ratio": ratio,
        "regenerate": False,
    }
