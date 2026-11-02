"""Cloud extraction benchmark (G-13): run the S1 prompt on OpenRouter and compare with a local run.

This sends raw chapter text to the cloud, a one-off exception to the egress rule, so it stays
out of xn.llm and needs --allow-raw-egress. Production S1 stays local.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import random
import sqlite3
import statistics
import time
from typing import Any, Awaitable, Callable
import uuid

from pydantic_ai import Agent, NativeOutput, capture_run_messages
from pydantic_ai.exceptions import ModelHTTPError
import rapidfuzz
import typer

from xn.config import load_config
from xn.gold import GoldChapter, GoldSet, gold_split, load_gold, name_key, score_extraction
from xn.gold_run import INVALID_CATEGORY, LOW_EVIDENCE
from xn.llm import CLOUD_BACKOFF_S, CLOUD_RETRIES, _is_retryable, _reported_cost, cloud_model
from xn.pipeline.s0_parse import run_s0
from xn.pipeline.s1_extract import EVIDENCE_MIN_SCORE, STAGE
from xn.prompts import LoadedPrompt, load_prompt, profile_from_config
from xn.schemas.extraction import ChapterExtraction
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository
from xn.text import normalize_text

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"
DEFAULT_CONCURRENCY = 10
MAX_CONCURRENCY = 20
BENCH_MAX_TOKENS = 8000
RATE_LIMIT_PAUSE_S = 5.0
EGRESS_CLASS = "long_context"
ERROR_TEXT_LIMIT = 200

Sleep = Callable[[float], Awaitable[None]]


class BenchRequestError(ValueError):
    """The benchmark was asked for something it must refuse."""


@dataclass
class _Shared:
    """State the chapter tasks share on the event loop."""

    http_429: int = 0
    pause_until: float = 0.0
    dropped: Counter[str] = field(default_factory=Counter)
    failed: dict[int, str] = field(default_factory=dict)


def check_request(*, allow_raw_egress: bool, concurrency: int) -> None:
    """Refuse a run before anything is built or sent.

    Raises:
        BenchRequestError: If raw egress is not allowed or concurrency is out of range.
    """
    if not allow_raw_egress:
        raise BenchRequestError("This run sends raw chapter text to OpenRouter; pass --allow-raw-egress")
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise BenchRequestError(f"--concurrency must be 1..{MAX_CONCURRENCY}, got {concurrency}")


def pick_chapters(gold_ids: set[int], chapter_max: int, extra: int) -> list[int]:
    """Return the gold chapters plus extra non-gold chapters evenly spaced across 1..chapter_max.

    Raises:
        ValueError: If fewer than extra non-gold chapters exist.
    """
    others = [n for n in range(1, chapter_max + 1) if n not in gold_ids]
    if extra > len(others):
        raise ValueError(f"Asked for {extra} non-gold chapters, only {len(others)} exist")
    picked = {others[i * len(others) // extra] for i in range(extra)}
    return sorted(gold_ids | picked)


def filter_events(
    extraction: ChapterExtraction, clean_text: str, allowed_categories: set[str]
) -> tuple[list[Any], Counter[str]]:
    """Apply S1's category and evidence checks; return kept events and drops by reason."""
    kept = []
    dropped: Counter[str] = Counter()
    for ev in extraction.events:
        if ev.category not in allowed_categories:
            dropped[INVALID_CATEGORY] += 1
        elif rapidfuzz.fuzz.partial_ratio(normalize_text(ev.evidence), clean_text) < EVIDENCE_MIN_SCORE:
            dropped[LOW_EVIDENCE] += 1
        else:
            kept.append(ev)
    return kept, dropped


def _error_text(err: Exception) -> str:
    return f"{type(err).__name__}: {str(err)[:ERROR_TEXT_LIMIT]}"


