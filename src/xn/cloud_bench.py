"""Cloud extraction benchmark (G-13, G-14): run the S1 prompt in the cloud and compare with a local run.

This sends raw chapter text to the cloud, a one-off exception to the egress rule, so it stays
out of xn.llm and needs --allow-raw-egress. Production S1 stays local.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field, replace
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

from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput, capture_run_messages
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
import rapidfuzz
import typer

from xn.config import CloudSettings, load_config
from xn.gold import GoldChapter, GoldSet, gold_split, load_gold, name_key, score_extraction
from xn.gold_run import INVALID_CATEGORY, LOW_EVIDENCE
from xn.llm import (
    CLOUD_BACKOFF_S,
    CLOUD_RETRIES,
    CLOUD_TIMEOUT_S,
    UpstreamError,
    _is_retryable,
    _reported_cost,
    cloud_model,
)
from xn.pipeline.s0_parse import run_s0
from xn.pipeline.s1_extract import EVIDENCE_MIN_SCORE, STAGE
from xn.prompts import LoadedPrompt, load_prompt, profile_from_config
from xn.schemas.extraction import ChapterExtraction
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository
from xn.text import normalize_text

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"
DEEPSEEK_MODEL = "deepseek-flash"
DEFAULT_CONCURRENCY = 10
MAX_CONCURRENCY = 20
BENCH_MAX_TOKENS = 8000
RATE_LIMIT_PAUSE_S = 5.0
EGRESS_CLASS = "long_context"
MISATTRIBUTED = "misattributed"

# USD per million tokens, off-peak, from DeepSeek's pricing page on 2026-11-02. Peak is double.
DEEPSEEK_CACHE_HIT_PRICE = 0.003
DEEPSEEK_CACHE_MISS_PRICE = 0.15
DEEPSEEK_OUTPUT_PRICE = 0.6
DEEPSEEK_PEAK_HOURS_UTC = {1, 2, 3, 6, 7, 8, 9}

PAIR_VERSION_SUFFIX = "+pair"
PAIR_RULES = """

