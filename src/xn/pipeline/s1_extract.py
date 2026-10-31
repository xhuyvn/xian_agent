r"""Stage S1: Chapter extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
import rapidfuzz

from xn.llm import local_model, run_logged
from xn.prompts import load_prompt, profile_from_config
from xn.schemas.extraction import ChapterExtraction, EntityEvent, MicroSummary
from xn.storage.repository import Repository
from xn.text import estimate_tokens, normalize_text

logger = logging.getLogger(__name__)

MAX_CHAPTER_TOKENS = 7000
OVERLAP_TOKENS = 300
EVIDENCE_MIN_SCORE = 85
STAGE = "s1"


@dataclass(frozen=True)
class S1Result:
    r"""Counts returned by run_s1."""

    processed: int
    written: int
    skipped: int
    failed: int


def _snap_to_whitespace(text: str, target: int) -> int:
    r"""Find the index of the whitespace in text closest to target."""
    if not text:
        return 0
    target = max(0, min(len(text), target))
    max_offset = max(target, len(text) - target)
    for offset in range(max_offset + 1):
        left = target - offset
        if 0 <= left < len(text) and text[left].isspace():
            return left
        right = target + offset
        if 0 <= right < len(text) and text[right].isspace():
            return right
    return target


def _split_chapter(text: str) -> tuple[str, str]:
    r"""Split text into exactly 2 parts overlapping by ~300 tokens (480 chars).

    Cut around the midpoint, snapped to the nearest whitespace so no word is cut.
    """
    total_len = len(text)
    mid = total_len // 2
    overlap_chars = int(OVERLAP_TOKENS * 1.6)
    half_overlap = overlap_chars // 2

    target_start2 = max(0, mid - half_overlap)
    target_end1 = min(total_len, mid + half_overlap)

    snap_start2 = _snap_to_whitespace(text, target_start2)
    snap_end1 = _snap_to_whitespace(text, target_end1)

    return text[:snap_end1].rstrip(), text[snap_start2:].lstrip()


def _extract_single(
    agent: Any,
    text: str,
    *,
    repo: Repository,
    novel_id: str,
    chapter_id: int,
    serving_name: str,
    prompt_version: str,
) -> ChapterExtraction:
    sanitized = text.replace("<chapter>", "").replace("</chapter>", "")
    user_prompt = f"<chapter>\n{sanitized}\n</chapter>"
    result = run_logged(
        agent, user_prompt, repo=repo, novel_id=novel_id, stage=STAGE,
        chapter_id=chapter_id, model=serving_name, prompt_version=prompt_version,
    )
    return result.output


def _extract_split(
    extract_agent: Any,
    resummary_agent: Any,
    text: str,
    *,
    repo: Repository,
    novel_id: str,
    chapter_id: int,
    serving_name: str,
    extract_prompt_version: str,
    resummary_prompt_version: str,
) -> ChapterExtraction:
    part1_text, part2_text = _split_chapter(text)

    if estimate_tokens(part1_text) > MAX_CHAPTER_TOKENS:
        logger.warning("Chapter %d part 1 exceeds %d tokens (%d)", chapter_id, MAX_CHAPTER_TOKENS, estimate_tokens(part1_text))
    if estimate_tokens(part2_text) > MAX_CHAPTER_TOKENS:
        logger.warning("Chapter %d part 2 exceeds %d tokens (%d)", chapter_id, MAX_CHAPTER_TOKENS, estimate_tokens(part2_text))

    p1 = _extract_single(
        extract_agent, part1_text, repo=repo, novel_id=novel_id,
        chapter_id=chapter_id, serving_name=serving_name, prompt_version=extract_prompt_version,
    )
    p2 = _extract_single(
        extract_agent, part2_text, repo=repo, novel_id=novel_id,
        chapter_id=chapter_id, serving_name=serving_name, prompt_version=extract_prompt_version,
    )

    clean_s1 = p1.micro_summary.replace("<summaries>", "").replace("</summaries>", "")
    clean_s2 = p2.micro_summary.replace("<summaries>", "").replace("</summaries>", "")
    resummary_user_prompt = f"<summaries>\n{clean_s1}\n\n{clean_s2}\n</summaries>"

    resummary_result = run_logged(
        resummary_agent, resummary_user_prompt, repo=repo, novel_id=novel_id, stage=STAGE,
        chapter_id=chapter_id, model=serving_name, prompt_version=resummary_prompt_version,
    )
    merged_summary = resummary_result.output.text

    merged_events: list[EntityEvent] = []
    seen_keys: set[tuple[str, Any, str]] = set()
    for ev in p1.events + p2.events:
        key = (ev.name, ev.event_type, ev.evidence)
        if key not in seen_keys:
            seen_keys.add(key)
            merged_events.append(ev)

    map_loc = p2.map_location if (p2.map_location and p2.map_location.strip()) else p1.map_location
    mc_realm = p2.mc_realm if p2.mc_realm is not None else p1.mc_realm

    return p1.model_copy(
        update={
            "map_location": map_loc,
            "location_change": bool(p1.location_change or p2.location_change),
            "mc_realm": mc_realm,
            "realm_breakthrough": bool(p1.realm_breakthrough or p2.realm_breakthrough),
            "major_conflict": bool(p1.major_conflict or p2.major_conflict),
            "time_skip_years": max(p1.time_skip_years, p2.time_skip_years),
            "micro_summary": merged_summary,
            "events": merged_events,
        }
    )


def run_s1(
    repo: Repository,
    *,
    novel_id: str,
    config: dict[str, Any],
    chapter_from: int = 1,
    chapter_to: int | None = None,
    only_chapters: set[int] | None = None,
    model: Any = None,
    prompts_dir: Path = Path("prompts"),
) -> S1Result:
    r"""Run stage S1 extraction over chapters from chapter_from to chapter_to."""
    if chapter_to is None:
        chapter_to = config["total_chapters"]

    if model is None:
        model = local_model(config)

    serving_name = config["models"]["local_extractor"]["serving_name"]
    temperature = config["models"]["local_extractor"].get("temperature", 0.1)

    profile = profile_from_config(config)

    prompts_cfg = config.get("prompts") or {}
    extract_version = prompts_cfg.get("s1_extract", "v1")
    resummary_version = prompts_cfg.get("s1_resummary", "v1")

    extract_prompt = load_prompt("s1_extract", extract_version, profile, prompts_dir)
    resummary_prompt = load_prompt("s1_resummary", resummary_version, profile, prompts_dir)

    extract_agent = Agent(
        model, output_type=ChapterExtraction, system_prompt=extract_prompt.text,
        retries=3, model_settings={"temperature": temperature},
    )
    resummary_agent = Agent(
        model, output_type=MicroSummary, system_prompt=resummary_prompt.text,
        retries=3, model_settings={"temperature": temperature},
    )

    all_chapters = repo.list_chapters(novel_id=novel_id, chapter_max=chapter_to)
    chapters = [
        ch
        for ch in all_chapters
        if ch["chapter_id"] >= chapter_from
        and (only_chapters is None or ch["chapter_id"] in only_chapters)
    ]

    processed = 0
    written = 0
    skipped = 0
    failed = 0

    allowed_categories = set(profile.get("categories") or [])

    for ch in chapters:
        processed += 1
        chapter_id = ch["chapter_id"]
        clean_text = ch["clean_text"]
        input_hash = ch["sha256"]

        existing_job = repo.get_job(novel_id=novel_id, chapter_id=chapter_id, stage=STAGE)
        if (
            existing_job is not None
            and existing_job["status"] == "done"
            and existing_job["input_hash"] == input_hash
            and existing_job["prompt_version"] == extract_prompt.prompt_version
        ):
            skipped += 1
            continue

        repo.upsert_job(
            novel_id=novel_id, chapter_id=chapter_id, stage=STAGE, status="running",
            input_hash=input_hash, prompt_version=extract_prompt.prompt_version, model=serving_name,
        )

        tokens = estimate_tokens(clean_text)

        try:
            if tokens <= MAX_CHAPTER_TOKENS:
                try:
                    extraction = _extract_single(
                        extract_agent, clean_text, repo=repo, novel_id=novel_id,
                        chapter_id=chapter_id, serving_name=serving_name,
                        prompt_version=extract_prompt.prompt_version,
                    )
                except Exception as err:
                    logger.warning(
                        "Chapter %d single-call extraction failed (%s); retrying with 2-part split",
                        chapter_id, err,
                    )
                    extraction = _extract_split(
                        extract_agent, resummary_agent, clean_text, repo=repo, novel_id=novel_id,
                        chapter_id=chapter_id, serving_name=serving_name,
                        extract_prompt_version=extract_prompt.prompt_version,
                        resummary_prompt_version=resummary_prompt.prompt_version,
                    )
            else:
                extraction = _extract_split(
                    extract_agent, resummary_agent, clean_text, repo=repo, novel_id=novel_id,
                    chapter_id=chapter_id, serving_name=serving_name,
                    extract_prompt_version=extract_prompt.prompt_version,
                    resummary_prompt_version=resummary_prompt.prompt_version,
                )

            valid_events = []
            for ev in extraction.events:
                if ev.category not in allowed_categories:
                    logger.warning(
                        "Chapter %d: dropping event '%s' with invalid category '%s'",
                        chapter_id, ev.name, ev.category,
                    )
                    continue

                score = rapidfuzz.fuzz.partial_ratio(normalize_text(ev.evidence), clean_text)
                if score < EVIDENCE_MIN_SCORE:
                    logger.warning(
                        "Chapter %d: dropping event '%s' with low evidence score %.1f < %d: %s",
                        chapter_id, ev.name, score, EVIDENCE_MIN_SCORE, ev.evidence,
                    )
                    continue

                valid_events.append(ev)

            final_extraction = extraction.model_copy(
                update={
                    "chapter_id": chapter_id,
                    "events": valid_events,
                }
            )

            repo.save_extraction(
                novel_id=novel_id, chapter_id=chapter_id, payload=final_extraction.model_dump_json(),
                prompt_version=extract_prompt.prompt_version, model=serving_name, stage=STAGE,
                input_hash=input_hash,
            )
            written += 1
        except Exception as err:
            logger.error("Chapter %d extraction failed: %s", chapter_id, err)
            repo.upsert_job(
                novel_id=novel_id, chapter_id=chapter_id, stage=STAGE, status="failed",
                input_hash=input_hash, prompt_version=extract_prompt.prompt_version,
                model=serving_name, error=str(err),
            )
            failed += 1
            continue

    return S1Result(processed=processed, written=written, skipped=skipped, failed=failed)
