"""Retrieve node: the evidence pack each intent needs, within the 20k-token budget (§3.3).

factual_qa: hybrid chunks, then the resolved entities' events.
timeline: summaries of arcs ending <= N, the current arc's prefix view, top chunks, events.
deep_dive and compare: per entity, one digest per arc, top ranked events, entity chunks.
"""

from __future__ import annotations

from itertools import chain, zip_longest
import logging
from typing import Any, Optional

from pydantic import ValidationError

from xn.graph.budget import Unit, fit, make_unit, pieces_of, split_text
from xn.graph.deps import GraphDeps
from xn.llm import MAX_CHUNKS, MAX_EVIDENCE_TOKENS, MAX_ITEM_TOKENS
from xn.pipeline.prefix_summaries import arc_prefix_view
from xn.schemas.arc import StoryArc
from xn.schemas.state import (
    ChunkEvidence,
    EvidenceItem,
    EvidencePack,
    QueryState,
    SummaryEvidence,
)
from xn.text import estimate_tokens

logger = logging.getLogger(__name__)

EVENTS_PER_ENTITY = 25
# Events kept until the oldest digests are gone, so the deep-dive gate still has its minimum.
MIN_KEPT_EVENTS = 3
PRIORITY_EVENT_TYPES = ("ACQUISITION", "UPGRADE", "DESTROYED", "TRANSFERRED", "REVEALED_IDENTITY")


def _rank_key(row: Any) -> tuple[int, int, int]:
    """Sort priority event types first, then by story order."""
    return (
        0 if row["event_type"] in PRIORITY_EVENT_TYPES else 1,
        row["chapter_id"],
        row["ordinal"],
    )


def _item_tokens(item: EvidenceItem) -> int:
    return estimate_tokens(item.cloud_text())


def _chunk_tokens(chunk: ChunkEvidence) -> int:
    return estimate_tokens(chunk.text)


def _summary_tokens(summary: SummaryEvidence) -> int:
    return estimate_tokens(summary.text)


def _within_budget(items: list[EvidenceItem], budget: int) -> list[EvidenceItem]:
    """Keep items in rank order until the budget runs out, dropping the lowest ranked."""
    kept: list[EvidenceItem] = []
    spent = 0
    for item in items:
        tokens = _item_tokens(item)
        if spent + tokens > budget:
            break
        kept.append(item)
        spent += tokens
    return kept


def _entity_items(entity_id: int, state: QueryState, deps: GraphDeps) -> list[EvidenceItem]:
    """Return an entity's top ranked events up to chapter N; an oversized event is dropped."""
    novel_id = state["novel_id"]
    chapter_max = state["chapter_max"]
    entity = deps.repo.get_entity(novel_id=novel_id, entity_id=entity_id, chapter_max=chapter_max)
    if entity is None:
        return []
    rows = deps.repo.get_entity_events(
        novel_id=novel_id, entity_id=entity_id, chapter_max=chapter_max
    )
    items = []
    for row in sorted(rows, key=_rank_key)[:EVENTS_PER_ENTITY]:
        item = EvidenceItem(
            event_id=row["id"],
            chapter_id=row["chapter_id"],
            entity_id=entity_id,
            entity_name=entity["canonical_name"],
            event_type=row["event_type"],
            description=row["description"],
            tier_or_realm=row["tier_or_realm"],
            evidence=row["evidence"],
        )
        if _item_tokens(item) > MAX_ITEM_TOKENS:
            logger.warning("Dropped event %d: %d tokens", item.event_id, _item_tokens(item))
            continue
        items.append(item)
    return items


def _interleave(lists: list[list[Any]]) -> list[Any]:
    """Take turns between lists, so trimming the tail cuts each one's lowest ranked."""
    return [x for x in chain.from_iterable(zip_longest(*lists)) if x is not None]


# --- chunks ---


def _search(state: QueryState, deps: GraphDeps) -> list[ChunkEvidence]:
    """Run the hybrid search the intent needs, best chunks first.

    factual_qa and timeline search every chunk; deep_dive and compare search each entity's own.
    """
    retriever = deps.retriever()
    bound = {"novel_id": state["novel_id"], "chapter_max": state["chapter_max"], "query": state["query"]}
    if state["decision"].intent in ("factual_qa", "timeline"):
        chunks = retriever.retrieve(**bound)
        logger.info("Hybrid retrieval timings: %s", retriever.last_timings)
        return chunks

    # Each entity gets an equal share of the chunk limit, from chunks tagged with it.
    entity_ids = state["entity_ids"]
    per_entity = MAX_CHUNKS // len(entity_ids) if entity_ids else 0
    found: list[list[ChunkEvidence]] = []
    for entity_id in entity_ids:
        if per_entity == 0:
            break
        found.append(retriever.retrieve(**bound, entity_id=entity_id, top_k=per_entity))
        logger.info("Hybrid retrieval timings for entity %d: %s", entity_id, retriever.last_timings)
    # A chunk tagged with several entities is kept once, under the entity that found it first.
    seen: set[tuple[int, str, int]] = set()
    chunks = []
    for chunk in _interleave(found):
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
        tokens = _chunk_tokens(chunk)
        if spent + tokens > MAX_EVIDENCE_TOKENS:
            break
        kept.append(chunk)
        spent += tokens
    return kept, []


