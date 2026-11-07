r"""Stage S7: entity×arc digests and prefix digests at spoiler checkpoints (§3.2 S7, §3.4 L3)."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry

from xn.llm import extractor_output, extractor_settings, local_model, run_logged
from xn.prompts import load_prompt, profile_from_config
from xn.storage.repository import Repository

logger = logging.getLogger(__name__)

STAGE = "s7"
CHARS_PER_TOKEN = 3  # rough size of Vietnamese text in Qwen tokens

# (entity name, category, event lines, upto_chapter) -> digest text
Digester = Callable[[str, str, list[str], int], str]


class S7RequestError(ValueError):
    r"""Raised when S7 cannot run on the requested chapters."""


class EntityDigest(BaseModel):
    r"""The local model's digest of one entity over part of one arc."""

    digest: str = Field(description="One Vietnamese paragraph of 80-150 words")


@dataclass(frozen=True)
class S7Settings:
    r"""Digest settings read from the novel's `entity_digests` block."""

    min_events: int
    checkpoint_every: int
    min_words: int
    max_words: int
    max_input_tokens: int

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> S7Settings:
        cfg = config.get("entity_digests") or {}
        return cls(
            min_events=int(cfg.get("min_events", 3)),
            checkpoint_every=int(cfg.get("checkpoint_every", 10)),
            min_words=int(cfg.get("min_words", 80)),
            max_words=int(cfg.get("max_words", 150)),
            max_input_tokens=int(cfg.get("max_input_tokens", 5000)),
        )


@dataclass(frozen=True)
class S7Result:
    r"""Counts returned by run_s7; arcs are the unit of work."""

    processed: int  # arcs whose digests were rewritten
    skipped: int  # arcs whose input was unchanged
    failed: int  # arcs whose run failed; their old rows are kept
    written: int  # digests written, full and prefix
    full: int
    prefix: int


@dataclass(frozen=True)
class DigestTask:
    r"""One digest to write: an entity's events in the arc up to upto_chapter."""

    entity_id: int
    name: str
    category: str
    upto_chapter: int
    lines: list[str]  # the event lines sent to the model, after the budget
    event_count: int  # len(lines)


