"""Pipeline runner for executing stages s0, s1, s2, and s3."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from xn.pipeline.s0_parse import S0Result, run_s0
from xn.pipeline.s1_extract import S1Result, run_s1
from xn.pipeline.s2_resolve import S2Result, run_s2
from xn.pipeline.s3_ledger import S3Result, compute_input_hash, run_s3
from xn.prompts import load_prompt, profile_from_config
from xn.storage.repository import Repository

logger = logging.getLogger(__name__)

STAGES = ("s0", "s1", "s2", "s3")


class RunRequestError(ValueError):
    """Raised when the runner refuses a request before doing any work."""


def run_stage(
    repo: Repository,
    *,
    stage: str,
    novel_id: str,
    config: dict[str, Any],
    chapter_from: int = 1,
    chapter_to: int | None = None,
    stale_only: bool = False,
    rebuild: bool = False,
    raw_dir: str | Path | None = None,
    model: Any = None,
    prompts_dir: Path = Path("prompts"),
) -> S0Result | S1Result | S2Result | S3Result:
    """Run a pipeline stage with request validation and error checking."""
    # 1. Validation before touching the database
    if stage not in STAGES:
        raise RunRequestError(f"Unknown stage: {stage!r}. Supported stages: {STAGES}")

    if chapter_to is None:
        chapter_to = config.get("total_chapters")

    if chapter_from < 1:
        raise RunRequestError(f"chapter_from ({chapter_from}) must be at least 1")

    if chapter_to is not None and chapter_from > chapter_to:
        raise RunRequestError(
            f"chapter_from ({chapter_from}) cannot be greater than chapter_to ({chapter_to})"
        )

    if stale_only and stage in ("s0", "s2"):
        raise RunRequestError(f"stale_only is not supported on stage {stage}")

    if chapter_from > 1 and stage in ("s0", "s2"):
        raise RunRequestError(f"chapter_from > 1 is not supported on stage {stage}")

    if rebuild and stage != "s2":
        raise RunRequestError(f"rebuild is only supported on stage s2, got {stage}")

    if stage == "s0" and (raw_dir is None or str(raw_dir) == ""):
        raise RunRequestError("raw_dir is required for stage s0")

    # 2. Stage execution
    if stage == "s0":
        return run_s0(
            repo,
            novel_id=novel_id,
            raw_dir=raw_dir,  # type: ignore[arg-type]
            config=config,
            chapter_limit=chapter_to,
        )

    if stage == "s1":
        only_chapters: set[int] | None = None
        if stale_only:
            chapters = repo.list_chapters(novel_id=novel_id, chapter_max=chapter_to)
            profile = profile_from_config(config)
            prompts_cfg = config.get("prompts") or {}
            extract_version = prompts_cfg.get("s1_extract", "v1")
            extract_prompt = load_prompt("s1_extract", extract_version, profile, prompts_dir)
            current_prompt_version = extract_prompt.prompt_version

            stale_set: set[int] = set()
            for ch in chapters:
                cid = ch["chapter_id"]
                if cid < chapter_from:
                    continue
                job = repo.get_job(novel_id=novel_id, chapter_id=cid, stage="s1")
                if job is None:
                    continue
                if (
                    job["status"] != "done"
                    or job["input_hash"] != ch["sha256"]
                    or job["prompt_version"] != current_prompt_version
                ):
                    stale_set.add(cid)
            only_chapters = stale_set

        return run_s1(
            repo,
            novel_id=novel_id,
            config=config,
            chapter_from=chapter_from,
            chapter_to=chapter_to,
            only_chapters=only_chapters,
            model=model,
            prompts_dir=prompts_dir,
        )

    if stage == "s2":
        if rebuild:
            repo.clear_ledger(novel_id=novel_id, stage="s3")
        return run_s2(
            repo,
            novel_id=novel_id,
            config=config,
            chapter_to=chapter_to,
            model=model,
            prompts_dir=prompts_dir,
            rebuild=rebuild,
        )

    if stage == "s3":
        only_chapters = None
        if stale_only:
            chapters = repo.list_chapters(novel_id=novel_id, chapter_max=chapter_to)
            extractions = repo.list_extractions(novel_id=novel_id, chapter_max=chapter_to)
            extractions_by_id = {row["chapter_id"]: row for row in extractions}

            stale_set = set()
            for ch in chapters:
                cid = ch["chapter_id"]
                if cid < chapter_from:
                    continue
                s3_job = repo.get_job(novel_id=novel_id, chapter_id=cid, stage="s3")
                if s3_job is None:
                    continue
                if s3_job["status"] != "done":
                    stale_set.add(cid)
                    continue
                if cid not in extractions_by_id:
                    stale_set.add(cid)
                    continue
                s2_job = repo.get_job(novel_id=novel_id, chapter_id=cid, stage="s2")
                if s2_job is None or s2_job["status"] != "done":
                    stale_set.add(cid)
                    continue
                ext_payload = extractions_by_id[cid]["payload"]
                s2_input_hash = s2_job["input_hash"]
                current_input_hash = compute_input_hash(ext_payload, s2_input_hash)
                if s3_job["input_hash"] != current_input_hash:
                    stale_set.add(cid)
            only_chapters = stale_set

        return run_s3(
            repo,
            novel_id=novel_id,
            config=config,
            chapter_from=chapter_from,
            chapter_to=chapter_to,
            only_chapters=only_chapters,
        )

    raise RunRequestError(f"Unhandled stage: {stage}")