CHẾ ĐỘ HAI CHƯƠNG:
- Tin nhắn có hai thẻ <chapter> liên tiếp, là hai chương truyện nối tiếp nhau.
- Trả về first_chapter cho chương trong thẻ thứ nhất và second_chapter cho chương trong thẻ thứ hai, mỗi phần là một ChapterExtraction đầy đủ.
- Có thể đọc chương kia để hiểu ngữ cảnh, nhưng mỗi biến cố, cờ và tóm tắt chỉ ghi cho chương nơi nó thực sự xảy ra, và evidence phải trích từ đúng chương đó.
"""

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
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0


class ChapterPair(BaseModel):
    """Structured extraction output for two chapters sent in one call."""

    first_chapter: ChapterExtraction
    second_chapter: ChapterExtraction


@dataclass(frozen=True)
class _Provider:
    """What differs between cloud routes to a model."""

    default_model: str
    build: Callable[[str], Any]
    wrap_output: Callable[[Any], Any]
    settings: dict[str, Any]
    cost: Callable[[list[ModelMessage], datetime], tuple[float | None, int, int]]


def check_request(*, allow_raw_egress: bool, concurrency: int, provider: str = "openrouter") -> None:
    """Refuse a run before anything is built or sent.

    Raises:
        BenchRequestError: If raw egress is not allowed, or concurrency or provider is out of range.
    """
    if not allow_raw_egress:
        raise BenchRequestError("This run sends raw chapter text to the cloud; pass --allow-raw-egress")
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise BenchRequestError(f"--concurrency must be 1..{MAX_CONCURRENCY}, got {concurrency}")
    if provider not in PROVIDERS:
        raise BenchRequestError(f"--provider must be one of {sorted(PROVIDERS)}, got {provider!r}")


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


def pair_chapters(gold_ids: set[int], chapter_max: int) -> list[int]:
    """Return every chapter of the fixed pairs (1-2, 3-4, ...) that hold a gold chapter."""
    firsts = {n if n % 2 else n - 1 for n in gold_ids}
    return sorted(n for first in firsts for n in (first, first + 1) if n <= chapter_max)


def check_pairs(chapter_ids: list[int]) -> None:
    """Refuse chapters that do not form whole fixed pairs.

    Raises:
        BenchRequestError: If a chapter's partner is missing.
    """
    ids = set(chapter_ids)
    lone = sorted(n for n in ids if (n + 1 if n % 2 else n - 1) not in ids)
    if lone:
        raise BenchRequestError(f"Two-chapter calls need whole pairs (1-2, 3-4, ...); no partner for {lone}")


def _has_evidence(event: Any, text: str) -> bool:
    return rapidfuzz.fuzz.partial_ratio(normalize_text(event.evidence), text) >= EVIDENCE_MIN_SCORE


def filter_events(
    extraction: ChapterExtraction,
    clean_text: str,
    allowed_categories: set[str],
    other_text: str | None = None,
) -> tuple[list[Any], Counter[str]]:
    """Apply S1's category and evidence checks; return kept events and drops by reason.

    other_text is the partner chapter in a two-chapter call: a quote found only there is
    misattributed, not invented.
    """
    kept = []
    dropped: Counter[str] = Counter()
    for ev in extraction.events:
        if ev.category not in allowed_categories:
            dropped[INVALID_CATEGORY] += 1
        elif _has_evidence(ev, clean_text):
            kept.append(ev)
        elif other_text is not None and _has_evidence(ev, other_text):
            dropped[MISATTRIBUTED] += 1
        else:
            dropped[LOW_EVIDENCE] += 1
    return kept, dropped


def bench_model(model_name: str) -> OpenRouterModel:
    """Build the OpenRouter model with JSON-schema output on, as S1 uses locally.

    pydantic-ai assumes DeepSeek models lack it; OpenRouter lists structured_outputs for this one.
    """
    base = cloud_model(model_name)
    profile = {**base.profile, "supports_json_schema_output": True}
    return OpenRouterModel(model_name, provider=base._provider, profile=profile)


def deepseek_model(model_name: str) -> OpenAIChatModel:
    """Build the DeepSeek model; retries live in the benchmark, not the client."""
    cloud = CloudSettings()
    api_key = cloud.deepseek_api_key.get_secret_value()
    if not api_key:
        raise UpstreamError("UPSTREAM_AUTH", "DEEPSEEK_API_KEY is not set")
    client = AsyncOpenAI(
        base_url=cloud.deepseek_base_url, api_key=api_key, timeout=CLOUD_TIMEOUT_S, max_retries=0
    )
    return OpenAIChatModel(model_name, provider=DeepSeekProvider(openai_client=client))


def is_deepseek_peak(at: datetime) -> bool:
    """Tell whether a UTC time is peak; DeepSeek's Chinese public holiday exception is not known here."""
    return at.weekday() < 5 and at.hour in DEEPSEEK_PEAK_HOURS_UTC


def deepseek_cost(messages: list[ModelMessage], at: datetime) -> tuple[float, int, int]:
    """Price the responses, as DeepSeek reports no cost; return cost, cache hit and cache miss tokens."""
    hit = miss = output = 0
    for message in messages:
        if isinstance(message, ModelResponse):
            usage = message.usage
            hit_now = usage.details.get("prompt_cache_hit_tokens", usage.cache_read_tokens)
            hit += hit_now
            miss += usage.input_tokens - hit_now
            output += usage.output_tokens
    cost = (
        hit * DEEPSEEK_CACHE_HIT_PRICE + miss * DEEPSEEK_CACHE_MISS_PRICE + output * DEEPSEEK_OUTPUT_PRICE
    ) / 1_000_000
    return (cost * 2 if is_deepseek_peak(at) else cost), hit, miss


