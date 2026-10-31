r"""Stage S2: In-memory entity resolver and adjudicator."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Callable

from pydantic_ai import Agent
import rapidfuzz

from xn.llm import run_logged
from xn.schemas.extraction import ChapterExtraction, EntityEvent
from xn.schemas.lifecycle import LifecycleEventType
from xn.schemas.resolution import AdjudicationDecision
from xn.storage.repository import Repository
from xn.text import normalize_alias, normalize_text, strip_diacritics

logger = logging.getLogger(__name__)

STAGE = "s2"
AUTO_MATCH_SCORE = 92
ADJUDICATE_MIN_SCORE = 80
MAX_ADJUDICATIONS = 3
CONTEXTS_PER_ENTITY = 3


def _fuzzy_score(a: str, b: str) -> float:
    r"""Compute rapidfuzz token_set_ratio score on two normalized alias values."""
    return float(rapidfuzz.fuzz.token_set_ratio(a, b))


def _token_set(text: str) -> set[str]:
    r"""Compute stripped diacritics whitespace token set."""
    return set(strip_diacritics(normalize_alias(text)).split())


def _sanitize_tags(text: str) -> str:
    r"""Remove literal prompt tags to prevent prompt injection."""
    for tag in ("<mention>", "</mention>", "<candidate>", "</candidate>"):
        text = text.replace(tag, "")
    return text


@dataclass(frozen=True)
class Mention:
    r"""Frozen mention representation for adjudication."""

    name: str
    category: str
    chapter_id: int
    context: str


@dataclass(frozen=True)
class Candidate:
    r"""Frozen candidate entity representation for adjudication."""

    entity_id: int
    aliases: list[str] = field(default_factory=list)
    contexts: list[str] = field(default_factory=list)


@dataclass
class ChapterWrites:
    r"""Pending database writes for chapter resolution matching Repository.save_resolution."""

    entities: list[dict[str, Any]] = field(default_factory=list)
    entity_updates: list[dict[str, Any]] = field(default_factory=list)
    aliases: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)


class _SeedGroup:
    r"""Internal representation of a seed alias group scoped by category."""

    def __init__(self, category: str, names: list[str]) -> None:
        self.category = category
        self.names = {normalize_alias(n) for n in names if normalize_alias(n)}
        self.bound_entity_id: int | None = None


class Resolver:
    r"""In-memory entity resolver for novel extraction events."""

    def __init__(
        self,
        *,
        first_entity_id: int = 1,
        seed_aliases: list[dict[str, Any]] | None = None,
        adjudicate: Callable[[Mention, Candidate], bool] | None = None,
    ) -> None:
        self._first_entity_id = first_entity_id
        self._next_entity_id = first_entity_id
        self._adjudicate = adjudicate

        # Known aliases: (category, alias_norm) -> entity_id
        self._aliases: dict[tuple[str, str], int] = {}
        # Entity aliases: entity_id -> list of alias_norm in order
        self._entity_aliases: dict[int, list[str]] = {}
        # Entity category: entity_id -> category
        self._entity_category: dict[int, str] = {}
        # Contexts: entity_id -> list of recent contexts (max CONTEXTS_PER_ENTITY)
        self._contexts: dict[int, list[str]] = {}

        # Seed groups
        self._seed_groups: list[_SeedGroup] = []
        if seed_aliases:
            for item in seed_aliases:
                cat = item.get("category", "")
                names = item.get("names", [])
                self._seed_groups.append(_SeedGroup(cat, names))

        # Entity tracking for ChapterWrites
        self._known_entities: set[int] = set()
        self._pending_new_entities: dict[int, dict[str, Any]] = {}
        self._chapter_mentions: dict[int, int] = {}
        self._last_chapter_seen: dict[int, int] = {}
        self._pending_aliases: list[dict[str, Any]] = []
        self._pending_links: list[dict[str, Any]] = []

    def load_alias(self, *, alias_norm: str, entity_id: int, category: str) -> None:
        r"""Register an already stored alias and bind any matching seed group."""
        norm = normalize_alias(alias_norm)
        if not norm:
            return
        self._aliases[(category, norm)] = entity_id
        if entity_id not in self._entity_aliases:
            self._entity_aliases[entity_id] = []
        if norm not in self._entity_aliases[entity_id]:
            self._entity_aliases[entity_id].append(norm)
        self._entity_category[entity_id] = category
        self._known_entities.add(entity_id)
        self._next_entity_id = max(self._next_entity_id, entity_id + 1)

        for group in self._seed_groups:
            if group.category == category and norm in group.names:
                group.bound_entity_id = entity_id

    def add_context(self, *, entity_id: int, text: str) -> None:
        r"""Keep the last CONTEXTS_PER_ENTITY texts for entity_id."""
        if not text:
            return
        contexts = self._contexts.setdefault(entity_id, [])
        contexts.append(text)
        if len(contexts) > CONTEXTS_PER_ENTITY:
            del contexts[:-CONTEXTS_PER_ENTITY]

    def lookup(self, *, name: str, category: str) -> int | None:
        r"""Exact alias lookup only, with no side effects."""
        norm = normalize_alias(name)
        if not norm:
            return None
        return self._aliases.get((category, norm))

    def add_link(
        self,
        *,
        name_entity: int | None,
        other_entity: int | None,
        chapter_id: int,
        evidence: str | None = None,
    ) -> None:
        r"""Record an identity link between two entities."""
        if name_entity is None or other_entity is None:
            return
        if name_entity == other_entity:
            logger.warning(
                "add_link: entity IDs are equal (%d); recording nothing",
                name_entity,
            )
            return

        ea = min(name_entity, other_entity)
        eb = max(name_entity, other_entity)
        for lk in self._pending_links:
            if lk["entity_a"] == ea and lk["entity_b"] == eb and lk["revealed_chapter"] == chapter_id:
                return
        self._pending_links.append({
            "entity_a": ea,
            "entity_b": eb,
            "revealed_chapter": chapter_id,
            "evidence": evidence,
        })

    def take_writes(self) -> ChapterWrites:
        r"""Return and clear pending chapter writes."""
        entities_out: list[dict[str, Any]] = []
        entity_updates_out: list[dict[str, Any]] = []

        for ent_id, ent_info in self._pending_new_entities.items():
            mentions = self._chapter_mentions.get(ent_id, 0)
            entities_out.append({
                "entity_id": ent_id,
                "canonical_name": ent_info["canonical_name"],
                "category": ent_info["category"],
                "first_chapter": ent_info["first_chapter"],
                "last_chapter": ent_info["last_chapter"],
                "mention_count": mentions,
            })
            self._known_entities.add(ent_id)

        for ent_id, mentions in self._chapter_mentions.items():
            if ent_id not in self._pending_new_entities:
                entity_updates_out.append({
                    "entity_id": ent_id,
                    "last_chapter": self._last_chapter_seen.get(ent_id, 0),
                    "mention_delta": mentions,
                })

        aliases_out = list(self._pending_aliases)
        links_out = list(self._pending_links)

        self._pending_new_entities.clear()
        self._chapter_mentions.clear()
        self._pending_aliases.clear()
        self._pending_links.clear()

        return ChapterWrites(
            entities=entities_out,
            entity_updates=entity_updates_out,
            aliases=aliases_out,
            links=links_out,
        )

    def _create_entity(self, *, canonical_name: str, category: str, chapter_id: int) -> int:
        ent_id = self._next_entity_id
        self._next_entity_id += 1
        self._pending_new_entities[ent_id] = {
            "entity_id": ent_id,
            "canonical_name": canonical_name,
            "category": category,
            "first_chapter": chapter_id,
            "last_chapter": chapter_id,
        }
        self._entity_category[ent_id] = category
        return ent_id

    def _record_mention(self, *, entity_id: int, chapter_id: int, context: str) -> None:
        self._chapter_mentions[entity_id] = self._chapter_mentions.get(entity_id, 0) + 1
        self._last_chapter_seen[entity_id] = chapter_id
        if entity_id in self._pending_new_entities:
            self._pending_new_entities[entity_id]["last_chapter"] = chapter_id
        self.add_context(entity_id=entity_id, text=context)

    def _record_resolution(
        self,
        *,
        entity_id: int,
        norm: str,
        category: str,
        chapter_id: int,
        context: str,
        source: str,
        is_new_alias: bool,
    ) -> None:
        self._record_mention(entity_id=entity_id, chapter_id=chapter_id, context=context)
        if is_new_alias:
            self._pending_aliases.append({
                "alias_norm": norm,
                "entity_id": entity_id,
                "first_chapter": chapter_id,
                "source": source,
            })
            self._aliases[(category, norm)] = entity_id
            if entity_id not in self._entity_aliases:
                self._entity_aliases[entity_id] = []
            if norm not in self._entity_aliases[entity_id]:
                self._entity_aliases[entity_id].append(norm)
            self._entity_category[entity_id] = category

    def resolve(
        self,
        *,
        name: str,
        category: str,
        chapter_id: int,
        context: str,
        exclude: frozenset[int] = frozenset(),
    ) -> int | None:
        r"""Resolve mention to entity ID through the 6-step cascade."""
        norm = normalize_alias(name)
        if not norm:
            logger.warning("Empty normalized alias for name '%s'", name)
            return None

        # Step 1: Exact
        if (category, norm) in self._aliases:
            resolved_id = self._aliases[(category, norm)]
            for group in self._seed_groups:
                if group.category == category and norm in group.names and group.bound_entity_id is None:
                    group.bound_entity_id = resolved_id
            self._record_mention(entity_id=resolved_id, chapter_id=chapter_id, context=context)
            return resolved_id

        # Step 2: Seed
        matched_group: _SeedGroup | None = None
        for group in self._seed_groups:
            if group.category == category and norm in group.names:
                matched_group = group
                break

        if matched_group is not None:
            if matched_group.bound_entity_id is not None:
                resolved_id = matched_group.bound_entity_id
                self._record_resolution(
                    entity_id=resolved_id,
                    norm=norm,
                    category=category,
                    chapter_id=chapter_id,
                    context=context,
                    source="seed",
                    is_new_alias=True,
                )
                return resolved_id
            else:
                resolved_id = self._create_entity(
                    canonical_name=normalize_text(name).strip(),
                    category=category,
                    chapter_id=chapter_id,
                )
                matched_group.bound_entity_id = resolved_id
                self._record_resolution(
                    entity_id=resolved_id,
                    norm=norm,
                    category=category,
                    chapter_id=chapter_id,
                    context=context,
                    source="seed",
                    is_new_alias=True,
                )
                return resolved_id

        # Step 3: Diacritics-stripped
        stripped_norm = strip_diacritics(norm)
        matching_entities: set[int] = set()
        for (cat, alias), eid in self._aliases.items():
            if cat == category and eid not in exclude:
                if strip_diacritics(alias) == stripped_norm:
                    matching_entities.add(eid)

        candidates_from_step3: list[tuple[int, float]] = []
        if len(matching_entities) == 1:
            resolved_id = next(iter(matching_entities))
            self._record_resolution(
                entity_id=resolved_id,
                norm=norm,
                category=category,
                chapter_id=chapter_id,
                context=context,
                source="fuzzy",
                is_new_alias=True,
            )
            return resolved_id
        elif len(matching_entities) > 1:
            candidates_from_step3 = [(eid, 100.0) for eid in matching_entities]

        # Step 4: Fuzzy
        candidates_for_adjudication: list[tuple[int, float]] = []
        if candidates_from_step3:
            candidates_for_adjudication = candidates_from_step3
        else:
            entity_scores: dict[int, list[tuple[str, float]]] = {}
            for (cat, alias), eid in self._aliases.items():
                if cat == category and eid not in exclude:
                    score = _fuzzy_score(norm, alias)
                    entity_scores.setdefault(eid, []).append((alias, score))

            entity_best_scores: dict[int, tuple[float, list[str]]] = {}
            for eid, pairs in entity_scores.items():
                max_sc = max(sc for _, sc in pairs)
                scoring_aliases = [al for al, sc in pairs if sc == max_sc]
                entity_best_scores[eid] = (max_sc, scoring_aliases)

            high_scorers = [
                (eid, data[0], data[1])
                for eid, data in entity_best_scores.items()
                if data[0] >= AUTO_MATCH_SCORE
            ]

            name_tokens = _token_set(norm)

            auto_matched_eid: int | None = None
            if len(high_scorers) == 1:
                eid, score, scoring_aliases = high_scorers[0]
                has_equal_tokens = any(
                    _token_set(al) == name_tokens for al in scoring_aliases
                )
                if has_equal_tokens:
                    auto_matched_eid = eid

            if auto_matched_eid is not None:
                resolved_id = auto_matched_eid
                self._record_resolution(
                    entity_id=resolved_id,
                    norm=norm,
                    category=category,
                    chapter_id=chapter_id,
                    context=context,
                    source="fuzzy",
                    is_new_alias=True,
                )
                return resolved_id

            for eid, (best_sc, _) in entity_best_scores.items():
                if best_sc >= ADJUDICATE_MIN_SCORE:
                    candidates_for_adjudication.append((eid, best_sc))

        # Step 5: Adjudication
        if candidates_for_adjudication:
            candidates_for_adjudication.sort(key=lambda item: (-item[1], item[0]))
            top_candidates = candidates_for_adjudication[:MAX_ADJUDICATIONS]

            mention_obj = Mention(
                name=name,
                category=category,
                chapter_id=chapter_id,
                context=context,
            )

            for eid, _ in top_candidates:
                cand_obj = Candidate(
                    entity_id=eid,
                    aliases=list(self._entity_aliases.get(eid, [])),
                    contexts=list(self._contexts.get(eid, [])),
                )
                if self._adjudicate is not None and self._adjudicate(mention_obj, cand_obj):
                    resolved_id = eid
                    self._record_resolution(
                        entity_id=resolved_id,
                        norm=norm,
                        category=category,
                        chapter_id=chapter_id,
                        context=context,
                        source="llm",
                        is_new_alias=True,
                    )
                    return resolved_id

        # Step 6: New entity
        resolved_id = self._create_entity(
            canonical_name=normalize_text(name).strip(),
            category=category,
            chapter_id=chapter_id,
        )
        self._record_resolution(
            entity_id=resolved_id,
            norm=norm,
            category=category,
            chapter_id=chapter_id,
            context=context,
            source="exact",
            is_new_alias=True,
        )
        return resolved_id


def resolve_chapter(
    resolver: Resolver,
    extraction: ChapterExtraction,
    *,
    categories: list[str],
    identity_links: list[dict[str, Any]],
) -> ChapterWrites:
    r"""Resolve all entity mentions and links for a single chapter extraction."""
    chapter_id = extraction.chapter_id

    for ev in extraction.events:
        context = f"{ev.description} | {ev.evidence}"
        name_entity = resolver.resolve(
            name=ev.name,
            category=ev.category,
            chapter_id=chapter_id,
            context=context,
        )

        if ev.same_as:
            if ev.event_type == LifecycleEventType.REVEALED_IDENTITY or str(ev.event_type) == "REVEALED_IDENTITY":
                exclude = frozenset({name_entity}) if name_entity is not None else frozenset()
                same_entity = resolver.resolve(
                    name=ev.same_as,
                    category=ev.category,
                    chapter_id=chapter_id,
                    context=context,
                    exclude=exclude,
                )
                if name_entity is not None and same_entity is not None:
                    resolver.add_link(
                        name_entity=name_entity,
                        other_entity=same_entity,
                        chapter_id=chapter_id,
                        evidence=ev.evidence,
                    )
            else:
                logger.warning(
                    "Chapter %d: same_as '%s' ignored on non-reveal event type '%s'",
                    chapter_id,
                    ev.same_as,
                    ev.event_type,
                )

        if ev.holder:
            if "character" in categories:
                resolver.resolve(
                    name=ev.holder,
                    category="character",
                    chapter_id=chapter_id,
                    context=context,
                )

    for link in identity_links:
        if link.get("revealed_chapter") == chapter_id:
            cat = link["category"]
            ent_a = resolver.lookup(name=link["name_a"], category=cat)
            ent_b = resolver.lookup(name=link["name_b"], category=cat)
            if ent_a is not None and ent_b is not None:
                resolver.add_link(
                    name_entity=ent_a,
                    other_entity=ent_b,
                    chapter_id=chapter_id,
                    evidence=link.get("evidence"),
                )
            else:
                logger.warning(
                    "Chapter %d: identity link lookup failed for '%s' (%s) and '%s' (%s)",
                    chapter_id,
                    link["name_a"],
                    ent_a,
                    link["name_b"],
                    ent_b,
                )

    return resolver.take_writes()


def make_adjudicator(
    agent: Any,
    *,
    repo: Any,
    novel_id: str,
    serving_name: str,
    prompt_version: str,
) -> Callable[[Mention, Candidate], bool]:
    r"""Construct an adjudicator function calling run_logged with agent."""

    def adjudicate(mention: Mention, candidate: Candidate) -> bool:
        clean_name = _sanitize_tags(mention.name)
        clean_category = _sanitize_tags(mention.category)
        clean_chapter = _sanitize_tags(str(mention.chapter_id))
        clean_context = _sanitize_tags(mention.context)

        clean_aliases = ", ".join(_sanitize_tags(a) for a in candidate.aliases)
        clean_contexts = (
            "\n".join(f"- {_sanitize_tags(c)}" for c in candidate.contexts)
            if candidate.contexts
            else "None"
        )

        user_prompt = (
            f"<mention>\n"
            f"Name: {clean_name}\n"
            f"Category: {clean_category}\n"
            f"Chapter: {clean_chapter}\n"
            f"Context: {clean_context}\n"
            f"</mention>\n"
            f"<candidate>\n"
            f"Aliases: {clean_aliases}\n"
            f"Contexts:\n{clean_contexts}\n"
            f"</candidate>"
        )

        result = run_logged(
            agent,
            user_prompt,
            repo=repo,
            novel_id=novel_id,
            stage=STAGE,
            chapter_id=mention.chapter_id,
            model=serving_name,
            prompt_version=prompt_version,
        )
        output = result.output
        if hasattr(output, "same_entity"):
            return bool(output.same_entity)
        if isinstance(output, dict):
            return bool(output["same_entity"])
        return bool(output)

    return adjudicate
