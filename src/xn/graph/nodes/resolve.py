"""Resolve node: map mentions to entities using only what is visible at chapter N."""

from __future__ import annotations

from typing import Any

import rapidfuzz

from xn.graph.deps import GraphDeps
from xn.schemas.state import QueryState
from xn.text import normalize_alias, strip_diacritics

SUGGESTION_LIMIT = 3
SUGGESTION_MIN_SCORE = 60.0


def _suggest(mention: str, novel_id: str, chapter_max: int, deps: GraphDeps) -> list[str]:
    """Return up to three display names of entities whose aliases resemble the mention."""
    target = strip_diacritics(mention)
    best: dict[int, float] = {}
    for row in deps.repo.list_aliases(novel_id=novel_id, chapter_max=chapter_max):
        alias = strip_diacritics(row["alias_norm"])
        score = float(rapidfuzz.fuzz.token_set_ratio(target, alias))
        if score >= SUGGESTION_MIN_SCORE:
            best[row["entity_id"]] = max(score, best.get(row["entity_id"], 0.0))

    ranked = sorted(best, key=lambda entity_id: (-best[entity_id], entity_id))
    names = []
    for entity_id in ranked[:SUGGESTION_LIMIT]:
        entity = deps.repo.get_entity(
            novel_id=novel_id, entity_id=entity_id, chapter_max=chapter_max
        )
        if entity is not None:
            names.append(entity["canonical_name"])
    return names


def _with_revealed_links(
    entity_ids: list[int], novel_id: str, chapter_max: int, deps: GraphDeps
) -> list[int]:
    """Add identities joined by a link revealed by chapter N, following chains of links."""
    found = list(entity_ids)
    for entity_id in found:
        links = deps.repo.get_identity_links(
            novel_id=novel_id, entity_id=entity_id, chapter_max=chapter_max
        )
        for link in links:
            other = link["entity_b"] if link["entity_a"] == entity_id else link["entity_a"]
            if other not in found:
                found.append(other)
    return found


def resolve_entities(state: QueryState, deps: GraphDeps) -> dict[str, Any]:
    """Resolve each mention, and suggest names for the ones that do not match."""
    novel_id = state["novel_id"]
    chapter_max = state["chapter_max"]
    entity_ids: list[int] = []
    unresolved: list[str] = []
    suggestions: dict[str, list[str]] = {}

    for mention in state["decision"].mentions:
        alias_norm = normalize_alias(mention)
        rows = (
            deps.repo.find_aliases(novel_id=novel_id, alias_norm=alias_norm, chapter_max=chapter_max)
            if alias_norm
            else []
        )
        if rows:
            entity_ids.extend(r["entity_id"] for r in rows if r["entity_id"] not in entity_ids)
        else:
            unresolved.append(mention)
            suggestions[mention] = _suggest(alias_norm, novel_id, chapter_max, deps)

    return {
        "entity_ids": _with_revealed_links(entity_ids, novel_id, chapter_max, deps),
        "unresolved_mentions": unresolved,
        "suggestions": suggestions,
    }
