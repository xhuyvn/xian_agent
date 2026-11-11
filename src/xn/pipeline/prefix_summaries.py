r"""Arc prefix summaries (blueprint §3.4 L2): spoiler-safe summaries of an arc's start up to each checkpoint."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from xn.llm import extractor_output, extractor_settings, run_logged
from xn.model_pool import PoolExhaustedError, stage_model
from xn.prompts import load_prompt, profile_from_config
from xn.storage.repository import Repository

logger = logging.getLogger(__name__)

STAGE = "prefix"
CHECKPOINT_EVERY = 10
PROMPT_NAME = "arc_prefix_summary"

# (arc_id, upto_chapter, [(chapter_id, micro_summary), ...] for start..upto) -> summary text
Summarizer = Callable[[int, int, list[tuple[int, str]]], str]


class PrefixSummary(BaseModel):
    r"""The local model's summary of an arc from its start to one checkpoint."""

    text: str = Field(min_length=200, max_length=3000)


class PrefixRequestError(ValueError):
    r"""Raised when prefix summaries cannot run on the requested chapters."""


@dataclass(frozen=True)
class PrefixResult:
    r"""Counts returned by run_prefix."""

    arcs: int
    processed: int  # checkpoints in range
    written: int
    skipped: int  # unchanged input, no model call
    failed: int
    pruned: int  # rows of checkpoints that no longer exist


@dataclass(frozen=True)
class ArcPrefixView:
    r"""What a reader at chapter_max may see of the arc that holds it.

    `summary` covers start_chapter..upto_chapter (None when no checkpoint is stored yet);
    `tail` holds the raw micro-summaries of the chapters after it, up to chapter_max.
    """

    arc_id: int
    start_chapter: int
    chapter_max: int
    upto_chapter: int | None
    summary: str | None
    tail: list[tuple[int, str]]


