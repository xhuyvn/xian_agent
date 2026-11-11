r"""Stage S5: arc segmentation by a summary-embedding change score, constrained DP and a local LLM review."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Any, Callable

from pydantic_ai import Agent

from xn.llm import extractor_output, extractor_settings, run_logged
from xn.model_pool import PoolExhaustedError, stage_model
from xn.pipeline.s4_embed import Embedder
from xn.prompts import load_prompt, profile_from_config
from xn.schemas.arc import BoundaryReview
from xn.schemas.extraction import ChapterExtraction
from xn.storage.qdrant import QdrantStore
from xn.storage.repository import Repository

logger = logging.getLogger(__name__)

STAGE = "s5"
JOB_CHAPTER = 0  # S5 is novel-wide, so its job row uses the sentinel chapter (OQ-05)

DEFAULT_WEIGHTS = {"map_change": 0.40, "realm_breakthrough": 0.30, "major_conflict": 0.20, "time_skip": 0.10}

# (chapter_id, window of (chapter_id, micro_summary)) -> proposed start chapter, or None to keep it
Reviewer = Callable[[int, list[tuple[int, str]]], int | None]


class S5RequestError(ValueError):
    r"""Raised when S5 cannot run on the requested chapters."""


@dataclass(frozen=True)
class S5Settings:
    r"""Segmentation settings read from the novel's `arc_detection` block."""

    weights: dict[str, float]
    smoothing_window: int
    change_scales: tuple[int, ...]  # window sizes, in chapters, compared on each side of a chapter
    flag_weight: float  # weight of the smoothed flag score next to the change score
    min_len: int
    max_len: int
    boundary_penalty: float
    review_window: int
    max_shift: int

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> S5Settings:
        cfg = config.get("arc_detection") or {}
        return cls(
            weights={**DEFAULT_WEIGHTS, **(cfg.get("weights") or {})},
            smoothing_window=int(cfg.get("smoothing_window", 5)),
            change_scales=tuple(int(k) for k in cfg.get("change_scales", (5, 10))),
            flag_weight=float(cfg.get("flag_weight", 0.1)),
            min_len=int(cfg.get("min_arc_length", 30)),
            max_len=int(cfg.get("max_arc_length", 90)),
            boundary_penalty=float(cfg.get("boundary_penalty", 0.30)),
            review_window=int(cfg.get("review_window", 10)),
            max_shift=int(cfg.get("max_shift", 10)),
        )


@dataclass(frozen=True)
class S5Result:
    r"""Counts returned by run_s5."""

    arcs: int
    shifted: int  # boundaries the review moved
    review_failed: int  # boundaries kept because the review call failed
    skipped: bool  # True when the input was unchanged and nothing was rewritten


def chapter_score(extraction: ChapterExtraction, weights: dict[str, float]) -> float:
    r"""Score `s_i = Σ w·flag`; any time skip counts, with no year threshold (OQ-04)."""
    flags = {
        "map_change": extraction.location_change,
        "realm_breakthrough": extraction.realm_breakthrough,
        "major_conflict": extraction.major_conflict,
        "time_skip": extraction.time_skip_years > 0,
    }
    return sum(weights.get(name, 0.0) for name, on in flags.items() if on)


def smooth(scores: list[float], window: int) -> list[float]:
    r"""Centered moving average; the window is truncated at both ends."""
    half = window // 2
    out = []
    for i in range(len(scores)):
        part = scores[max(0, i - half): i + half + 1]
        out.append(sum(part) / len(part))
    return out


def _unit_mean(vectors: list[list[float]]) -> list[float]:
    mean = [sum(xs) / len(vectors) for xs in zip(*vectors)]
    norm = math.sqrt(sum(x * x for x in mean)) or 1.0
    return [x / norm for x in mean]


def change_scores(vectors: list[list[float]], scales: tuple[int, ...]) -> list[float]:
    r"""How much the story moves at each chapter, scaled to 0..1.

    For each scale k, the cosine distance between the mean summary embedding of the k chapters
    before and the k chapters from this one on, summed over scales. Chapter 1 scores 0.
    """
    n = len(vectors)
    raw = [0.0] * n
    for k in scales:
        for i in range(1, n):
            before = _unit_mean(vectors[max(0, i - k): i])
            after = _unit_mean(vectors[i: i + k])
            raw[i] += (1 - sum(a * b for a, b in zip(before, after))) / 2
    lo, hi = min(raw, default=0.0), max(raw, default=0.0)
    return [(s - lo) / (hi - lo) if hi > lo else 0.0 for s in raw]


