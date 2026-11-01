"""Bound node: resolve the spoiler bound N in code, never from the model alone."""

from __future__ import annotations

import re
from typing import Any

from xn.graph.deps import GraphDeps, QueryError
from xn.schemas.state import QueryState
from xn.text import strip_diacritics

# Patterns run on casefolded text without diacritics, so "den chuong 300" also matches.
_CHAPTER = r"(?:chuong|chapter|chap|ch|c)\.?\s*"
_INCLUSIVE_BOUNDS = [
    re.compile(p)
    for p in (
        rf"\b(?:den|toi|het|khong qua|chua qua)(?:\s+(?:het|cuoi|tan))*\s+{_CHAPTER}(\d+)\b",
        rf"\b{_CHAPTER}(\d+)\s+(?:tro ve truoc|tro xuong|ve truoc|do lai)\b",
        r"\b(\d+)\s+(?:chuong|chapter|chap)\s+dau\b",
        rf"\b{_CHAPTER}\d+\s*(?:den|toi|-|–)\s*(?:{_CHAPTER})?(\d+)\b",
    )
]
_BEFORE_BOUND = re.compile(rf"\btruoc\s+{_CHAPTER}(\d+)\b")


def _question_bound(query: str) -> int | None:
    """Read a chapter bound in code, because the model can miss it and leak past the bound.

    Several bounds in one question resolve to the smallest, and "trước chương N" stops at
    N - 1. A chapter that is only named, as in "chương 30 có gì?", is not a bound.
    """
    text = strip_diacritics(query).casefold()
    bounds = [int(m) for pattern in _INCLUSIVE_BOUNDS for m in pattern.findall(text)]
    bounds += [max(int(m) - 1, 1) for m in _BEFORE_BOUND.findall(text) if int(m) >= 1]
    bounds = [b for b in bounds if b >= 1]
    return min(bounds) if bounds else None


def bound(state: QueryState, deps: GraphDeps) -> dict[str, Any]:
    """Set chapter_max from §3.3, or refuse a follow-up that asks for a different N.

    The first turn of a thread pins N in the checkpoint. A later turn inherits it, and a
    different N is refused, because a thread must not reuse answers across bounds (§3.4 L6).

    Raises:
        QueryError: With NOVEL_NOT_FOUND, or VALIDATION_FAILED for a bound below 1.
    """
    novel = deps.repo.get_novel(novel_id=state["novel_id"])
    if novel is None:
        raise QueryError("NOVEL_NOT_FOUND", f"Novel not found: {state['novel_id']}")
    total = novel["total_chapters"]

    until = state.get("until")
    if until is not None and until < 1:
        raise QueryError("VALIDATION_FAILED", f"Chapter bound must be at least 1: {until}")
    hint = state["decision"].chapter_hint
    if hint is None or hint < 1:
        hint = _question_bound(state.get("query", ""))
    requested = until or hint

    pinned = state.get("chapter_max")
    if pinned is not None:
        if requested is not None and min(requested, total) != pinned:
            return {
                "refusal": (
                    f"Cuộc trò chuyện này đã cố định ở chương {pinned}. "
                    "Hãy bắt đầu một cuộc trò chuyện mới để đổi phạm vi."
                )
            }
        return {"chapter_max": pinned}

    return {"chapter_max": min(requested or novel["reader_progress"] or total, total)}
