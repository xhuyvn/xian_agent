r"""Stage S6: macro StoryArc synthesis from each arc's micro-summaries on the OpenRouter model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable

from pydantic_ai import Agent

from xn.llm import CompactItem, SpendCapError, cloud_model, run_compact
from xn.pipeline.s4_embed import Embedder
from xn.prompts import load_prompt, profile_from_config
from xn.schemas.arc import StoryArc, StoryArcSynthesis
from xn.schemas.extraction import ChapterExtraction
from xn.storage.qdrant import ArcPoint, QdrantStore
from xn.storage.repository import Repository

logger = logging.getLogger(__name__)

STAGE = "s6"
MODEL_KEY = "macro_synthesizer"
ALL_CHAPTERS = 2**31 - 1  # bound for reading every arc row, to find stale arc points


class S6RequestError(ValueError):
    r"""Raised before any arc is touched, when S6 cannot run on the requested chapters."""


@dataclass(frozen=True)
class S6Result:
    r"""Counts returned by run_s6."""

    arcs: int  # arcs ending at or before chapter_to
    written: int
    skipped: int  # payload already current
    failed: int
    cost_usd: float  # cloud spend reported for this run
    stopped_at: int | None = None  # arc id at which the spend cap stopped the run


def arc_question(start: int, end: int) -> str:
    r"""The `<question>` line of the S6 prompt."""
    return f"Tổng hợp mạch truyện từ chương {start} đến chương {end}."


def _input_hash(
    start: int, end: int, summaries: list[tuple[int, str]], prompt_version: str, model: str,
    temperature: float, embed_model: str,
) -> str:
    blob = json.dumps(
        {
            "span": [start, end], "summaries": summaries, "prompt": prompt_version, "model": model,
            "temperature": temperature, "embed": embed_model,
        },
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def run_s6(
    repo: Repository,
    *,
    novel_id: str,
    config: dict[str, Any],
    store: QdrantStore,
    embedder: Embedder,
    chapter_to: int | None = None,
    cloud_factory: Callable[[str], Any] = cloud_model,
    prompts_dir: Path = Path("prompts"),
    sleep: Callable[[float], None] | None = None,
) -> S6Result:
    r"""Synthesize a `StoryArc` for every S5 arc ending at or before chapter_to.

    Each arc sends only its own chapters' micro-summaries, with its end chapter as the spoiler
    bound. An arc whose payload is current (job done, same input hash) is skipped, so a rerun on
    unchanged input makes no cloud call. One failed arc does not stop the others; reaching the
    spend cap stops the run.
    """
    if chapter_to is None:
        chapter_to = config["total_chapters"]
    arcs = repo.list_arcs(novel_id=novel_id, chapter_max=chapter_to)
    if not arcs:
        raise S6RequestError(f"No arcs end at or before chapter {chapter_to}; run s5 first")
    last = max(arc["end_chapter"] for arc in arcs)
    summaries = {
        row["chapter_id"]: ChapterExtraction.model_validate_json(row["payload"]).micro_summary
        for row in repo.list_extractions(novel_id=novel_id, chapter_max=last)
    }
    for arc in arcs:
        missing = [c for c in range(arc["start_chapter"], arc["end_chapter"] + 1) if c not in summaries]
        if missing:
            raise S6RequestError(
                f"S1 has not run on {len(missing)} chapters of arc {arc['arc_id']} (first: {missing[0]})"
            )

    prompts_cfg = config.get("prompts") or {}
    prompt = load_prompt(
        "s6_arc_synthesize", prompts_cfg.get("s6_arc_synthesize", "v1"),
        profile_from_config(config), prompts_dir,
    )
    model_cfg = config["models"][MODEL_KEY]
    model_name = model_cfg["name"]
    temperature = model_cfg.get("temperature", 0.3)
    agent = Agent(output_type=StoryArcSynthesis, system_prompt=prompt.text, retries=1)
    extra = {"sleep": sleep} if sleep is not None else {}

    store.ensure_collections()
    all_arcs = repo.list_arcs(novel_id=novel_id, chapter_max=ALL_CHAPTERS)
    store.delete_arcs_except(novel_id=novel_id, arc_ids=[arc["arc_id"] for arc in all_arcs])

    started = datetime.now(timezone.utc).isoformat()
    written = skipped = failed = 0
    stopped_at = None
    for arc in arcs:
        arc_id, start, end = arc["arc_id"], arc["start_chapter"], arc["end_chapter"]
        window = [(c, summaries[c]) for c in range(start, end + 1)]
        input_hash = _input_hash(
            start, end, window, prompt.prompt_version, model_name, temperature, embedder.model
        )
        job = repo.get_job(novel_id=novel_id, chapter_id=end, stage=STAGE)
        if (
            arc["payload"] is not None and job is not None
            and job["status"] == "done" and job["input_hash"] == input_hash
        ):
            skipped += 1
            continue

        job_fields: dict[str, Any] = {
            "novel_id": novel_id, "chapter_id": end, "stage": STAGE,
            "input_hash": input_hash, "prompt_version": prompt.prompt_version, "model": model_name,
        }
        repo.upsert_job(status="running", **job_fields)
        try:
            result = run_compact(
                agent,
                arc_question(start, end),
                [CompactItem(kind="micro_summary", chapter_id=c, text=text) for c, text in window],
                repo=repo,
                novel_id=novel_id,
                stage=STAGE,
                chapter_max=end,
                model_name=model_name,
                prompt_version=prompt.prompt_version,
                config=config,
                temperature=temperature,
                model_factory=cloud_factory,
                **extra,
            )
            story = StoryArc(
                arc_id=arc_id, start_chapter=start, end_chapter=end, **result.output.model_dump()
            )
            [dense] = embedder.embed([f"{story.title}\n{story.summary}"])
            store.upsert_arc(
                novel_id=novel_id,
                point=ArcPoint(
                    arc_id=arc_id, start_chapter=start, end_chapter=end,
                    title=story.title, summary=story.summary, dense=dense,
                ),
            )
            repo.save_arc_payload(
                novel_id=novel_id, arc_id=arc_id, start_chapter=start, end_chapter=end,
                payload=story.model_dump_json(), stage=STAGE, input_hash=input_hash,
                prompt_version=prompt.prompt_version, model=model_name,
            )
            written += 1
        except SpendCapError as err:
            logger.error("S6 stopped at arc %d: %s", arc_id, err)
            repo.upsert_job(status="failed", error=str(err), **job_fields)
            stopped_at = arc_id
            break
        except Exception as err:
            logger.error("S6 failed on arc %d (%d-%d): %s", arc_id, start, end, err)
            repo.upsert_job(status="failed", error=str(err), **job_fields)
            failed += 1

    cost, _ = repo.cloud_spend_since(since=started)
    return S6Result(
        arcs=len(arcs), written=written, skipped=skipped, failed=failed,
        cost_usd=round(cost, 6), stopped_at=stopped_at,
    )