async def _call_with_retries(
    agent: Any,
    user_prompt: str,
    *,
    repo: Repository,
    novel_id: str,
    chapter_id: int,
    model_name: str,
    prompt_version: str,
    model_settings: dict[str, Any],
    slots: asyncio.Semaphore,
    shared: _Shared,
    sleep: Sleep,
) -> ChapterExtraction:
    """Run one chapter through the model, writing one llm_runs row per attempt.

    Waiting for a backoff or a rate-limit pause holds no concurrency slot.
    """
    error: Exception | None = None
    for attempt in range(CLOUD_RETRIES + 1):
        if attempt:
            await sleep(CLOUD_BACKOFF_S * 2 ** (attempt - 1) + random.uniform(0, CLOUD_BACKOFF_S))
        delay = shared.pause_until - time.monotonic()
        if delay > 0:
            await sleep(delay)

        result = None
        error = None
        async with slots:
            start_time = time.perf_counter()
            with capture_run_messages() as messages:
                try:
                    result = await agent.run(user_prompt, model_settings=model_settings)
                except Exception as err:
                    error = err
            latency_ms = int((time.perf_counter() - start_time) * 1000)

        usage = result.usage if result is not None else None
        repo.add_llm_run(
            novel_id=novel_id,
            run_id=uuid.uuid4().hex,
            stage=STAGE,
            chapter_id=chapter_id,
            model=model_name,
            prompt_version=prompt_version,
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
            latency_ms=latency_ms,
            retries=attempt + (max(usage.requests - 1, 0) if usage is not None else 0),
            ok=1 if result is not None else 0,
            cost_usd=_reported_cost(result.all_messages() if result is not None else messages),
            egress_class=EGRESS_CLASS,
            payload_chapters=str(chapter_id),
        )
        if result is not None:
            return result.output
        if isinstance(error, ModelHTTPError) and error.status_code == 429:
            shared.http_429 += 1
            shared.pause_until = time.monotonic() + RATE_LIMIT_PAUSE_S
        if not _is_retryable(error):
            break
        logger.warning("Chapter %d try %d failed: %s", chapter_id, attempt + 1, type(error).__name__)
    raise error


async def _run_chapter(
    agent: Any,
    chapter: Any,
    *,
    repo: Repository,
    novel_id: str,
    model_name: str,
    prompt_version: str,
    model_settings: dict[str, Any],
    allowed_categories: set[str],
    slots: asyncio.Semaphore,
    shared: _Shared,
    sleep: Sleep,
) -> None:
    """Extract one chapter and save it; a failure is recorded and stops nothing else."""
    chapter_id = chapter["chapter_id"]
    clean_text = chapter["clean_text"]
    sanitized = clean_text.replace("<chapter>", "").replace("</chapter>", "")
    try:
        extraction = await _call_with_retries(
            agent, f"<chapter>\n{sanitized}\n</chapter>", repo=repo, novel_id=novel_id,
            chapter_id=chapter_id, model_name=model_name, prompt_version=prompt_version,
            model_settings=model_settings, slots=slots, shared=shared, sleep=sleep,
        )
    except Exception as err:
        logger.error("Chapter %d failed: %s", chapter_id, type(err).__name__)
        shared.failed[chapter_id] = _error_text(err)
        return

    kept, dropped = filter_events(extraction, clean_text, allowed_categories)
    shared.dropped.update(dropped)
    final = extraction.model_copy(update={"chapter_id": chapter_id, "events": kept})
    repo.save_extraction(
        novel_id=novel_id, chapter_id=chapter_id, payload=final.model_dump_json(),
        prompt_version=prompt_version, model=model_name, stage=STAGE, input_hash=chapter["sha256"],
    )


def _is_saved(
    repo: Repository, chapter: Any, *, novel_id: str, model_name: str, prompt_version: str
) -> bool:
    job = repo.get_job(novel_id=novel_id, chapter_id=chapter["chapter_id"], stage=STAGE)
    return (
        job is not None
        and job["status"] == "done"
        and job["input_hash"] == chapter["sha256"]
        and job["prompt_version"] == prompt_version
        and job["model"] == model_name
    )


