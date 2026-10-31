r"""Stage S3: Idempotent ledger write pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import logging
import sqlite3
from typing import Any

from xn.prompts import profile_from_config
from xn.schemas.extraction import ChapterExtraction
from xn.schemas.lifecycle import EntityLifecycleRecord, LifecycleEventType
from xn.storage.repository import Repository
from xn.text import normalize_alias

logger = logging.getLogger(__name__)

STAGE = "s3"
S2_STAGE = "s2"
ID_MULTIPLIER = 1000


@dataclass(frozen=True)
class S3Result:
    r"""Result metrics for an S3 pipeline run."""

    processed: int
    written: int
    skipped: int
    failed: int
    stopped_at: int | None = None


class S3ResolutionError(RuntimeError):
    r"""Raised when an entity or holder alias cannot be uniquely resolved in S3."""

    name: str
    category: str
    chapter_id: int

    def __init__(
        self,
        name: str,
        category: str | None = None,
        chapter_id: int | None = None,
        message: str | None = None,
    ) -> None:
        if category is None and chapter_id is None:
            super().__init__(name)
            self.name = ""
            self.category = ""
            self.chapter_id = 0
        else:
            self.name = name
            self.category = category or ""
            self.chapter_id = chapter_id or 0
            if message is None:
                message = (
                    f"Resolution failed for '{name}' (category: {self.category}) "
                    f"in chapter {self.chapter_id}"
                )
            super().__init__(message)


def compute_input_hash(payload: str, s2_input_hash: str | None) -> str:
    r"""Compute S3 input hash from extraction payload and S2 job input_hash."""
    s2_hash = s2_input_hash or ""
    return hashlib.sha256((payload + "\0" + s2_hash).encode("utf-8")).hexdigest()


def build_chapter_events(
    repo: Repository,
    *,
    novel_id: str,
    extraction: Any,
    realm_ladder: list[str] | None = None,
    categories: list[str] | None = None,
) -> list[dict[str, Any]]:
    r"""Build and validate ledger event rows for a single chapter."""
    if isinstance(extraction, ChapterExtraction):
        chapter_id = extraction.chapter_id
        parsed = extraction
    elif isinstance(extraction, (dict, sqlite3.Row)) or hasattr(extraction, "__getitem__"):
        chapter_id = extraction["chapter_id"]
        payload = extraction["payload"]
        if isinstance(payload, str):
            parsed = ChapterExtraction.model_validate_json(payload)
        elif isinstance(payload, ChapterExtraction):
            parsed = payload
        elif isinstance(payload, dict):
            parsed = ChapterExtraction.model_validate(payload)
        else:
            parsed = ChapterExtraction.model_validate_json(str(payload))
    else:
        chapter_id = getattr(extraction, "chapter_id")
        payload = getattr(extraction, "payload")
        if isinstance(payload, str):
            parsed = ChapterExtraction.model_validate_json(payload)
        elif isinstance(payload, ChapterExtraction):
            parsed = payload
        else:
            parsed = ChapterExtraction.model_validate(payload)

    events_out: list[dict[str, Any]] = []
    ordinal_counts: dict[tuple[int, str], int] = {}
    position = 0

    allowed_categories = set(categories) if categories is not None else set()

    for ev in parsed.events:
        name_norm = normalize_alias(ev.name)
        if not name_norm:
            logger.warning(
                "Chapter %d: skipping event with empty normalized name %r",
                chapter_id,
                ev.name,
            )
            continue

        rows = repo.find_entities_by_alias(
            novel_id=novel_id,
            alias_norm=name_norm,
            category=ev.category,
            chapter_max=chapter_id,
        )
        if len(rows) != 1:
            raise S3ResolutionError(
                ev.name,
                ev.category,
                chapter_id,
                f"Expected exactly 1 entity for '{ev.name}' ({ev.category}) "
                f"in chapter {chapter_id}, found {len(rows)}",
            )
        entity_id = int(rows[0]["entity_id"])

        holder_entity_id: int | None = None
        if ev.holder and "character" in allowed_categories:
            holder_norm = normalize_alias(ev.holder)
            if holder_norm:
                holder_rows = repo.find_entities_by_alias(
                    novel_id=novel_id,
                    alias_norm=holder_norm,
                    category="character",
                    chapter_max=chapter_id,
                )
                if len(holder_rows) != 1:
                    raise S3ResolutionError(
                        ev.holder,
                        "character",
                        chapter_id,
                        f"Expected exactly 1 character entity for holder '{ev.holder}' "
                        f"in chapter {chapter_id}, found {len(holder_rows)}",
                    )
                holder_entity_id = int(holder_rows[0]["entity_id"])

        event_type = ev.event_type
        event_type_str = event_type.value if isinstance(event_type, Enum) else str(event_type)
        ord_key = (entity_id, event_type_str)
        ordinal = ordinal_counts.get(ord_key, 0)
        ordinal_counts[ord_key] = ordinal + 1

        if position >= ID_MULTIPLIER:
            raise S3ResolutionError(
                ev.name,
                ev.category,
                chapter_id,
                f"Event position {position} reaches ID_MULTIPLIER ({ID_MULTIPLIER}) "
                f"in chapter {chapter_id}",
            )
        event_id = chapter_id * ID_MULTIPLIER + position
        position += 1

        tier_or_realm = ev.tier_or_realm
        realm_rank: int | None = None
        if tier_or_realm is not None:
            norm_tier = normalize_alias(tier_or_realm)
            if norm_tier:
                for idx, ladder_item in enumerate(realm_ladder or [], start=1):
                    if ladder_item and normalize_alias(ladder_item) == norm_tier:
                        realm_rank = idx
                        break

        record = EntityLifecycleRecord(
            novel_id=novel_id,
            id=event_id,
            chapter_id=chapter_id,
            entity_id=entity_id,
            event_type=ev.event_type,
            ordinal=ordinal,
            holder_entity_id=holder_entity_id,
            tier_or_realm=tier_or_realm,
            realm_rank=realm_rank,
            description=ev.description,
            evidence=ev.evidence,
        )

        row_dict = record.model_dump()
        events_out.append(row_dict)

    return events_out


def run_s3(
    repo: Repository,
    *,
    novel_id: str,
    config: dict[str, Any],
    chapter_from: int = 1,
    chapter_to: int | None = None,
    only_chapters: set[int] | None = None,
) -> S3Result:
    r"""Run stage S3 ledger pipeline."""
    if chapter_to is None:
        chapter_to = config["total_chapters"]

    profile = profile_from_config(config)
    categories = list(profile.get("categories") or [])
    realm_ladder = list(profile.get("realm_ladder") or [])

    chapters = repo.list_chapters(novel_id=novel_id, chapter_max=chapter_to)
    extractions = repo.list_extractions(novel_id=novel_id, chapter_max=chapter_to)
    extractions_by_id = {row["chapter_id"]: row for row in extractions}

    chapters_to_handle = [
        ch
        for ch in chapters
        if ch["chapter_id"] >= chapter_from
        and (only_chapters is None or ch["chapter_id"] in only_chapters)
    ]
    chapters_to_handle.sort(key=lambda ch: ch["chapter_id"])

    processed = 0
    written = 0
    skipped = 0
    failed = 0
    stopped_at: int | None = None

    for ch in chapters_to_handle:
        cid = ch["chapter_id"]

        s2_job = repo.get_job(novel_id=novel_id, chapter_id=cid, stage=S2_STAGE)
        if s2_job is None or s2_job["status"] != "done" or cid not in extractions_by_id:
            logger.warning(
                "Chapter %d missing completed S2 job or extraction row; stopping S3", cid
            )
            stopped_at = cid
            break

        ext_row = extractions_by_id[cid]
        payload = ext_row["payload"]
        input_hash = compute_input_hash(payload, s2_job["input_hash"])

        s3_job = repo.get_job(novel_id=novel_id, chapter_id=cid, stage=STAGE)
        if (
            s3_job is not None
            and s3_job["status"] == "done"
            and s3_job["input_hash"] == input_hash
        ):
            skipped += 1
            continue

        processed += 1
        repo.upsert_job(
            novel_id=novel_id,
            chapter_id=cid,
            stage=STAGE,
            status="running",
            input_hash=input_hash,
        )

        try:
            events = build_chapter_events(
                repo,
                novel_id=novel_id,
                extraction=ext_row,
                realm_ladder=realm_ladder,
                categories=categories,
            )
            repo.save_ledger(
                novel_id=novel_id,
                chapter_id=cid,
                events=events,
                stage=STAGE,
                input_hash=input_hash,
            )
            written += 1
        except Exception as err:
            logger.error("Chapter %d ledger build failed: %s", cid, err)
            repo.upsert_job(
                novel_id=novel_id,
                chapter_id=cid,
                stage=STAGE,
                status="failed",
                input_hash=input_hash,
                error=str(err),
            )
            failed += 1
            stopped_at = cid
            break

    return S3Result(
        processed=processed,
        written=written,
        skipped=skipped,
        failed=failed,
        stopped_at=stopped_at,
    )