def checkpoints(start: int, end: int) -> list[int]:
    r"""Checkpoint chapters of an arc: every multiple of 10 with start < c <= end (D1)."""
    first = (start // CHECKPOINT_EVERY + 1) * CHECKPOINT_EVERY
    return list(range(first, end + 1, CHECKPOINT_EVERY))


def _input_hash(
    arc_id: int, start: int, upto: int, window: list[tuple[int, str]], prompt_version: str, model: str
) -> str:
    blob = json.dumps(
        {"arc": [arc_id, start, upto], "rows": window, "prompt": prompt_version, "model": model},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_summarizer(
    repo: Repository, *, novel_id: str, config: dict[str, Any], model: Any, prompt: Any
) -> Summarizer:
    r"""The local-model summarizer; one `llm_runs` row per checkpoint, keyed by upto_chapter."""
    agent = Agent(
        model,
        output_type=extractor_output(PrefixSummary, config),
        system_prompt=prompt.text,
        retries=3,
        model_settings=extractor_settings(config),
    )
    serving_name = config["models"]["local_extractor"]["serving_name"]

    def summarize(arc_id: int, upto: int, window: list[tuple[int, str]]) -> str:
        body = "\n".join(
            f"[Chương {cid}] " + text.replace("<summaries>", "").replace("</summaries>", "")
            for cid, text in window
        )
        user_prompt = f"<upto>{upto}</upto>\n<summaries>\n{body}\n</summaries>"
        result = run_logged(
            agent, user_prompt, repo=repo, novel_id=novel_id, stage=STAGE,
            chapter_id=upto, model=serving_name, prompt_version=prompt.prompt_version,
        )
        return result.output.text

    return summarize


def _arc_spans(repo: Repository, *, novel_id: str, chapter_to: int) -> list[tuple[int, int, int]]:
    r"""(arc_id, start, last) for every arc starting <= chapter_to; last is min(end, chapter_to)."""
    spans = [
        (row["arc_id"], row["start_chapter"], row["end_chapter"])
        for row in repo.list_arcs(novel_id=novel_id, chapter_max=chapter_to)
    ]
    straddling = repo.get_arc_at(novel_id=novel_id, chapter_max=chapter_to)
    if straddling is not None and straddling["arc_id"] not in {a for a, _, _ in spans}:
        spans.append((straddling["arc_id"], straddling["start_chapter"], chapter_to))
    return spans


def run_prefix(
    repo: Repository,
    *,
    novel_id: str,
    config: dict[str, Any],
    chapter_to: int | None = None,
    model: Any = None,
    summarizer: Summarizer | None = None,
    prompts_dir: Path = Path("prompts"),
) -> PrefixResult:
    r"""Write a prefix summary for each checkpoint <= chapter_to of every S5 arc.

    Each summary reads only the micro-summaries of start..checkpoint, through a bounded read.
    A checkpoint whose input hash is unchanged is skipped; a failed one is recorded and the
    others go on.
    """
    if chapter_to is None:
        chapter_to = config["total_chapters"]
    spans = _arc_spans(repo, novel_id=novel_id, chapter_to=chapter_to)
    if not spans:
        raise PrefixRequestError(f"S5 has no arcs in chapters 1-{chapter_to}; run stage s5 first")
    have = {
        row["chapter_id"]
        for row in repo.list_micro_summaries(novel_id=novel_id, chapter_from=1, chapter_max=chapter_to)
        if row["micro_summary"]
    }
    needed = [c for _, start, last in spans for c in range(start, last + 1)]
    missing = [c for c in needed if c not in have]
    if missing:
        raise PrefixRequestError(
            f"S1 has not run on {len(missing)} arc chapters in 1-{chapter_to} (first: {missing[0]})"
        )

    prompts_cfg = config.get("prompts") or {}
    prompt = load_prompt(
        PROMPT_NAME, prompts_cfg.get(PROMPT_NAME, "v1"), profile_from_config(config), prompts_dir
    )
    serving_name = config["models"]["local_extractor"]["serving_name"]

    wanted = [(arc_id, start, c) for arc_id, start, last in spans for c in checkpoints(start, last)]
    pruned = repo.prune_arc_prefix_summaries(
        novel_id=novel_id, keep=[(a, c) for a, _, c in wanted], upto_max=chapter_to, stage=STAGE
    )

    written = skipped = failed = 0
    for arc_id, start, upto in wanted:
        window = [
            (row["chapter_id"], row["micro_summary"])
            for row in repo.list_micro_summaries(novel_id=novel_id, chapter_from=start, chapter_max=upto)
        ]
        input_hash = _input_hash(arc_id, start, upto, window, prompt.prompt_version, serving_name)
        job = repo.get_job(novel_id=novel_id, chapter_id=upto, stage=STAGE)
        if job is not None and job["status"] == "done" and job["input_hash"] == input_hash:
            skipped += 1
            continue

        job_fields: dict[str, Any] = {
            "novel_id": novel_id, "chapter_id": upto, "stage": STAGE,
            "input_hash": input_hash, "prompt_version": prompt.prompt_version, "model": serving_name,
        }
        repo.upsert_job(status="running", **job_fields)
        if summarizer is None:
            summarizer = build_summarizer(
                repo, novel_id=novel_id, config=config,
                model=model if model is not None else stage_model(config, "prefix", repo=repo, novel_id=novel_id), prompt=prompt,
            )
        try:
            summary = summarizer(arc_id, upto, window)
            repo.save_arc_prefix_summary(
                novel_id=novel_id, arc_id=arc_id, upto_chapter=upto, summary=summary, stage=STAGE,
                input_hash=input_hash, prompt_version=prompt.prompt_version, model=serving_name,
            )
        except PoolExhaustedError as err:  # G-32: stop, so a resume picks up here
            repo.upsert_job(status="failed", error=str(err), **job_fields)
            raise
        except Exception as err:
            logger.error("Prefix summary of arc %d up to chapter %d failed: %s", arc_id, upto, err)
            repo.upsert_job(status="failed", error=str(err), **job_fields)
            failed += 1
            continue
        written += 1
    return PrefixResult(
        arcs=len(spans), processed=len(wanted), written=written, skipped=skipped, failed=failed, pruned=pruned
    )


def arc_prefix_view(repo: Repository, *, novel_id: str, chapter_max: int) -> ArcPrefixView | None:
    r"""Snap chapter_max down to the arc's nearest stored checkpoint and append the raw tail.

    Returns None when chapter_max lies in no arc. Every read is bounded by chapter_max, and
    the arc's end chapter is never read.
    """
    arc = repo.get_arc_at(novel_id=novel_id, chapter_max=chapter_max)
    if arc is None:
        return None
    arc_id, start = arc["arc_id"], arc["start_chapter"]
    rows = [
        row for row in repo.get_arc_prefix_summaries(novel_id=novel_id, arc_id=arc_id, chapter_max=chapter_max)
        if row["upto_chapter"] >= start
    ]
    best = rows[-1] if rows else None
    tail_from = best["upto_chapter"] + 1 if best is not None else start
    tail = [
        (row["chapter_id"], row["micro_summary"])
        for row in repo.list_micro_summaries(novel_id=novel_id, chapter_from=tail_from, chapter_max=chapter_max)
        if row["micro_summary"]
    ]
    return ArcPrefixView(
        arc_id=arc_id,
        start_chapter=start,
        chapter_max=chapter_max,
        upto_chapter=best["upto_chapter"] if best is not None else None,
        summary=best["summary"] if best is not None else None,
        tail=tail,
    )