async def run_chapters(
    repo: Repository,
    *,
    chapters: list[Any],
    novel_id: str,
    model_name: str,
    prompt: LoadedPrompt,
    temperature: float,
    allowed_categories: set[str],
    concurrency: int,
    model_factory: Callable[[str], Any],
    sleep: Sleep = asyncio.sleep,
) -> tuple[_Shared, int]:
    """Run the chapters not yet saved, at most concurrency at once; return state and count run."""
    todo = [
        c for c in chapters
        if not _is_saved(repo, c, novel_id=novel_id, model_name=model_name, prompt_version=prompt.prompt_version)
    ]
    shared = _Shared()
    if not todo:
        return shared, 0

    agent = Agent(
        model_factory(model_name), output_type=NativeOutput(ChapterExtraction),
        system_prompt=prompt.text, retries=3,
    )
    model_settings = {
        "temperature": temperature,
        "max_tokens": BENCH_MAX_TOKENS,
        "openrouter_usage": {"include": True},
        "openrouter_reasoning": {"enabled": False},
    }
    slots = asyncio.Semaphore(concurrency)
    await asyncio.gather(*(
        _run_chapter(
            agent, chapter, repo=repo, novel_id=novel_id, model_name=model_name,
            prompt_version=prompt.prompt_version, model_settings=model_settings,
            allowed_categories=allowed_categories, slots=slots, shared=shared, sleep=sleep,
        )
        for chapter in todo
    ))
    return shared, len(todo)


def _payloads(repo: Repository, *, novel_id: str, chapter_max: int, ids: set[int]) -> dict[int, dict[str, Any]]:
    rows = repo.list_extractions(novel_id=novel_id, chapter_max=chapter_max)
    return {r["chapter_id"]: json.loads(r["payload"]) for r in rows if r["chapter_id"] in ids}


def _scores(chapters: list[GoldChapter], payloads: dict[int, dict[str, Any]]) -> dict[str, Any] | None:
    ran = [c for c in chapters if c.chapter_id in payloads]
    return score_extraction(ran, payloads) if ran else None


def _score_splits(gold: GoldSet, payloads: dict[int, dict[str, Any]]) -> dict[str, Any]:
    dev, held_out = gold_split(gold.chapters)
    return {
        "all": _scores(gold.chapters, payloads),
        "dev": _scores(dev, payloads),
        "held_out": _scores(held_out, payloads),
    }


def _names(payload: dict[str, Any]) -> set[str]:
    return {name_key(e["name"]) for e in payload["events"]}


def name_overlap(
    cloud: dict[int, dict[str, Any]], local: dict[int, dict[str, Any]], gold_ids: set[int]
) -> dict[str, Any]:
    """Compare event names on the non-gold chapters both runs hold."""
    both = cloud_only = local_only = 0
    chapters = sorted((set(cloud) & set(local)) - gold_ids)
    for chapter_id in chapters:
        cloud_names, local_names = _names(cloud[chapter_id]), _names(local[chapter_id])
        both += len(cloud_names & local_names)
        cloud_only += len(cloud_names - local_names)
        local_only += len(local_names - cloud_names)
    union = both + cloud_only + local_only
    return {
        "chapters": len(chapters),
        "both": both,
        "cloud_only": cloud_only,
        "local_only": local_only,
        "jaccard": both / union if union else None,
    }


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _llm_rows(
    conn: sqlite3.Connection, *, novel_id: str, model_name: str, prompt_version: str, ids: set[int]
) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT chapter_id, input_tokens, output_tokens, latency_ms, retries, ok, cost_usd "
        "FROM llm_runs WHERE novel_id = ? AND stage = ? AND model = ? AND prompt_version = ?",
        (novel_id, STAGE, model_name, prompt_version),
    ).fetchall()
    return [r for r in rows if r["chapter_id"] in ids]