def boundary_scores(
    extractions: list[ChapterExtraction], vectors: list[list[float]], settings: S5Settings
) -> list[float]:
    r"""The DP score per chapter: the change score plus the weighted, smoothed flag score."""
    flags = smooth([chapter_score(e, settings.weights) for e in extractions], settings.smoothing_window)
    change = change_scores(vectors, settings.change_scales)
    return [c + settings.flag_weight * f for c, f in zip(change, flags)]


def segment(scores: list[float], *, min_len: int, max_len: int, penalty: float) -> list[int]:
    r"""Pick arc starts (1-based) maximizing Σ score at each new start minus a penalty per boundary.

    Every arc is min_len..max_len chapters long. Fewer than min_len chapters form one arc.
    Ties go to the shorter last arc, so the result is deterministic.
    """
    n = len(scores)
    if n < min_len:
        return [1] if n else []
    neg = float("-inf")
    best = [neg] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for end in range(min_len, n + 1):
        for length in range(min_len, min(max_len, end) + 1):
            start = end - length  # chapters start+1..end form the last arc
            if best[start] == neg:
                continue
            gain = scores[start] - penalty if start > 0 else 0.0
            value = best[start] + gain
            if value > best[end]:
                best[end] = value
                back[end] = start
    starts = []
    end = n
    while end > 0:
        start = back[end]
        starts.append(start + 1)
        end = start
    return sorted(starts)


def review_boundaries(
    starts: list[int],
    *,
    n: int,
    summaries: dict[int, str],
    reviewer: Reviewer,
    settings: S5Settings,
) -> tuple[list[int], int, int]:
    r"""Let the reviewer move each boundary by at most max_shift, keeping every arc min..max long.

    Boundaries are reviewed left to right; each one is clamped against the already reviewed
    previous start and the next DP start. Returns (starts, shifted, failed).
    """
    out = [starts[0]]
    shifted = failed = 0
    for i, b in enumerate(starts[1:], start=1):
        prev = out[-1]
        nxt = starts[i + 1] if i + 1 < len(starts) else n + 1
        lo = max(b - settings.max_shift, prev + settings.min_len, nxt - settings.max_len)
        hi = min(b + settings.max_shift, prev + settings.max_len, nxt - settings.min_len)
        w = settings.review_window
        window = [(c, summaries[c]) for c in range(max(1, b - w), min(n, b + w) + 1)]
        try:
            proposed = reviewer(b, window)
        except PoolExhaustedError:  # G-32: stop S5 (the job is marked failed) so a resume redoes it
            raise
        except Exception as err:
            logger.warning("Boundary %d review failed (%s); keeping it", b, err)
            proposed = None
            failed += 1
        choice = b if proposed is None else min(max(proposed, lo), hi)
        if choice != b:
            shifted += 1
        out.append(choice)
    return out, shifted, failed


def spans_from_starts(starts: list[int], n: int) -> list[tuple[int, int]]:
    r"""Turn arc starts into inclusive (start, end) spans over chapters 1..n."""
    ends = [s - 1 for s in starts[1:]] + [n]
    return list(zip(starts, ends))


def boundary_f1(predicted: list[int], gold: list[int], *, tolerance: int = 3) -> dict[str, float]:
    r"""Precision, recall and F1 of arc starts, chapter 1 excluded, matched one-to-one within ±tolerance."""
    pred = sorted(s for s in set(predicted) if s > 1)
    ref = sorted(s for s in set(gold) if s > 1)
    unmatched = list(ref)
    hits = 0
    for p in pred:
        near = [g for g in unmatched if abs(g - p) <= tolerance]
        if near:
            unmatched.remove(min(near, key=lambda g: (abs(g - p), g)))
            hits += 1
    precision = hits / len(pred) if pred else 0.0
    recall = hits / len(ref) if ref else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "hits": hits, "predicted": len(pred), "gold": len(ref)}


def build_reviewer(
    repo: Repository, *, novel_id: str, config: dict[str, Any], model: Any, prompt: Any
) -> Reviewer:
    r"""The local-model reviewer; one `llm_runs` row per boundary, keyed by the proposed chapter."""
    agent = Agent(
        model,
        output_type=extractor_output(BoundaryReview, config),
        system_prompt=prompt.text,
        retries=3,
        model_settings=extractor_settings(config),
    )
    serving_name = config["models"]["local_extractor"]["serving_name"]

    def review(chapter_id: int, window: list[tuple[int, str]]) -> int | None:
        body = "\n".join(
            f"[Chương {cid}] " + text.replace("<summaries>", "").replace("</summaries>", "")
            for cid, text in window
        )
        user_prompt = f"<proposed>{chapter_id}</proposed>\n<summaries>\n{body}\n</summaries>"
        result = run_logged(
            agent, user_prompt, repo=repo, novel_id=novel_id, stage=STAGE,
            chapter_id=chapter_id, model=serving_name, prompt_version=prompt.prompt_version,
        )
        return result.output.start_chapter

    return review


