"""Gold set (S1-T9): load eval assets and score the pipeline and query graph against them."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TypeVar

from pydantic import BaseModel, Field

from xn.graph.deps import GraphDeps
from xn.graph.nodes.bound import bound
from xn.graph.nodes.gate import gate
from xn.graph.nodes.resolve import resolve_entities
from xn.graph.nodes.retrieve import retrieve
from xn.graph.nodes.route import route
from xn.llm import local_model, s2_model_key
from xn.pipeline.s2_resolve import (
    Candidate,
    Mention,
    Resolver,
    build_adjudicator,
    load_adjudicate_prompt,
)
from xn.schemas.state import QueryState
from xn.storage.repository import Repository
from xn.text import normalize_alias, strip_diacritics

SPRINT1_SIZES = {"chapters": 20, "aliases": 80, "qa": 15, "spoilers": 10}
MIN_RECALL = 0.80
FLAGS = ("location_change", "realm_breakthrough", "major_conflict")
CONTEXT_CHARS = 200

Adjudicate = Callable[[Mention, Candidate], bool]
_Item = TypeVar("_Item", bound=BaseModel)


class GoldEntity(BaseModel):
    """A named entity with a concrete event in the chapter, and the event types that fit it."""

    name: str
    category: str
    aliases: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(min_length=1)

    def keys(self) -> set[str]:
        return {name_key(name) for name in [self.name, *self.aliases]}


class GoldChapter(BaseModel):
    """Annotation of one chapter; optional names count neither for nor against the extractor."""

    chapter_id: int
    location_change: bool
    realm_breakthrough: bool
    major_conflict: bool
    entities: list[GoldEntity]
    optional: list[str] = Field(default_factory=list)


class GoldAliasPair(BaseModel):
    """Two names in one category; same is false for different things and for hidden identities."""

    a: str
    b: str
    category: str
    same: bool


class GoldQuestion(BaseModel):
    """A question asked at a bound, with the chapters that hold its answer."""

    id: str
    question: str
    chapter_max: int
    answer: str
    answer_chapters: list[int] = Field(min_length=1)


class GoldSpoiler(BaseModel):
    """A question at bound N, and terms that first appear in source_chapter, after N."""

    id: str
    question: str
    chapter_max: int
    kind: str
    forbidden: list[str] = Field(min_length=1)
    source_chapter: int


@dataclass(frozen=True)
class GoldSet:
    """All gold assets of one novel; meta records who annotated them and their chapter scope."""

    meta: dict[str, Any]
    chapters: list[GoldChapter]
    aliases: list[GoldAliasPair]
    qa: list[GoldQuestion]
    spoilers: list[GoldSpoiler]


def name_key(name: str) -> str:
    """Return the form names are compared in: normalized, without diacritics."""
    return strip_diacritics(normalize_alias(name))


def gold_split(chapters: list[GoldChapter]) -> tuple[list[GoldChapter], list[GoldChapter]]:
    """Return the dev and held-out chapters: taking turns in chapter order, dev first."""
    ordered = sorted(chapters, key=lambda c: c.chapter_id)
    return ordered[::2], ordered[1::2]


def _read_jsonl(path: Path, model: type[_Item]) -> list[_Item]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [model.model_validate_json(line) for line in lines if line.strip()]


def load_gold(eval_dir: Path) -> GoldSet:
    """Load the assets under <eval_dir>/gold."""
    gold_dir = eval_dir / "gold"
    return GoldSet(
        meta=json.loads((gold_dir / "meta.json").read_text(encoding="utf-8")),
        chapters=_read_jsonl(gold_dir / "chapters.jsonl", GoldChapter),
        aliases=_read_jsonl(gold_dir / "aliases.jsonl", GoldAliasPair),
        qa=_read_jsonl(gold_dir / "qa.jsonl", GoldQuestion),
        spoilers=_read_jsonl(gold_dir / "spoilers.jsonl", GoldSpoiler),
    )


def _validate_chapters(
    gold: GoldSet, keyed: dict[int, str], *, categories: set[str], event_types: set[str]
) -> list[str]:
    problems = []
    for chapter in gold.chapters:
        text = keyed.get(chapter.chapter_id)
        if text is None:
            problems.append(f"chapter {chapter.chapter_id}: no text")
            continue
        for entity in chapter.entities:
            where = f"chapter {chapter.chapter_id}, {entity.name}"
            if entity.category not in categories:
                problems.append(f"{where}: unknown category {entity.category}")
            problems += [
                f"{where}: unknown event type {t}"
                for t in entity.event_types
                if t not in event_types
            ]
            problems += [f"{where}: name not in text: {key}" for key in entity.keys() if key not in text]
        problems += [
            f"chapter {chapter.chapter_id}: optional name not in text: {name}"
            for name in chapter.optional
            if name_key(name) not in text
        ]
    return problems


def validate_gold(
    gold: GoldSet, texts: dict[int, str], *, categories: Iterable[str], event_types: Iterable[str]
) -> list[str]:
    """Return every problem in the assets, checked against the clean chapter texts."""
    keyed = {chapter_id: name_key(text) for chapter_id, text in texts.items()}
    sizes = {
        "chapters": len(gold.chapters),
        "aliases": len(gold.aliases),
        "qa": len(gold.qa),
        "spoilers": len(gold.spoilers),
    }
    problems = [
        f"{asset}: {size} items, need {SPRINT1_SIZES[asset]}"
        for asset, size in sizes.items()
        if size < SPRINT1_SIZES[asset]
    ]
    problems += _validate_chapters(gold, keyed, categories=set(categories), event_types=set(event_types))

    known_categories = set(categories)
    for pair in gold.aliases:
        if pair.category not in known_categories:
            problems.append(f"alias {pair.a} / {pair.b}: unknown category {pair.category}")
        problems += [
            f"alias not in any chapter: {name}"
            for name in (pair.a, pair.b)
            if not any(name_key(name) in text for text in keyed.values())
        ]

    for item in gold.qa:
        problems += [
            f"{item.id}: answer chapter {n} is above the bound {item.chapter_max}"
            for n in item.answer_chapters
            if n > item.chapter_max
        ]

    for probe in gold.spoilers:
        if probe.source_chapter <= probe.chapter_max:
            problems.append(f"{probe.id}: source chapter is not after the bound")
        source = keyed.get(probe.source_chapter, "")
        early = [text for n, text in keyed.items() if n <= probe.chapter_max]
        for term in probe.forbidden:
            key = name_key(term)
            if key not in source:
                problems.append(f"{probe.id}: {term} not in chapter {probe.source_chapter}")
            if any(key in text for text in early):
                problems.append(f"{probe.id}: {term} already appears by chapter {probe.chapter_max}")
    return problems


@dataclass(frozen=True)
class _Extracted:
    """Names S2 would receive from one chapter, with the events filed under each."""

    names: dict[str, list[dict[str, Any]]]
    others: set[str]  # holder and same_as names, which also become entities


def _extracted(payload: dict[str, Any]) -> _Extracted:
    names: dict[str, list[dict[str, Any]]] = {}
    others: set[str] = set()
    for event in payload["events"]:
        names.setdefault(name_key(event["name"]), []).append(event)
        others |= {name_key(event[f]) for f in ("holder", "same_as") if event.get(f)}
    names.pop("", None)
    others.discard("")
    return _Extracted(names=names, others=others)


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _f1(tp: int, fp: int, fn: int) -> float:
    return _ratio(2 * tp, 2 * tp + fp + fn)


def _flag_f1(chapters: list[GoldChapter], payloads: dict[int, dict[str, Any]]) -> dict[str, float]:
    scores = {}
    for flag in FLAGS:
        pairs = [(getattr(c, flag), bool(payloads[c.chapter_id].get(flag))) for c in chapters]
        scores[flag] = _f1(
            sum(g and p for g, p in pairs),
            sum(p and not g for g, p in pairs),
            sum(g and not p for g, p in pairs),
        )
    return scores


def score_extraction(
    chapters: list[GoldChapter], payloads: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """Score stored extractions against the annotated chapters.

    Recall counts a gold entity as found when any of its names matches an extracted name,
    holder or same_as, because S2 makes entities from all three. Category and event type
    are scored on found entities that have an event under their own name.

    Raises:
        ValueError: A gold chapter has no extraction.
    """
    missing_chapters = [c.chapter_id for c in chapters if c.chapter_id not in payloads]
    if missing_chapters:
        raise ValueError(f"No extraction for gold chapters: {missing_chapters}")

    found = total = false_names = category_ok = event_ok = typed = 0
    missed: list[str] = []
    extra: list[str] = []
    for chapter in chapters:
        seen = _extracted(payloads[chapter.chapter_id])
        allowed: set[str] = {name_key(name) for name in chapter.optional}
        for entity in chapter.entities:
            keys = entity.keys()
            allowed |= keys
            total += 1
            if not keys & (set(seen.names) | seen.others):
                missed.append(f"{chapter.chapter_id}: {entity.name}")
                continue
            found += 1
            events = [e for key in keys for e in seen.names.get(key, [])]
            if events:
                typed += 1
                category_ok += any(e["category"] == entity.category for e in events)
                event_ok += any(e["event_type"] in entity.event_types for e in events)
        wrong = sorted((set(seen.names) | seen.others) - allowed)
        false_names += len(wrong)
        extra += [f"{chapter.chapter_id}: {key}" for key in wrong]

    return {
        "recall": _ratio(found, total),
        "precision": _ratio(found, found + false_names),
        "category_accuracy": _ratio(category_ok, typed),
        "event_type_accuracy": _ratio(event_ok, typed),
        "flag_f1": _flag_f1(chapters, payloads),
        "gold_entities": total,
        "found": found,
        "false_names": false_names,
        "missed": missed,
        "extra": extra,
    }


def _context(name: str, texts: dict[int, str]) -> tuple[int, str]:
    """Return the first chapter holding a name, and the text around it."""
    target = name_key(name)
    for chapter_id in sorted(texts):
        # The key is built character by character, so offsets match the original text.
        at = strip_diacritics(texts[chapter_id]).casefold().find(target)
        if at >= 0:
            return chapter_id, texts[chapter_id][max(at - CONTEXT_CHARS, 0) : at + CONTEXT_CHARS]
    return 1, ""


def resolver_merges(pair: GoldAliasPair, texts: dict[int, str], adjudicate: Adjudicate) -> bool:
    """Run both names through a fresh S2 resolver and report whether they merged."""
    resolver = Resolver(adjudicate=adjudicate)
    ids = []
    for name in (pair.a, pair.b):
        chapter_id, context = _context(name, texts)
        ids.append(
            resolver.resolve(name=name, category=pair.category, chapter_id=chapter_id, context=context)
        )
    return ids[0] == ids[1]


def score_aliases(pairs: list[GoldAliasPair], predicted: list[bool]) -> dict[str, Any]:
    """Score resolver decisions pairwise, with "same" as the positive class."""
    results = list(zip(pairs, predicted))
    tp = sum(p.same and merged for p, merged in results)
    fp = sum(merged and not p.same for p, merged in results)
    fn = sum(p.same and not merged for p, merged in results)
    return {
        "f1": _f1(tp, fp, fn),
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "wrong": [
            f"{p.a} / {p.b}: gold {'same' if p.same else 'different'}"
            for p, merged in results
            if merged != p.same
        ],
    }


def evidence_state(deps: GraphDeps, *, novel_id: str, question: str, chapter_max: int) -> QueryState:
    """Run the query graph's nodes up to gate, which makes no cloud call."""
    state: QueryState = {
        "novel_id": novel_id,
        "query": question,
        "until": chapter_max,
        "mode": "compact",
    }
    for node in (route, bound, resolve_entities, retrieve, gate):
        state.update(node(state, deps))  # type: ignore[typeddict-item]
    return state