def _openrouter_cost(messages: list[ModelMessage], _: datetime) -> tuple[float | None, int, int]:
    return _reported_cost(messages), 0, 0


PROVIDERS = {
    "openrouter": _Provider(
        default_model=DEFAULT_MODEL,
        build=bench_model,
        wrap_output=NativeOutput,
        settings={"openrouter_usage": {"include": True}, "openrouter_reasoning": {"enabled": False}},
        cost=_openrouter_cost,
    ),
    "deepseek": _Provider(
        default_model=DEEPSEEK_MODEL,
        build=deepseek_model,
        wrap_output=lambda output_type: output_type,
        settings={"extra_body": {"thinking": {"type": "disabled"}}},
        cost=deepseek_cost,
    ),
}


def _error_text(err: Exception) -> str:
    """Name the failure without its message, which could echo chapter text."""
    status = f" {err.status_code}" if isinstance(err, ModelHTTPError) else ""
    return f"{type(err).__name__}{status}"


async def _call_with_retries(
    agent: Any,
    user_prompt: str,
    *,
    repo: Repository,
    novel_id: str,
    chapter_ids: list[int],
    model_name: str,
    prompt_version: str,
    model_settings: dict[str, Any],
    cost_of: Callable[[list[ModelMessage], datetime], tuple[float | None, int, int]],
    slots: asyncio.Semaphore,
    shared: _Shared,
    sleep: Sleep,
) -> Any:
    """Run one call through the model, writing one llm_runs row per attempt.

    The row sits on the first chapter of the call. Waiting for a backoff or a rate-limit pause
    holds no concurrency slot.
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
        cost, cache_hit, cache_miss = cost_of(
            result.all_messages() if result is not None else messages, datetime.now(timezone.utc)
        )
        shared.cache_hit_tokens += cache_hit
        shared.cache_miss_tokens += cache_miss
        repo.add_llm_run(
            novel_id=novel_id,
            run_id=uuid.uuid4().hex,
            stage=STAGE,
            chapter_id=chapter_ids[0],
            model=model_name,
            prompt_version=prompt_version,
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
            latency_ms=latency_ms,
            retries=attempt + (max(usage.requests - 1, 0) if usage is not None else 0),
            ok=1 if result is not None else 0,
            cost_usd=cost,
            egress_class=EGRESS_CLASS,
            payload_chapters=",".join(map(str, chapter_ids)),
        )
        if result is not None:
            return result.output
        if isinstance(error, ModelHTTPError) and error.status_code == 429:
            shared.http_429 += 1
            shared.pause_until = time.monotonic() + RATE_LIMIT_PAUSE_S
        if not _is_retryable(error):
            break
        logger.warning("Chapter %d try %d failed: %s", chapter_ids[0], attempt + 1, type(error).__name__)
    raise error


def _user_prompt(group: tuple[Any, ...]) -> str:
    blocks = []
    for chapter in group:
        sanitized = chapter["clean_text"].replace("<chapter>", "").replace("</chapter>", "")
        blocks.append(f"<chapter>\n{sanitized}\n</chapter>")
    return "\n".join(blocks)


async def _run_group(
    agent: Any,
    group: tuple[Any, ...],
    *,
    repo: Repository,
    novel_id: str,
    model_name: str,
    prompt_version: str,
    model_settings: dict[str, Any],
    cost_of: Callable[[list[ModelMessage], datetime], tuple[float | None, int, int]],
    allowed_categories: set[str],
    slots: asyncio.Semaphore,
    shared: _Shared,
    sleep: Sleep,
) -> None:
    """Extract one call's chapters and save them; a failure is recorded and stops nothing else."""
    chapter_ids = [c["chapter_id"] for c in group]
    try:
        output = await _call_with_retries(
            agent, _user_prompt(group), repo=repo, novel_id=novel_id, chapter_ids=chapter_ids,
            model_name=model_name, prompt_version=prompt_version, model_settings=model_settings,
            cost_of=cost_of, slots=slots, shared=shared, sleep=sleep,
        )
    except Exception as err:
        logger.error("Chapters %s failed: %s", chapter_ids, type(err).__name__)
        for chapter_id in chapter_ids:
            shared.failed[chapter_id] = _error_text(err)
        return

    pair = len(group) == 2
    results = (output.first_chapter, output.second_chapter) if pair else (output,)
    for index, (chapter, extraction) in enumerate(zip(group, results)):
        other_text = group[1 - index]["clean_text"] if pair else None
        kept, dropped = filter_events(extraction, chapter["clean_text"], allowed_categories, other_text)
        shared.dropped.update(dropped)
        final = extraction.model_copy(update={"chapter_id": chapter["chapter_id"], "events": kept})
        repo.save_extraction(
            novel_id=novel_id, chapter_id=chapter["chapter_id"], payload=final.model_dump_json(),
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
    provider: _Provider = PROVIDERS["openrouter"],
    pairs: bool = False,
    sleep: Sleep = asyncio.sleep,
) -> tuple[_Shared, int]:
    """Run the calls not yet saved, at most concurrency at once; return state and chapters run.

    A pair runs again unless both its chapters are saved.
    """
    size = 2 if pairs else 1
    groups = [tuple(chapters[i:i + size]) for i in range(0, len(chapters), size)]
    todo = [
        g for g in groups
        if not all(
            _is_saved(repo, c, novel_id=novel_id, model_name=model_name, prompt_version=prompt.prompt_version)
            for c in g
        )
    ]
    shared = _Shared()
    if not todo:
        return shared, 0

    agent = Agent(
        model_factory(model_name), output_type=provider.wrap_output(ChapterPair if pairs else ChapterExtraction),
        system_prompt=prompt.text, retries=3,
    )
    model_settings = {"temperature": temperature, "max_tokens": BENCH_MAX_TOKENS * size, **provider.settings}
    slots = asyncio.Semaphore(concurrency)
    await asyncio.gather(*(
        _run_group(
            agent, group, repo=repo, novel_id=novel_id, model_name=model_name,
            prompt_version=prompt.prompt_version, model_settings=model_settings, cost_of=provider.cost,
            allowed_categories=allowed_categories, slots=slots, shared=shared, sleep=sleep,
        )
        for group in todo
    ))
    return shared, sum(len(g) for g in todo)