# --- summaries ---


def _summary_unit(header: str, body: str, **fields: Any) -> Unit:
    """Split one summary into items that each fit the per-item limit, kept or dropped together."""
    pieces = [
        SummaryEvidence(text=text, **fields) for text in split_text(header, body, MAX_ITEM_TOKENS)
    ]
    return make_unit(pieces, _summary_tokens)


def _arc_units(state: QueryState, deps: GraphDeps) -> tuple[list[Unit], set[int]]:
    """Return a unit per arc ending <= N, newest first, and the ids of those arcs (§3.4 L2).

    Each unit shrinks to a short form (title and resolution) before it is dropped (D2).
    """
    rows = deps.repo.list_arcs(novel_id=state["novel_id"], chapter_max=state["chapter_max"])
    units: list[Unit] = []
    for row in reversed(rows):
        if not row["payload"]:
            continue
        try:
            arc = StoryArc.model_validate_json(row["payload"])
        except ValidationError as err:
            logger.warning("Skipped arc %d with a bad payload: %s", row["arc_id"], err)
            continue
        arc_id, start, end = row["arc_id"], row["start_chapter"], row["end_chapter"]
        fields = {"kind": "arc_summary", "chapter_id": end, "start_chapter": start, "arc_id": arc_id}
        span = f"Mạch {arc_id} «{arc.title}», chương {start}–{end}"
        short = _summary_unit(f"{span} (tóm lược kết cục): ", arc.resolution, **fields)
        full = _summary_unit(f"{span}: ", arc.summary, **fields)
        units.append(Unit(full.pieces, full.tokens, fallback=short))
    return units, {row["arc_id"] for row in rows}


def _prefix_items(state: QueryState, deps: GraphDeps, complete: set[int]) -> list[SummaryEvidence]:
    """Return the view of the arc holding N, from its start up to N only (§3.4 L2).

    When N is that arc's end chapter the full summary is already in the pack, so none is added.
    """
    chapter_max = state["chapter_max"]
    view = arc_prefix_view(deps.repo, novel_id=state["novel_id"], chapter_max=chapter_max)
    if view is None or view.arc_id in complete:
        return []
    items: list[SummaryEvidence] = []
    if view.summary and view.upto_chapter is not None:
        header = (
            f"Mạch {view.arc_id} đang diễn ra, chương {view.start_chapter}–{view.upto_chapter} "
            f"(tóm tắt đến chương {view.upto_chapter}): "
        )
        items += _summary_unit(
            header,
            view.summary,
            kind="arc_prefix",
            chapter_id=view.upto_chapter,
            start_chapter=view.start_chapter,
            arc_id=view.arc_id,
        ).pieces
    for chapter_id, micro in view.tail:
        items += _summary_unit(
            f"Chương {chapter_id}: ",
            micro,
            kind="micro_summary",
            chapter_id=chapter_id,
            start_chapter=chapter_id,
            arc_id=view.arc_id,
        ).pieces
    return items


def _arc_starts(state: QueryState, deps: GraphDeps) -> dict[int, int]:
    """Map each arc a reader at N has begun to its start chapter."""
    novel_id, chapter_max = state["novel_id"], state["chapter_max"]
    starts = {
        row["arc_id"]: row["start_chapter"]
        for row in deps.repo.list_arcs(novel_id=novel_id, chapter_max=chapter_max)
    }
    current = deps.repo.get_arc_at(novel_id=novel_id, chapter_max=chapter_max)
    if current is not None:
        starts[current["arc_id"]] = current["start_chapter"]
    return starts


def _digest_units(
    entity_id: int, name: str, state: QueryState, deps: GraphDeps, starts: dict[int, int]
) -> list[Unit]:
    """Return one digest per arc, newest first (§3.4 L3).

    Rows are read with upto_chapter <= N, so an arc ending <= N gives its full digest and the
    arc holding N gives its prefix digest at the largest upto_chapter <= N.
    """
    rows = deps.repo.get_entity_arc_digests(
        novel_id=state["novel_id"], entity_id=entity_id, chapter_max=state["chapter_max"]
    )
    best: dict[int, Any] = {}
    for row in rows:
        if row["arc_id"] not in best or row["upto_chapter"] > best[row["arc_id"]]["upto_chapter"]:
            best[row["arc_id"]] = row
    units = []
    for row in sorted(best.values(), key=lambda r: r["upto_chapter"], reverse=True):
        upto = row["upto_chapter"]
        start = starts.get(row["arc_id"], upto)
        header = f"{name} trong mạch {row['arc_id']}, chương {start}–{upto}: "
        units.append(
            _summary_unit(
                header,
                row["digest"],
                kind="digest",
                chapter_id=upto,
                start_chapter=start,
                arc_id=row["arc_id"],
                entity_id=entity_id,
            )
        )
    return units