def _evidence_chapters(state: QueryState) -> set[int]:
    if state.get("refusal") or state.get("evidence") is None:
        return set()
    return {item.chapter_id for item in state["evidence"].items}


def score_qa(items: list[GoldQuestion], states: list[QueryState]) -> dict[str, Any]:
    """Score whether the evidence reached the chapters that hold each answer."""
    hits = 0
    recall = 0.0
    misses: list[str] = []
    for item, state in zip(items, states):
        wanted = set(item.answer_chapters)
        covered = wanted & _evidence_chapters(state)
        hits += bool(covered)
        recall += len(covered) / len(wanted)
        if not covered:
            misses.append(f"{item.id}: {state.get('refusal') or 'answer chapters not in evidence'}")
    return {
        "citation_hit_rate": _ratio(hits, len(items)),
        "context_recall": recall / len(items) if items else 0.0,
        "refused": sum(bool(s.get("refusal")) for s in states),
        "misses": misses,
    }


def score_spoilers(probes: list[GoldSpoiler], states: list[QueryState]) -> dict[str, Any]:
    """Count probes whose suggestions or cloud-bound evidence hold a forbidden term or later chapter."""
    leaks: list[str] = []
    for probe, state in zip(probes, states):
        shown = [name for names in (state.get("suggestions") or {}).values() for name in names]
        pack = state.get("evidence")
        if pack is not None and not state.get("refusal"):
            shown += [f"{item.cloud_text()} {item.evidence or ''}" for item in pack.items]
            leaks += [
                f"{probe.id}: chapter {item.chapter_id} above bound {probe.chapter_max}"
                for item in pack.items
                if item.chapter_id > probe.chapter_max
            ]
        text = name_key(" ".join(shown))
        leaks += [f"{probe.id}: {term}" for term in probe.forbidden if name_key(term) in text]
    return {"probes": len(probes), "leaks": len(leaks), "details": leaks}