def build_report(
    conn: sqlite3.Connection,
    repo: Repository,
    baseline: Repository,
    *,
    gold: GoldSet,
    novel_id: str,
    chapter_ids: list[int],
    chapter_max: int,
    total_chapters: int,
    model_name: str,
    prompt_version: str,
    concurrency: int,
    started_at: str,
    wall_seconds: float,
    ran: int,
    shared: _Shared,
    earlier: dict[str, Any],
) -> dict[str, Any]:
    """Build the report from the database, adding what earlier runs of this report counted."""
    ids = set(chapter_ids)
    gold_ids = {c.chapter_id for c in gold.chapters}
    cloud = _payloads(repo, novel_id=novel_id, chapter_max=chapter_max, ids=ids)
    local = _payloads(baseline, novel_id=novel_id, chapter_max=chapter_max, ids=set(cloud))

    rows = _llm_rows(conn, novel_id=novel_id, model_name=model_name, prompt_version=prompt_version, ids=ids)
    ok_rows = [r for r in rows if r["ok"]]
    per_chapter = Counter(r["chapter_id"] for r in rows)
    first_try = sum(1 for r in ok_rows if per_chapter[r["chapter_id"]] == 1 and not r["retries"])
    seconds = [r["latency_ms"] / 1000 for r in ok_rows]
    input_tokens = sum(r["input_tokens"] or 0 for r in ok_rows)
    output_tokens = sum(r["output_tokens"] or 0 for r in ok_rows)
    cost = sum(r["cost_usd"] or 0.0 for r in rows)
    done = len(cloud)
    kept = sum(len(p["events"]) for p in cloud.values())
    wall_per_chapter = wall_seconds / ran if ran else None

    earlier_reliability = earlier.get("reliability", {})
    dropped = Counter(earlier.get("dropped", {}).get("by_reason", {}))
    dropped.update(shared.dropped)
    return {
        "meta": {
            "model": model_name,
            "prompt_version": prompt_version,
            "concurrency": concurrency,
            "chapters": chapter_ids,
            "started_at": started_at,
            "wall_seconds": wall_seconds,
            "chapters_run_now": ran,
        },
        "reliability": {
            "chapters_ok": done,
            "chapters_failed": shared.failed,
            "schema_success": done / len(ids) if ids else None,
            "first_try_success": first_try / len(ids) if ids else None,
            "attempts": len(rows),
            "retries": sum(r["retries"] or 0 for r in rows),
            "http_429": earlier_reliability.get("http_429", 0) + shared.http_429,
        },
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_tokens_per_chapter": input_tokens / done if done else None,
            "output_tokens_per_chapter": output_tokens / done if done else None,
            "cost_usd": cost,
            "cost_usd_per_chapter": cost / done if done else None,
            "attempts_without_cost": sum(1 for r in rows if r["cost_usd"] is None),
            "seconds_per_call_mean": statistics.fmean(seconds) if seconds else None,
            "seconds_per_call_p95": _p95(seconds) if seconds else None,
            "wall_seconds_per_chapter": wall_per_chapter,
        },
        "dropped": {
            "by_reason": dict(dropped),
            "events_kept": kept,
            "events_per_chapter": kept / done if done else None,
        },
        "cloud": _score_splits(gold, cloud),
        "local": _score_splits(gold, local),
        "overlap": name_overlap(cloud, local, gold_ids),
        "projection": {
            "chapters": total_chapters,
            "cost_usd": cost / done * total_chapters if done else None,
            "hours": wall_per_chapter * total_chapters / 3600 if wall_per_chapter else None,
        },
    }


def _open_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_only(only: str | None) -> list[int] | None:
    if only is None:
        return None
    return sorted({int(part) for part in only.split(",")})