def checkpoints(start: int, end: int, every: int) -> list[int]:
    r"""Prefix checkpoints of an arc: multiples of `every` in start..end-1, then end for the full digest."""
    first = -(-start // every) * every
    return list(range(first, end, every)) + [end]


def event_line(row: Any) -> str:
    r"""One event as the model sees it."""
    realm = f" ({row['tier_or_realm']})" if row["tier_or_realm"] else ""
    return f"[Chương {row['chapter_id']}] {row['event_type']}{realm}: {row['description']}"


def fit_budget(rows: list[Any], max_tokens: int) -> list[Any]:
    r"""Keep events within the input budget: MENTIONED go first, then the latest events are cut."""
    def size(rs: list[Any]) -> int:
        return sum(len(event_line(r)) + 1 for r in rs) // CHARS_PER_TOKEN

    if size(rows) <= max_tokens:
        return rows
    rows = [r for r in rows if r["event_type"] != "MENTIONED"]
    kept: list[Any] = []
    used = 0
    for r in rows:
        cost = (len(event_line(r)) + 1) // CHARS_PER_TOKEN
        if used + cost > max_tokens:
            break
        kept.append(r)
        used += cost
    return kept


def plan_arc(
    repo: Repository, *, novel_id: str, start: int, end: int, settings: S7Settings
) -> list[DigestTask]:
    r"""List the digests of one arc, prefix checkpoints first, each read with chapter_max = its upto_chapter.

    A checkpoint gets a digest when the entity has >= min_events events in start..upto and more
    than at its previous digest in this arc; the arc end always gets one when it has enough.
    """
    tasks: list[DigestTask] = []
    last_count: dict[int, int] = {}
    for upto in checkpoints(start, end, settings.checkpoint_every):
        rows = repo.list_events_between(novel_id=novel_id, chapter_from=start, chapter_max=upto)
        by_entity: dict[int, list[Any]] = {}
        for row in rows:
            by_entity.setdefault(row["entity_id"], []).append(row)
        for entity_id in sorted(by_entity):
            events = by_entity[entity_id]
            if len(events) < settings.min_events:
                continue
            if upto != end and len(events) <= last_count.get(entity_id, 0):
                continue
            entity = repo.get_entity(novel_id=novel_id, entity_id=entity_id, chapter_max=upto)
            if entity is None:
                logger.warning("Entity %d has events but is not known at chapter %d; skipped", entity_id, upto)
                continue
            last_count[entity_id] = len(events)
            used = fit_budget(events, settings.max_input_tokens)
            tasks.append(DigestTask(
                entity_id=entity_id, name=entity["canonical_name"], category=entity["category"],
                upto_chapter=upto, lines=[event_line(r) for r in used], event_count=len(used),
            ))
    return tasks


def arc_input_hash(
    arc: tuple[int, int, int], tasks: list[DigestTask], settings: S7Settings, prompt_version: str, model: str
) -> str:
    blob = json.dumps(
        {
            "arc": list(arc),
            "tasks": [[t.entity_id, t.upto_chapter, t.name, t.category, t.lines] for t in tasks],
            "settings": settings.__dict__, "prompt": prompt_version, "model": model,
        },
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def render_user_prompt(name: str, category: str, lines: list[str], upto: int) -> str:
    body = "\n".join(line.replace("<events>", "").replace("</events>", "") for line in lines)
    return (
        f"<entity>{name}</entity>\n<category>{category}</category>\n<upto>{upto}</upto>\n"
        f"<events>\n{body}\n</events>"
    )


def build_digester(
    repo: Repository, *, novel_id: str, config: dict[str, Any], model: Any, prompt: Any, settings: S7Settings
) -> Digester:
    r"""The local-model digester; a digest outside the word range goes back to the model, 3 retries at most."""
    agent = Agent(
        model,
        output_type=extractor_output(EntityDigest, config),
        system_prompt=prompt.text,
        retries=3,
        model_settings=extractor_settings(config),
    )

    @agent.output_validator
    def check_words(output: EntityDigest) -> EntityDigest:
        words = len(output.digest.split())
        target = (settings.min_words + settings.max_words) // 2 - 15
        # at a low temperature a bare "out of range" note gets the same text back, so say which way and how far
        if words > settings.max_words:
            raise ModelRetry(
                f"Đoạn digest có {words} từ, dài hơn giới hạn {settings.max_words} từ. Hãy viết lại NGẮN HƠN, "
                f"khoảng {target} từ: bỏ chi tiết phụ, gộp các sự kiện giống nhau."
            )
        if words < settings.min_words:
            raise ModelRetry(
                f"Đoạn digest có {words} từ, ngắn hơn mức tối thiểu {settings.min_words} từ. Hãy viết lại DÀI HƠN, "
                f"khoảng {target} từ, nêu rõ từng sự kiện."
            )
        return output

    serving_name = config["models"]["local_extractor"]["serving_name"]

    def digest(name: str, category: str, lines: list[str], upto: int) -> str:
        result = run_logged(
            agent, render_user_prompt(name, category, lines, upto), repo=repo, novel_id=novel_id,
            stage=STAGE, chapter_id=upto, model=serving_name, prompt_version=prompt.prompt_version,
        )
        return " ".join(result.output.digest.split())

    return digest


def _require_ledger(repo: Repository, *, novel_id: str, arcs: list[Any]) -> None:
    missing = [
        cid
        for arc in arcs
        for cid in range(arc["start_chapter"], arc["end_chapter"] + 1)
        if (job := repo.get_job(novel_id=novel_id, chapter_id=cid, stage="s3")) is None or job["status"] != "done"
    ]
    if missing:
        raise S7RequestError(f"S3 is not done on {len(missing)} chapters of these arcs (first: {missing[0]})")


def run_s7(
    repo: Repository,
    *,
    novel_id: str,
    config: dict[str, Any],
    chapter_to: int | None = None,
    model: Any = None,
    digester: Digester | None = None,
    prompts_dir: Path = Path("prompts"),
) -> S7Result:
    r"""Write digests for every arc that ends at or before chapter_to.

    Each arc is one job keyed by its end chapter (OQ-05). An unchanged arc is skipped without a
    model call; a failed arc keeps its old rows and the run goes on to the next arc.
    """
    if chapter_to is None:
        chapter_to = config["total_chapters"]
    settings = S7Settings.from_config(config)
    arcs = repo.list_arcs(novel_id=novel_id, chapter_max=chapter_to)
    if not arcs:
        raise S7RequestError(f"No arc ends at or before chapter {chapter_to}; run S5 first")
    _require_ledger(repo, novel_id=novel_id, arcs=arcs)

    prompts_cfg = config.get("prompts") or {}
    prompt = load_prompt(
        "s7_digest", prompts_cfg.get("s7_digest", "v1"), profile_from_config(config), prompts_dir
    )
    serving_name = config["models"]["local_extractor"]["serving_name"]

    processed = skipped = failed = full = prefix = 0
    for arc in arcs:
        arc_id, start, end = arc["arc_id"], arc["start_chapter"], arc["end_chapter"]
        tasks = plan_arc(repo, novel_id=novel_id, start=start, end=end, settings=settings)
        input_hash = arc_input_hash((arc_id, start, end), tasks, settings, prompt.prompt_version, serving_name)
        job = repo.get_job(novel_id=novel_id, chapter_id=end, stage=STAGE)
        if job is not None and job["status"] == "done" and job["input_hash"] == input_hash:
            skipped += 1
            continue

        job_fields: dict[str, Any] = {
            "novel_id": novel_id, "chapter_id": end, "stage": STAGE,
            "input_hash": input_hash, "prompt_version": prompt.prompt_version, "model": serving_name,
        }
        repo.upsert_job(status="running", **job_fields)
        if digester is None:
            digester = build_digester(
                repo, novel_id=novel_id, config=config,
                model=model if model is not None else local_model(config), prompt=prompt, settings=settings,
            )
        began = time.perf_counter()
        try:
            rows = [
                {
                    "entity_id": t.entity_id, "upto_chapter": t.upto_chapter, "event_count": t.event_count,
                    "digest": digester(t.name, t.category, t.lines, t.upto_chapter),
                }
                for t in tasks
            ]
            repo.save_arc_digests(
                novel_id=novel_id, arc_id=arc_id, end_chapter=end, digests=rows, stage=STAGE,
                input_hash=input_hash, prompt_version=prompt.prompt_version, model=serving_name,
            )
        except Exception as err:
            logger.error("S7 arc %d (%d-%d) failed: %s", arc_id, start, end, err)
            repo.upsert_job(status="failed", error=str(err), **job_fields)
            failed += 1
            continue
        arc_full = sum(t.upto_chapter == end for t in tasks)
        processed += 1
        full += arc_full
        prefix += len(tasks) - arc_full
        logger.info(
            "S7 arc %d (%d-%d): %d full, %d prefix digests in %.0f s",
            arc_id, start, end, arc_full, len(tasks) - arc_full, time.perf_counter() - began,
        )
    return S7Result(
        processed=processed, skipped=skipped, failed=failed, written=full + prefix, full=full, prefix=prefix
    )
