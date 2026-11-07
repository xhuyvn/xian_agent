"""Gate node: refuse without enough evidence, so no answer comes from model memory."""

from __future__ import annotations

from collections import Counter
from typing import Any

from xn.graph.deps import GraphDeps
from xn.schemas.state import QueryState

MIN_EVENTS = {"deep_dive": 3, "compare": 3, "timeline": 1}
MIN_RERANK_SCORE = 0.3


def _enough(state: QueryState, deps: GraphDeps) -> bool:
    """Apply the intent's evidence rule."""
    intent = state["decision"].intent
    evidence = state["evidence"]
    if intent == "factual_qa":
        # The reranker's top score decides; no entity is needed (G-17 D1).
        threshold = (deps.config.get("retrieval") or {}).get("min_rerank_score", MIN_RERANK_SCORE)
        return max((c.score for c in evidence.chunks), default=0.0) >= threshold
    if intent == "compare":
        # Two resolved entities, each with its own minimum of events (G-17 D2, OQ-07).
        counts = Counter(item.entity_id for item in evidence.items)
        return len(state["entity_ids"]) >= 2 and all(
            counts[entity_id] >= MIN_EVENTS["compare"] for entity_id in state["entity_ids"]
        )
    if intent == "timeline" and evidence.summaries:
        # Arc or prefix summaries answer a timeline question without a named entity (G-28).
        return True
    return bool(state["entity_ids"]) and len(evidence.items) >= MIN_EVENTS[intent]


def gate(state: QueryState, deps: GraphDeps) -> dict[str, Any]:
    """Refuse unless the evidence meets the intent's rule, so no cloud call is made."""
    if _enough(state, deps):
        return {}
    return {"refusal": f"Không đủ dữ liệu đến chương {state['chapter_max']}"}
