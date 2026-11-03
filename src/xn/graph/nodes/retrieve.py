"""Retrieve node: ledger events for the resolved entities, plus hybrid chunks.

factual_qa searches every chunk; compare searches each entity's own chunks.
"""

from __future__ import annotations

from itertools import chain, zip_longest
import logging
from typing import Any

from xn.graph.deps import GraphDeps
from xn.llm import MAX_CHUNKS, MAX_EVIDENCE_TOKENS, MAX_ITEM_TOKENS
from xn.schemas.state import ChunkEvidence, EvidenceItem, EvidencePack, QueryState
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


def _search(state: QueryState, deps: GraphDeps) -> list[ChunkEvidence]:
    """Run the hybrid search the intent needs, best chunks first."""
    retriever = deps.retriever()
    bound = {"novel_id": state["novel_id"], "chapter_max": state["chapter_max"], "query": state["query"]}
    if state["decision"].intent == "factual_qa":
        chunks = retriever.retrieve(**bound)
        logger.info("Hybrid retrieval timings: %s", retriever.last_timings)
        return chunks

    # compare: each entity gets an equal share of the chunk limit, from chunks tagged with it.
    entity_ids = state["entity_ids"]
    per_entity = MAX_CHUNKS // len(entity_ids) if entity_ids else 0
    found: list[list[ChunkEvidence]] = []
    for entity_id in entity_ids:
        if per_entity == 0:
            break
        found.append(retriever.retrieve(**bound, entity_id=entity_id, top_k=per_entity))
        logger.info("Hybrid retrieval timings for entity %d: %s", entity_id, retriever.last_timings)
    # A chunk tagged with both entities is kept once, under the entity that found it first.
    seen: set[tuple[int, str, int]] = set()
    chunks = []
    for chunk in chain.from_iterable(zip_longest(*found)):
        if chunk is None:
            continue
        key = (chunk.chapter_id, chunk.kind, chunk.chunk_no)
        if key not in seen:
            seen.add(key)
            chunks.append(chunk)
    return chunks


def _chunks(state: QueryState, deps: GraphDeps) -> tuple[list[ChunkEvidence], list[str]]:
    """Return reranked chunks within the budget; a failure gives none and an error (D5)."""
    try:
        chunks = _search(state, deps)
    except Exception as err:
        logger.warning("Hybrid retrieval failed: %s", err)
        return [], [f"retrieve: {err}"]
    kept: list[ChunkEvidence] = []
    spent = 0
    for chunk in chunks[:MAX_CHUNKS]:
        tokens = estimate_tokens(chunk.text)
        if spent + tokens > MAX_EVIDENCE_TOKENS:
            break
        kept.append(chunk)
        spent += tokens
    return kept, []


def retrieve(state: QueryState, deps: GraphDeps) -> dict[str, Any]:
    """Build the evidence pack; compare gives each entity half the budget."""
    chunks: list[ChunkEvidence] = []
    errors: list[str] = []
    if state["decision"].intent in ("factual_qa", "compare"):
        chunks, errors = _chunks(state, deps)
    budget = MAX_EVIDENCE_TOKENS - sum(estimate_tokens(c.text) for c in chunks)

    entity_budget = budget
    if state["decision"].intent == "compare":
        entity_budget = budget // 2

    per_entity = [
        _within_budget(_entity_items(entity_id, state, deps), entity_budget)
        for entity_id in state["entity_ids"]
    ]
    # Taking turns between entities means the budget trims each one's lowest ranked events.
    merged = [i for i in chain.from_iterable(zip_longest(*per_entity)) if i is not None]
    items = _within_budget(merged, budget)
    tokens = sum(estimate_tokens(item.cloud_text()) for item in items)
    tokens += sum(estimate_tokens(c.text) for c in chunks)
    out: dict[str, Any] = {"evidence": EvidencePack(items=items, chunks=chunks, tokens=tokens)}
    if errors:
        out["errors"] = errors
    return out
