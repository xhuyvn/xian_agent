"""Bound node: resolve the spoiler bound N in code, never from the model alone."""

from __future__ import annotations

from typing import Any

from xn.graph.deps import GraphDeps, QueryError
from xn.schemas.state import QueryState


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
    if hint is not None and hint < 1:
        hint = None
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