def run_bench(
    *,
    db: Path,
    raw_dir: Path,
    out: Path,
    baseline_db: Path,
    config: dict[str, Any],
    gold: GoldSet,
    model_name: str = DEFAULT_MODEL,
    concurrency: int = DEFAULT_CONCURRENCY,
    extra_chapters: int = 30,
    only: list[int] | None = None,
    allow_raw_egress: bool = False,
    model_factory: Callable[[str], Any] = cloud_model,
    sleep: Sleep = asyncio.sleep,
    prompts_dir: Path = Path("prompts"),
) -> dict[str, Any]:
    """Run the benchmark and write the report; refuses before building anything.

    Raises:
        BenchRequestError: If the request is refused.
    """
    check_request(allow_raw_egress=allow_raw_egress, concurrency=concurrency)
    if not baseline_db.is_file():
        raise BenchRequestError(f"No baseline database: {baseline_db}")

    novel_id = config["novel_id"]
    chapter_max = gold.meta["chapter_max"]
    chapter_ids = only if only is not None else pick_chapters(
        {c.chapter_id for c in gold.chapters}, chapter_max, extra_chapters
    )
    profile = profile_from_config(config)
    prompt = load_prompt("s1_extract", config["prompts"]["s1_extract"], profile, prompts_dir)
    earlier = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}

    db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db)
    baseline_conn = _open_read_only(baseline_db)
    try:
        init_schema(conn)
        repo = Repository(conn)
        run_s0(repo, novel_id=novel_id, raw_dir=raw_dir, config=config, chapter_limit=chapter_max)
        wanted = set(chapter_ids)
        chapters = [
            c for c in repo.list_chapters(novel_id=novel_id, chapter_max=chapter_max)
            if c["chapter_id"] in wanted
        ]

        started_at = datetime.now(timezone.utc).isoformat()
        start_time = time.perf_counter()
        shared, ran = asyncio.run(run_chapters(
            repo, chapters=chapters, novel_id=novel_id, model_name=model_name, prompt=prompt,
            temperature=config["models"]["local_extractor"].get("temperature", 0.1),
            allowed_categories=set(profile.get("categories") or []), concurrency=concurrency,
            model_factory=model_factory, sleep=sleep,
        ))
        report = build_report(
            conn, repo, Repository(baseline_conn), gold=gold, novel_id=novel_id,
            chapter_ids=chapter_ids, chapter_max=chapter_max, total_chapters=config["total_chapters"],
            model_name=model_name, prompt_version=prompt.prompt_version, concurrency=concurrency,
            started_at=started_at, wall_seconds=time.perf_counter() - start_time, ran=ran,
            shared=shared, earlier=earlier,
        )
    finally:
        baseline_conn.close()
        conn.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main(
    db: Path = typer.Option(..., "--db", help="Database for this benchmark; S0 fills it if new"),
    raw_dir: Path = typer.Option(..., "--raw-dir", help="Path to raw chapters directory"),
    out: Path = typer.Option(..., "--out", help="JSON report path"),
    baseline_db: Path = typer.Option(..., "--baseline-db", help="Local run to compare with, read only"),
    model: str = typer.Option(DEFAULT_MODEL, "--model", help="OpenRouter model"),
    concurrency: int = typer.Option(DEFAULT_CONCURRENCY, "--concurrency", help="Calls at once, 1-20"),
    extra_chapters: int = typer.Option(30, "--extra-chapters", help="Non-gold chapters to add"),
    only: str | None = typer.Option(None, "--only", help="Comma list of chapters, for a pilot"),
    novel: str = typer.Option("pntt1", "--novel", help="Novel ID"),
    allow_raw_egress: bool = typer.Option(
        False, "--allow-raw-egress", help="Confirm raw chapter text may go to OpenRouter"
    ),
) -> None:
    """Benchmark an OpenRouter model on S1 extraction against a local run."""
    logging.basicConfig(level=logging.WARNING)
    try:
        check_request(allow_raw_egress=allow_raw_egress, concurrency=concurrency)
        report = run_bench(
            db=db, raw_dir=raw_dir, out=out, baseline_db=baseline_db,
            config=load_config(novel, configs_dir=Path("configs")), gold=load_gold(Path("eval") / novel),
            model_name=model, concurrency=concurrency, extra_chapters=extra_chapters,
            only=_parse_only(only), allow_raw_egress=allow_raw_egress,
        )
    except BenchRequestError as err:
        typer.echo(f"Error: {err}")
        raise typer.Exit(code=1)
    reliability = report["reliability"]
    typer.echo(
        f"ok={reliability['chapters_ok']} failed={len(reliability['chapters_failed'])} "
        f"http_429={reliability['http_429']} cost_usd={report['usage']['cost_usd']:.4f} report={out}"
    )
    if reliability["chapters_failed"]:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    typer.run(main)