def _payloads(
    repo: Repository,
    *,
    novel_id: str,
    chapter_max: int,
    ids: set[int],
    model_name: str | None = None,
    prompt_version: str | None = None,
) -> dict[int, dict[str, Any]]:
    """Read extractions for ids, limited to one model and prompt version when given."""
    rows = repo.list_extractions(novel_id=novel_id, chapter_max=chapter_max)
    return {
        r["chapter_id"]: json.loads(r["payload"])
        for r in rows
        if r["chapter_id"] in ids
        and model_name in (None, r["model"])
        and prompt_version in (None, r["prompt_version"])
    }


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


def add_totals(
    conn: sqlite3.Connection,
    *,
    model_name: str,
    prompt_version: str,
    shared: _Shared,
    wall_seconds: float,
    chapters_ok: int,
) -> dict[str, Any]:
    """Add this run's counts to the totals kept in the benchmark database, and return them.

    llm_runs can't hold these, and keeping them here means they go when the database goes.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS bench_totals (model TEXT, prompt_version TEXT, http_429 INTEGER, "
        "wall_seconds REAL, chapters_ok INTEGER, dropped TEXT, PRIMARY KEY (model, prompt_version))"
    )
    row = conn.execute(
        "SELECT http_429, wall_seconds, chapters_ok, dropped FROM bench_totals "
        "WHERE model = ? AND prompt_version = ?",
        (model_name, prompt_version),
    ).fetchone()
    dropped = Counter(json.loads(row["dropped"]) if row else {})
    dropped.update(shared.dropped)
    totals = {
        "http_429": (row["http_429"] if row else 0) + shared.http_429,
        "wall_seconds": (row["wall_seconds"] if row else 0.0) + wall_seconds,
        "chapters_ok": (row["chapters_ok"] if row else 0) + chapters_ok,
        "dropped": dict(dropped),
    }
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO bench_totals VALUES (?, ?, ?, ?, ?, ?)",
            (
                model_name, prompt_version, totals["http_429"], totals["wall_seconds"],
                totals["chapters_ok"], json.dumps(totals["dropped"]),
            ),
        )
    return {**totals, **_add_cache_totals(conn, model_name, prompt_version, shared)}


def _add_cache_totals(
    conn: sqlite3.Connection, model_name: str, prompt_version: str, shared: _Shared
) -> dict[str, int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS bench_cache (model TEXT, prompt_version TEXT, hit_tokens INTEGER, "
        "miss_tokens INTEGER, PRIMARY KEY (model, prompt_version))"
    )
    row = conn.execute(
        "SELECT hit_tokens, miss_tokens FROM bench_cache WHERE model = ? AND prompt_version = ?",
        (model_name, prompt_version),
    ).fetchone()
    hit = (row["hit_tokens"] if row else 0) + shared.cache_hit_tokens
    miss = (row["miss_tokens"] if row else 0) + shared.cache_miss_tokens
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO bench_cache VALUES (?, ?, ?, ?)", (model_name, prompt_version, hit, miss)
        )
    return {"cache_hit_tokens": hit, "cache_miss_tokens": miss}


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
    totals: dict[str, Any],
    failed: dict[int, str],
    provider: str = "openrouter",
    pairs: bool = False,
) -> dict[str, Any]:
    """Build the report from the database, so a resumed run reports every chapter."""
    ids = set(chapter_ids)
    gold_ids = {c.chapter_id for c in gold.chapters}
    cloud = _payloads(
        repo, novel_id=novel_id, chapter_max=chapter_max, ids=ids,
        model_name=model_name, prompt_version=prompt_version,
    )
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
    timed = totals["chapters_ok"]
    wall_per_chapter = totals["wall_seconds"] / timed if timed else None
    calls = len(ids) // 2 if pairs else len(ids)
    cache_hit, cache_miss = totals["cache_hit_tokens"], totals["cache_miss_tokens"]
    return {
        "meta": {
            "provider": provider,
            "pairs": pairs,
            "model": model_name,
            "prompt_version": prompt_version,
            "concurrency": concurrency,
            "chapters": chapter_ids,
            "started_at": started_at,
            "wall_seconds": totals["wall_seconds"],
        },
        "reliability": {
            "chapters_ok": done,
            "chapters_failed": {n: failed.get(n, "failed earlier") for n in sorted(ids - set(cloud))},
            "schema_success": done / len(ids) if ids else None,
            "first_try_success": first_try / calls if calls else None,
            "attempts": len(rows),
            "retries": sum(r["retries"] or 0 for r in rows),
            "http_429": totals["http_429"],
        },
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_tokens_per_chapter": input_tokens / done if done else None,
            "output_tokens_per_chapter": output_tokens / done if done else None,
            "cache_hit_tokens": cache_hit,
            "cache_miss_tokens": cache_miss,
            "cache_hit_rate": cache_hit / (cache_hit + cache_miss) if cache_hit + cache_miss else None,
            "cost_usd": cost,
            "cost_usd_per_chapter": cost / done if done else None,
            "attempts_without_cost": sum(1 for r in rows if r["cost_usd"] is None),
            "seconds_per_call_mean": statistics.fmean(seconds) if seconds else None,
            "seconds_per_call_p95": _p95(seconds) if seconds else None,
            "wall_seconds_per_chapter": wall_per_chapter,
        },
        "dropped": {
            "by_reason": totals["dropped"],
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
    model_name: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    extra_chapters: int = 30,
    only: list[int] | None = None,
    allow_raw_egress: bool = False,
    provider_name: str = "openrouter",
    pairs: bool = False,
    gold_pairs: bool = False,
    model_factory: Callable[[str], Any] | None = None,
    sleep: Sleep = asyncio.sleep,
    prompts_dir: Path = Path("prompts"),
) -> dict[str, Any]:
    """Run the benchmark and write the report; refuses before building anything.

    Raises:
        BenchRequestError: If the request is refused.
    """
    check_request(allow_raw_egress=allow_raw_egress, concurrency=concurrency, provider=provider_name)
    if not baseline_db.is_file():
        raise BenchRequestError(f"No baseline database: {baseline_db}")

    provider = PROVIDERS[provider_name]
    model_name = model_name or provider.default_model
    novel_id = config["novel_id"]
    chapter_max = gold.meta["chapter_max"]
    gold_ids = {c.chapter_id for c in gold.chapters}
    if only is not None:
        chapter_ids = only
    elif gold_pairs:
        chapter_ids = pair_chapters(gold_ids, chapter_max)
    else:
        chapter_ids = pick_chapters(gold_ids, chapter_max, extra_chapters)
    if pairs:
        check_pairs(chapter_ids)
    profile = profile_from_config(config)
    prompt = load_prompt("s1_extract", config["prompts"]["s1_extract"], profile, prompts_dir)
    if pairs:
        prompt = replace(
            prompt, text=prompt.text + PAIR_RULES, prompt_version=prompt.prompt_version + PAIR_VERSION_SUFFIX
        )

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
            model_factory=model_factory or provider.build, provider=provider, pairs=pairs, sleep=sleep,
        ))
        totals = add_totals(
            conn, model_name=model_name, prompt_version=prompt.prompt_version, shared=shared,
            wall_seconds=time.perf_counter() - start_time if ran else 0.0,
            chapters_ok=ran - len(shared.failed),
        )
        report = build_report(
            conn, repo, Repository(baseline_conn), gold=gold, novel_id=novel_id,
            chapter_ids=chapter_ids, chapter_max=chapter_max, total_chapters=config["total_chapters"],
            model_name=model_name, prompt_version=prompt.prompt_version, concurrency=concurrency,
            started_at=started_at, totals=totals, failed=shared.failed, provider=provider_name, pairs=pairs,
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
    model: str | None = typer.Option(None, "--model", help="Model; defaults to the provider's"),
    provider: str = typer.Option("openrouter", "--provider", help="openrouter or deepseek"),
    pairs: bool = typer.Option(False, "--pairs", help="Send two chapters per call"),
    gold_pairs: bool = typer.Option(False, "--gold-pairs", help="Run every fixed pair holding a gold chapter"),
    concurrency: int = typer.Option(DEFAULT_CONCURRENCY, "--concurrency", help="Calls at once, 1-20"),
    extra_chapters: int = typer.Option(30, "--extra-chapters", help="Non-gold chapters to add"),
    only: str | None = typer.Option(None, "--only", help="Comma list of chapters, for a pilot"),
    novel: str = typer.Option("pntt1", "--novel", help="Novel ID"),
    allow_raw_egress: bool = typer.Option(
        False, "--allow-raw-egress", help="Confirm raw chapter text may go to the provider"
    ),
) -> None:
    """Benchmark a cloud model on S1 extraction against a local run."""
    logging.basicConfig(level=logging.WARNING)
    try:
        check_request(allow_raw_egress=allow_raw_egress, concurrency=concurrency, provider=provider)
        report = run_bench(
            db=db, raw_dir=raw_dir, out=out, baseline_db=baseline_db,
            config=load_config(novel, configs_dir=Path("configs")), gold=load_gold(Path("eval") / novel),
            model_name=model, concurrency=concurrency, extra_chapters=extra_chapters,
            only=_parse_only(only), allow_raw_egress=allow_raw_egress, provider_name=provider,
            pairs=pairs, gold_pairs=gold_pairs,
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