def gold_report(
    repo: Repository,
    *,
    gold: GoldSet,
    config: dict[str, Any],
    model: Optional[Any] = None,
    prompts_dir: Path = Path("prompts"),
) -> dict[str, Any]:
    """Score a real ingest on every gold asset, using the local model for router and adjudicator."""
    novel_id = config["novel_id"]
    scope = gold.meta["chapter_max"]
    texts = {
        row["chapter_id"]: row["clean_text"]
        for row in repo.list_chapters(novel_id=novel_id, chapter_max=scope)
    }
    payloads = {
        row["chapter_id"]: json.loads(row["payload"])
        for row in repo.list_extractions(novel_id=novel_id, chapter_max=scope)
    }
    adjudicator_model = model or local_model(config, s2_model_key(config))
    model = model or local_model(config)
    adjudicate = build_adjudicator(
        repo,
        novel_id=novel_id,
        config=config,
        model=adjudicator_model,
        prompt=load_adjudicate_prompt(config, prompts_dir),
    )
    deps = GraphDeps(repo=repo, config=config, prompts_dir=prompts_dir, router_model=model)

    def states(items: Iterable[GoldQuestion | GoldSpoiler]) -> list[QueryState]:
        return [
            evidence_state(deps, novel_id=novel_id, question=i.question, chapter_max=i.chapter_max)
            for i in items
        ]

    dev, held_out = gold_split(gold.chapters)
    return {
        "meta": gold.meta,
        "extraction": score_extraction(gold.chapters, payloads),
        "extraction_dev": score_extraction(dev, payloads),
        "extraction_held_out": score_extraction(held_out, payloads),
        "aliases": score_aliases(
            gold.aliases, [resolver_merges(p, texts, adjudicate) for p in gold.aliases]
        ),
        "qa": score_qa(gold.qa, states(gold.qa)),
        "spoilers": score_spoilers(gold.spoilers, states(gold.spoilers)),
    }
