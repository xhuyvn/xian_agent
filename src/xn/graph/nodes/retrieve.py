"""Retrieve node: rank ledger events for the resolved entities under the evidence budget."""

from __future__ import annotations

from itertools import chain, zip_longest
import logging
from typing import Any

from xn.graph.deps import GraphDeps
from xn.llm import MAX_EVIDENCE_TOKENS, MAX_ITEM_TOKENS
from xn.schemas.state import EvidenceItem, EvidencePack, QueryState
from xn.text import estimate_tokens

logger = logging.getLogger(__name__)

EVENTS_PER_ENTITY = 25
PRIORITY_EVENT_TYPES = ("ACQUISITION", "UPGRADE", "DESTROYED", "TRANSFERRED", "REVEALED_IDENTITY")


def _rank_key(row: Any) -> tuple[int, int, int]:
    """Sort priority event types first, then by story order."""
    return (
        0 if row["event_type"] in PRIORITY_EVENT_TYPES else 1,
        row["chapter_id"],
        row["ordinal"],
    )


def _within_budget(items: list[EvidenceItem], budget: int) -> list[EvidenceItem]:
    """Keep items in rank order until the budget runs out, dropping the lowest ranked."""
    kept: list[EvidenceItem] = []
    spent = 0
    for item in items:
        tokens = estimate_tokens(item.cloud_text())
        if tokens > MAX_ITEM_TOKENS:
            logger.warning("Dropped event %d: %d tokens", item.event_id, tokens)
            continue
        if spent + tokens > budget:
            break
        kept.append(item)
        spent += tokens
    return kept


def _entity_items(entity_id: int, state: QueryState, deps: GraphDeps) -> list[EvidenceItem]:
    """Return an entity's top ranked events up to chapter N."""
    novel_id = state["novel_id"]
    chapter_max = state["chapter_max"]
    entity = deps.repo.get_entity(novel_id=novel_id, entity_id=entity_id, chapter_max=chapter_max)
    if entity is None:
        return []
    rows = deps.repo.get_entity_events(
        novel_id=novel_id, entity_id=entity_id, chapter_max=chapter_max
    )
    return [
        EvidenceItem(
            event_id=row["id"],
            chapter_id=row["chapter_id"],
            entity_id=entity_id,
            entity_name=entity["canonical_name"],
            event_type=row["event_type"],
            description=row["description"],
            tier_or_realm=row["tier_or_realm"],
            evidence=row["evidence"],
        )
        for row in sorted(rows, key=_rank_key)[:EVENTS_PER_ENTITY]
    ]


def retrieve(state: QueryState, deps: GraphDeps) -> dict[str, Any]:
    """Build the evidence pack; compare gives each entity half the budget."""
    entity_budget = MAX_EVIDENCE_TOKENS
    if state["decision"].intent == "compare":
        entity_budget = MAX_EVIDENCE_TOKENS // 2

    per_entity = [
        _within_budget(_entity_items(entity_id, state, deps), entity_budget)
        for entity_id in state["entity_ids"]
    ]
    # Taking turns between entities means the budget trims each one's lowest ranked events.
    merged = [i for i in chain.from_iterable(zip_longest(*per_entity)) if i is not None]
    items = _within_budget(merged, MAX_EVIDENCE_TOKENS)
    tokens = sum(estimate_tokens(item.cloud_text()) for item in items)
    return {"evidence": EvidencePack(items=items, tokens=tokens)}