def _input_hash(
    extractions: list[ChapterExtraction], settings: S5Settings, prompt_version: str, model: str, embed_model: str
) -> str:
    rows = [
        [e.chapter_id, e.location_change, e.realm_breakthrough, e.major_conflict, e.time_skip_years, e.micro_summary]
        for e in extractions
    ]
    blob = json.dumps(
        {"rows": rows, "settings": settings.__dict__, "prompt": prompt_version, "model": model, "embed": embed_model},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def run_s5(
    repo: Repository,
    *,
    novel_id: str,
    config: dict[str, Any],
    store: QdrantStore,
    embedder: Embedder,
    chapter_to: int | None = None,
    model: Any = None,
    reviewer: Reviewer | None = None,
    prompts_dir: Path = Path("prompts"),
) -> S5Result:
    r"""Segment chapters 1..chapter_to into arcs and write them to SQLite and `xn_chunks`.

    The micro-summaries are embedded on each run that is not skipped; chunk points are never re-embedded.

    Needs S1 done on every chapter in range. An unchanged input skips the run, so a rerun
    gives identical rows without calling the model again.
    """
    if chapter_to is None:
        chapter_to = config["total_chapters"]
    settings = S5Settings.from_config(config)
    rows = repo.list_extractions(novel_id=novel_id, chapter_max=chapter_to)
    have = {row["chapter_id"] for row in rows}
    missing = [c for c in range(1, chapter_to + 1) if c not in have]
    if missing:
        raise S5RequestError(
            f"S1 has not run on {len(missing)} chapters in 1-{chapter_to} (first: {missing[0]})"
        )
    extractions = [ChapterExtraction.model_validate_json(row["payload"]) for row in rows]

    prompts_cfg = config.get("prompts") or {}
    prompt = load_prompt(
        "s5_boundary_review", prompts_cfg.get("s5_boundary_review", "v1"),
        profile_from_config(config), prompts_dir,
    )
    serving_name = config["models"]["local_extractor"]["serving_name"]
    input_hash = _input_hash(extractions, settings, prompt.prompt_version, serving_name, embedder.model)

    job = repo.get_job(novel_id=novel_id, chapter_id=JOB_CHAPTER, stage=STAGE)
    if job is not None and job["status"] == "done" and job["input_hash"] == input_hash:
        arcs = len(repo.list_arcs(novel_id=novel_id, chapter_max=chapter_to))
        return S5Result(arcs=arcs, shifted=0, review_failed=0, skipped=True)

    job_fields: dict[str, Any] = {
        "novel_id": novel_id, "chapter_id": JOB_CHAPTER, "stage": STAGE,
        "input_hash": input_hash, "prompt_version": prompt.prompt_version, "model": serving_name,
    }
    repo.upsert_job(status="running", **job_fields)
    try:
        vectors = embedder.embed([e.micro_summary for e in extractions])
        scores = boundary_scores(extractions, vectors, settings)
        starts = segment(
            scores, min_len=settings.min_len, max_len=settings.max_len, penalty=settings.boundary_penalty
        )
        if reviewer is None:
            reviewer = build_reviewer(
                repo, novel_id=novel_id, config=config,
                model=model if model is not None else stage_model(config, "s5", repo=repo, novel_id=novel_id), prompt=prompt,
            )
        summaries = {e.chapter_id: e.micro_summary for e in extractions}
        starts, shifted, review_failed = review_boundaries(
            starts, n=chapter_to, summaries=summaries, reviewer=reviewer, settings=settings
        )
        spans = spans_from_starts(starts, chapter_to)

        store.ensure_collections()
        for arc_id, (start, end) in enumerate(spans, start=1):
            store.set_arc_id(novel_id=novel_id, chapter_from=start, chapter_to=end, arc_id=arc_id)
        store.set_arc_id(novel_id=novel_id, chapter_from=chapter_to + 1, chapter_to=2**31 - 1, arc_id=None)

        repo.save_arc_boundaries(
            novel_id=novel_id, spans=spans, stage=STAGE, input_hash=input_hash,
            prompt_version=prompt.prompt_version, model=serving_name,
        )
    except Exception as err:
        logger.error("S5 failed: %s", err)
        repo.upsert_job(status="failed", error=str(err), **job_fields)
        raise
    return S5Result(arcs=len(spans), shifted=shifted, review_failed=review_failed, skipped=False)
