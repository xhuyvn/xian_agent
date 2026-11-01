"""Gate node: refuse without enough evidence, so no answer comes from model memory."""

from __future__ import annotations

from typing import Any

from xn.graph.deps import GraphDeps
from xn.schemas.state import QueryState

MIN_EVENTS = {"deep_dive": 3, "compare": 3, "timeline": 1, "factual_qa": 1}


def gate(state: QueryState, deps: GraphDeps) -> dict[str, Any]:
    """Require one resolved entity and the intent's minimum events (OQ-07, Sprint 1 rule)."""
    enough = bool(state["entity_ids"]) and (
        len(state["evidence"].items) >= MIN_EVENTS[state["decision"].intent]
    )
    if enough:
        return {}
    return {"refusal": f"Không đủ dữ liệu đến chương {state['chapter_max']}"}