# --- packs ---


def _pack(
    items: list[EvidenceItem], chunks: list[ChunkEvidence], summaries: list[SummaryEvidence]
) -> EvidencePack:
    tokens = (
        sum(_item_tokens(i) for i in items)
        + sum(_chunk_tokens(c) for c in chunks)
        + sum(_summary_tokens(s) for s in summaries)
    )
    return EvidencePack(items=items, chunks=chunks, summaries=summaries, tokens=tokens)


def _factual_pack(state: QueryState, deps: GraphDeps, chunks: list[ChunkEvidence]) -> EvidencePack:
    """Chunks first, then the resolved entities' events in the budget left."""
    budget = MAX_EVIDENCE_TOKENS - sum(_chunk_tokens(c) for c in chunks)
    per_entity = [
        _within_budget(_entity_items(entity_id, state, deps), budget)
        for entity_id in state["entity_ids"]
    ]
    return _pack(_within_budget(_interleave(per_entity), budget), chunks, [])


def _timeline_pack(state: QueryState, deps: GraphDeps, chunks: list[ChunkEvidence]) -> EvidencePack:
    """Arc summaries, the current arc's prefix view, chunks and events, trimmed in D2 order.

    The prefix view is never dropped. Over budget, events go first, then chunks lowest score
    first, then older arcs shrink to their short form, then the oldest short forms go.
    """
    arcs, complete = _arc_units(state, deps)
    prefix = _prefix_items(state, deps, complete)
    events = _interleave([_entity_items(e, state, deps) for e in state["entity_ids"]])
    budget = MAX_EVIDENCE_TOKENS - sum(_summary_tokens(s) for s in prefix)
    kept_events, kept_chunks, kept_arcs = fit(
        [
            [make_unit([i], _item_tokens) for i in events],
            [make_unit([c], _chunk_tokens) for c in chunks],
            arcs,
        ],
        budget,
    )
    summaries = pieces_of(kept_arcs) + prefix
    return _pack(pieces_of(kept_events), pieces_of(kept_chunks), summaries)


def _entity_pack(
    entity_id: int,
    state: QueryState,
    deps: GraphDeps,
    chunks: list[ChunkEvidence],
    starts: dict[int, int],
    budget: int,
) -> Optional[tuple[list[EvidenceItem], list[ChunkEvidence], list[SummaryEvidence]]]:
    """One entity's digests, events and chunks, trimmed in D4 order to budget.

    Chunks go first, lowest score first; then events, lowest rank first, down to three; then
    the oldest digests; then the last three events.
    """
    entity = deps.repo.get_entity(
        novel_id=state["novel_id"], entity_id=entity_id, chapter_max=state["chapter_max"]
    )
    if entity is None:
        return None
    events = [make_unit([i], _item_tokens) for i in _entity_items(entity_id, state, deps)]
    digests = _digest_units(entity_id, entity["canonical_name"], state, deps, starts)
    kept_chunks, extra_events, kept_digests, first_events = fit(
        [
            [make_unit([c], _chunk_tokens) for c in chunks],
            events[MIN_KEPT_EVENTS:],
            digests,
            events[:MIN_KEPT_EVENTS],
        ],
        budget,
    )
    return (
        pieces_of(first_events + extra_events),
        pieces_of(kept_chunks),
        pieces_of(kept_digests),
    )


def _entities_pack(state: QueryState, deps: GraphDeps, chunks: list[ChunkEvidence]) -> EvidencePack:
    """deep_dive and compare: each entity gets an equal share of the budget (D5)."""
    entity_ids = state["entity_ids"]
    if not entity_ids:
        return _pack([], [], [])
    budget = MAX_EVIDENCE_TOKENS // len(entity_ids)
    starts = _arc_starts(state, deps)
    # The search tags each chunk with its entity; an untagged one goes to the first entity only.
    packs = [
        _entity_pack(
            entity_id,
            state,
            deps,
            [c for c in chunks if c.entity_id == entity_id or (c.entity_id is None and n == 0)],
            starts,
            budget,
        )
        for n, entity_id in enumerate(entity_ids)
    ]
    packs_found = [p for p in packs if p is not None]
    items = _interleave([p[0] for p in packs_found])
    kept_chunks = _interleave([p[1] for p in packs_found])
    summaries = [s for p in packs_found for s in p[2]]
    return _pack(items, kept_chunks, summaries)


def retrieve(state: QueryState, deps: GraphDeps) -> dict[str, Any]:
    """Build the evidence pack for the intent, never over MAX_EVIDENCE_TOKENS."""
    intent = state["decision"].intent
    chunks, errors = _chunks(state, deps)
    if intent == "factual_qa":
        pack = _factual_pack(state, deps, chunks)
    elif intent == "timeline":
        pack = _timeline_pack(state, deps, chunks)
    else:
        pack = _entities_pack(state, deps, chunks)
    out: dict[str, Any] = {"evidence": pack}
    if errors:
        out["errors"] = errors
    return out
