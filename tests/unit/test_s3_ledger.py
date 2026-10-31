r"""Unit tests for stage S3 ledger pipeline and repository ledger methods."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from typing import Any
import pytest

from xn.pipeline.s3_ledger import (
    ID_MULTIPLIER,
    S2_STAGE,
    STAGE,
    S3ResolutionError,
    S3Result,
    build_chapter_events,
    run_s3,
)
from xn.schemas.extraction import ChapterExtraction, EntityEvent
from xn.schemas.lifecycle import EntityLifecycleRecord, LifecycleEventType
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository
from xn.text import normalize_alias

SAMPLE_SUMMARY = (
    "Hàn Lập tiến vào Thất Huyền Môn, bắt đầu con đường tu tiên gian nan nhưng đầy kiên định. "
    "Trải qua nhiều thử thách tại Thất Huyền Môn, hắn dần tích lũy kinh nghiệm, tìm kiếm cơ duyên "
    "và chuẩn bị cho những bước tiến xa hơn trên con đường tu tiên đầy hiểm nguy phía trước."
)


@pytest.fixture
def db_conn(tmp_path: Path):
    r"""Provide an initialized SQLite database connection."""
    db_file = tmp_path / "test_s3_ledger.db"
    conn = connect(db_file)
    init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def repo(db_conn: sqlite3.Connection):
    r"""Provide a Repository backed by db_conn."""
    return Repository(db_conn)


def make_config(**overrides: Any) -> dict[str, Any]:
    r"""Helper to construct novel configuration dictionary."""
    cfg: dict[str, Any] = {
        "novel_id": "nov1",
        "title": "Novel Test Title",
        "author": "Test Author",
        "genre": "xianxia",
        "total_chapters": 5,
        "categories": [
            "character",
            "weapon_treasure",
            "puppet_beast",
            "technique",
            "sect_faction",
        ],
        "event_types": {
            "ACQUISITION": "Nhận được thực thể",
            "UPGRADE": "Thăng cấp thực thể",
            "USED_IN_BATTLE": "Sử dụng trong chiến đấu",
            "REVEALED_IDENTITY": "Tiết lộ thân phận",
            "MENTIONED": "Nhắc tới",
        },
        "realm_ladder": ["Mortal", "Qi Condensation", "Foundation Establishment", "Golden Core"],
    }
    cfg.update(overrides)
    return cfg


def make_extraction(
    chapter_id: int = 1,
    events: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> ChapterExtraction:
    r"""Helper to construct ChapterExtraction instance."""
    if events is None:
        events = [
            {
                "name": "Hero One",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": "Hero One",
                "tier_or_realm": "Qi Condensation",
                "same_as": None,
                "description": "Hero One acquires a mysterious jade slip.",
                "evidence": "Hero One holds the jade slip",
            }
        ]
    event_objs = [EntityEvent(**ev) for ev in events]
    data: dict[str, Any] = {
        "chapter_id": chapter_id,
        "map_location": "Seven Mysteries Sect",
        "location_change": False,
        "mc_realm": "Qi Condensation",
        "realm_breakthrough": False,
        "major_conflict": False,
        "time_skip_years": 0.0,
        "micro_summary": SAMPLE_SUMMARY,
        "events": event_objs,
    }
    data.update(overrides)
    return ChapterExtraction(**data)


def seed_novel(
    repo: Repository,
    *,
    novel_id: str = "nov1",
    total_chapters: int = 5,
) -> None:
    r"""Ensure the novel row exists."""
    if repo.get_novel(novel_id=novel_id) is None:
        repo.add_novel(
            novel_id=novel_id,
            title=f"Title {novel_id}",
            genre_profile="xianxia",
            total_chapters=total_chapters,
        )


def seed_chapter_and_extraction(
    repo: Repository,
    *,
    novel_id: str = "nov1",
    chapter_id: int = 1,
    extraction: ChapterExtraction | None = None,
) -> tuple[str, str]:
    r"""Seed chapter, extraction, and return (clean_text_hash, extraction_payload_json)."""
    seed_novel(repo, novel_id=novel_id)
    clean_text = f"Chapter {chapter_id} text content for novel {novel_id}."
    sha256 = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
    repo.save_chapter(
        novel_id=novel_id,
        chapter_id=chapter_id,
        title=f"Chapter {chapter_id}",
        clean_text=clean_text,
        sha256=sha256,
        token_count=50,
        stage="s0",
        input_hash=sha256,
    )
    if extraction is None:
        extraction = make_extraction(chapter_id=chapter_id)
    payload_str = extraction.model_dump_json()
    repo.save_extraction(
        novel_id=novel_id,
        chapter_id=chapter_id,
        payload=payload_str,
        prompt_version="ext_v1",
        model="test_extractor",
        stage="s1",
        input_hash=sha256,
    )
    return sha256, payload_str


def seed_s2_job(
    repo: Repository,
    *,
    novel_id: str = "nov1",
    chapter_id: int = 1,
    status: str = "done",
    input_hash: str | None = None,
) -> str:
    r"""Seed S2 job row and return the input_hash."""
    s2_hash = input_hash or f"s2_hash_ch{chapter_id}"
    repo.upsert_job(
        novel_id=novel_id,
        chapter_id=chapter_id,
        stage=S2_STAGE,
        status=status,
        input_hash=s2_hash,
        prompt_version="s2_v1",
        model="test_resolver",
    )
    return s2_hash


def seed_entity(
    conn: sqlite3.Connection,
    *,
    novel_id: str = "nov1",
    entity_id: int,
    name: str,
    category: str,
    first_chapter: int = 1,
    aliases: list[str] | None = None,
) -> None:
    r"""Seed entity and alias rows directly into SQLite."""
    conn.execute(
        "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) "
        "VALUES (?, ?, ?, ?, ?)",
        (novel_id, entity_id, name, category, first_chapter),
    )
    alias_list = aliases or [name]
    for a in alias_list:
        conn.execute(
            "INSERT INTO entity_aliases (novel_id, alias_norm, entity_id, first_chapter, source) "
            "VALUES (?, ?, ?, ?, 'seed')",
            (novel_id, normalize_alias(a), entity_id, first_chapter),
        )
    conn.commit()


# --- Acceptance Criteria 1 ---
def test_ac1_rows_written_with_fields_and_id(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 1: Rows are written with the right entity_id, event_type, holder_entity_id,

    tier_or_realm, realm_rank, description, evidence, and non-NULL id = chapter_id * 1000 + position.
    """
    cfg = make_config(total_chapters=1)
    seed_novel(repo, novel_id="nov1", total_chapters=1)
    seed_entity(db_conn, novel_id="nov1", entity_id=10, name="Hero One", category="character")
    seed_entity(db_conn, novel_id="nov1", entity_id=20, name="Mystic Sword", category="weapon_treasure")

    ext = make_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Hero One",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": "Hero One",
                "tier_or_realm": "Qi Condensation",
                "same_as": None,
                "description": "Hero One acquires the sword",
                "evidence": "found sword in cave",
            },
            {
                "name": "Mystic Sword",
                "category": "weapon_treasure",
                "event_type": "USED_IN_BATTLE",
                "holder": "Hero One",
                "tier_or_realm": "Spirit Tool",
                "same_as": None,
                "description": "Sword used in duel",
                "evidence": "struck the demon",
            },
        ],
    )
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=1, extraction=ext)
    seed_s2_job(repo, novel_id="nov1", chapter_id=1)

    result = run_s3(repo, novel_id="nov1", config=cfg)
    assert result == S3Result(processed=1, written=1, skipped=0, failed=0, stopped_at=None)

    rows = db_conn.execute(
        "SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 1 ORDER BY id"
    ).fetchall()
    assert len(rows) == 2

    row0 = rows[0]
    assert row0["id"] == 1 * ID_MULTIPLIER + 0
    assert row0["chapter_id"] == 1
    assert row0["entity_id"] == 10
    assert row0["event_type"] == "ACQUISITION"
    assert row0["ordinal"] == 0
    assert row0["holder_entity_id"] == 10
    assert row0["tier_or_realm"] == "Qi Condensation"
    assert row0["realm_rank"] == 2  # 2nd entry in ["Mortal", "Qi Condensation", ...]
    assert row0["description"] == "Hero One acquires the sword"
    assert row0["evidence"] == "found sword in cave"

    row1 = rows[1]
    assert row1["id"] == 1 * ID_MULTIPLIER + 1
    assert row1["chapter_id"] == 1
    assert row1["entity_id"] == 20
    assert row1["event_type"] == "USED_IN_BATTLE"
    assert row1["ordinal"] == 0
    assert row1["holder_entity_id"] == 10
    assert row1["tier_or_realm"] == "Spirit Tool"
    assert row1["realm_rank"] is None  # "Spirit Tool" not in realm_ladder
    assert row1["description"] == "Sword used in duel"
    assert row1["evidence"] == "struck the demon"


# --- Acceptance Criteria 2 ---
def test_ac2_ordinals_per_entity_and_type(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 2: Two events with same entity and type get 0 and 1; different type restarts at 0."""
    cfg = make_config(total_chapters=1)
    seed_novel(repo, novel_id="nov1", total_chapters=1)
    seed_entity(db_conn, novel_id="nov1", entity_id=10, name="Hero One", category="character")

    ext = make_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Hero One",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": None,
                "same_as": None,
                "description": "First acquisition",
                "evidence": "ev1",
            },
            {
                "name": "Hero One",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": None,
                "same_as": None,
                "description": "Second acquisition",
                "evidence": "ev2",
            },
            {
                "name": "Hero One",
                "category": "character",
                "event_type": "UPGRADE",
                "holder": None,
                "tier_or_realm": None,
                "same_as": None,
                "description": "First upgrade",
                "evidence": "ev3",
            },
        ],
    )
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=1, extraction=ext)
    seed_s2_job(repo, novel_id="nov1", chapter_id=1)

    result = run_s3(repo, novel_id="nov1", config=cfg)
    assert result.written == 1

    rows = db_conn.execute(
        "SELECT event_type, ordinal, description FROM entity_events WHERE novel_id = 'nov1' ORDER BY id"
    ).fetchall()
    assert len(rows) == 3
    assert (rows[0]["event_type"], rows[0]["ordinal"]) == ("ACQUISITION", 0)
    assert (rows[1]["event_type"], rows[1]["ordinal"]) == ("ACQUISITION", 1)
    assert (rows[2]["event_type"], rows[2]["ordinal"]) == ("UPGRADE", 0)


# --- Acceptance Criteria 3 ---
def test_ac3_revealed_identity_writes_one_row_on_name_entity(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 3: A REVEALED_IDENTITY event with same_as writes one row, on the name entity."""
    cfg = make_config(total_chapters=1)
    seed_novel(repo, novel_id="nov1", total_chapters=1)
    seed_entity(db_conn, novel_id="nov1", entity_id=10, name="Hero One", category="character")
    seed_entity(db_conn, novel_id="nov1", entity_id=11, name="Masked Figure", category="character")

    ext = make_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Hero One",
                "category": "character",
                "event_type": "REVEALED_IDENTITY",
                "holder": None,
                "tier_or_realm": "Qi Condensation",
                "same_as": "Masked Figure",
                "description": "Hero One reveals he was Masked Figure all along",
                "evidence": "took off the mask",
            }
        ],
    )
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=1, extraction=ext)
    seed_s2_job(repo, novel_id="nov1", chapter_id=1)

    result = run_s3(repo, novel_id="nov1", config=cfg)
    assert result.written == 1

    rows = db_conn.execute(
        "SELECT entity_id, event_type, description FROM entity_events WHERE novel_id = 'nov1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["entity_id"] == 10
    assert rows[0]["event_type"] == "REVEALED_IDENTITY"
    assert "Masked Figure" in rows[0]["description"]


# --- Acceptance Criteria 4 ---
def test_ac4_resolution_failures_fail_job_and_preserve_earlier_rows(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 4: Zero matching entities, two matching entities, and unresolvable holder each fail the chapter,

    leave earlier ledger rows, and write no partial rows.
    """
    cfg = make_config(total_chapters=1)
    seed_novel(repo, novel_id="nov1", total_chapters=1)
    seed_entity(db_conn, novel_id="nov1", entity_id=10, name="Hero One", category="character")
    seed_entity(db_conn, novel_id="nov1", entity_id=11, name="Twin Brother", category="character", aliases=["duplicate_alias"])
    seed_entity(db_conn, novel_id="nov1", entity_id=12, name="Twin Sister", category="character", aliases=["duplicate_alias"])

    # 1. First write a valid chapter so earlier ledger rows exist
    valid_ext = make_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Hero One",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": None,
                "same_as": None,
                "description": "Original valid event",
                "evidence": "orig_ev",
            }
        ],
    )
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=1, extraction=valid_ext)
    seed_s2_job(repo, novel_id="nov1", chapter_id=1, input_hash="hash_v1")
    res_init = run_s3(repo, novel_id="nov1", config=cfg)
    assert res_init.written == 1

    orig_rows = db_conn.execute(
        "SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 1"
    ).fetchall()
    assert len(orig_rows) == 1

    # Case A: Name matches zero entities
    ext_zero = make_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Ghost Entity",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": None,
                "same_as": None,
                "description": "Nonexistent entity",
                "evidence": "none",
            }
        ],
    )
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=1, extraction=ext_zero)
    seed_s2_job(repo, novel_id="nov1", chapter_id=1, input_hash="hash_zero")

    res_a = run_s3(repo, novel_id="nov1", config=cfg)
    assert res_a.failed == 1
    assert res_a.stopped_at == 1

    job_a = repo.get_job(novel_id="nov1", chapter_id=1, stage=STAGE)
    assert job_a is not None
    assert job_a["status"] == "failed"
    assert "Ghost Entity" in (job_a["error"] or "")

    # Previous rows still survive, no partial rows
    rows_a = db_conn.execute(
        "SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 1"
    ).fetchall()
    assert len(rows_a) == 1
    assert rows_a[0]["description"] == "Original valid event"

    # Case B: Name matches two entities
    ext_dup = make_extraction(
        chapter_id=1,
        events=[
            {
                "name": "duplicate_alias",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": None,
                "same_as": None,
                "description": "Ambiguous entity",
                "evidence": "dup",
            }
        ],
    )
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=1, extraction=ext_dup)
    seed_s2_job(repo, novel_id="nov1", chapter_id=1, input_hash="hash_dup")

    res_b = run_s3(repo, novel_id="nov1", config=cfg)
    assert res_b.failed == 1
    assert res_b.stopped_at == 1

    job_b = repo.get_job(novel_id="nov1", chapter_id=1, stage=STAGE)
    assert job_b is not None
    assert job_b["status"] == "failed"
    assert "duplicate_alias" in (job_b["error"] or "")

    # Earlier rows still intact
    rows_b = db_conn.execute(
        "SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 1"
    ).fetchall()
    assert len(rows_b) == 1
    assert rows_b[0]["description"] == "Original valid event"

    # Case C: Unresolvable holder
    ext_bad_holder = make_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Hero One",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": "Unregistered Holder",
                "tier_or_realm": None,
                "same_as": None,
                "description": "Bad holder event",
                "evidence": "holder_fail",
            }
        ],
    )
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=1, extraction=ext_bad_holder)
    seed_s2_job(repo, novel_id="nov1", chapter_id=1, input_hash="hash_bad_holder")

    res_c = run_s3(repo, novel_id="nov1", config=cfg)
    assert res_c.failed == 1
    assert res_c.stopped_at == 1

    job_c = repo.get_job(novel_id="nov1", chapter_id=1, stage=STAGE)
    assert job_c is not None
    assert job_c["status"] == "failed"
    assert "Unregistered Holder" in (job_c["error"] or "")

    # Earlier rows still intact
    rows_c = db_conn.execute(
        "SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 1"
    ).fetchall()
    assert len(rows_c) == 1
    assert rows_c[0]["description"] == "Original valid event"


# --- Acceptance Criteria 5 ---
def test_ac5_empty_normalized_name_skipped_rest_written(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 5: An empty normalized name is skipped and the rest of the chapter is written."""
    cfg = make_config(total_chapters=1)
    seed_novel(repo, novel_id="nov1", total_chapters=1)
    seed_entity(db_conn, novel_id="nov1", entity_id=10, name="Hero One", category="character")

    ext = make_extraction(
        chapter_id=1,
        events=[
            {
                "name": "   ... !?",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": None,
                "same_as": None,
                "description": "Should be skipped because normalized name is empty",
                "evidence": "skip",
            },
            {
                "name": "Hero One",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": None,
                "same_as": None,
                "description": "Should be written as first event",
                "evidence": "written",
            },
        ],
    )
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=1, extraction=ext)
    seed_s2_job(repo, novel_id="nov1", chapter_id=1)

    result = run_s3(repo, novel_id="nov1", config=cfg)
    assert result.written == 1
    assert result.failed == 0

    rows = db_conn.execute(
        "SELECT id, entity_id, description FROM entity_events WHERE novel_id = 'nov1'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == 1000  # Position 0 among events actually written
    assert rows[0]["entity_id"] == 10
    assert rows[0]["description"] == "Should be written as first event"


# --- Acceptance Criteria 6 ---
def test_ac6_missing_or_not_done_s2_job_stops_run(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 6: A chapter whose S2 job is missing or not done stops the run without a job row,

    and later chapters are not written.
    """
    cfg = make_config(total_chapters=3)
    seed_novel(repo, novel_id="nov1", total_chapters=3)
    seed_entity(db_conn, novel_id="nov1", entity_id=10, name="Hero One", category="character")

    # Chapter 1: S2 done
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=1)
    seed_s2_job(repo, novel_id="nov1", chapter_id=1, status="done")

    # Chapter 2: S2 job missing or running (not done)
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=2)
    repo.upsert_job(novel_id="nov1", chapter_id=2, stage=S2_STAGE, status="running")

    # Chapter 3: S2 done
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=3)
    seed_s2_job(repo, novel_id="nov1", chapter_id=3, status="done")

    result = run_s3(repo, novel_id="nov1", config=cfg)
    assert result.stopped_at == 2
    assert result.written == 1

    # Chapter 1 has S3 done job and event rows
    assert repo.get_job(novel_id="nov1", chapter_id=1, stage=STAGE) is not None
    assert len(db_conn.execute("SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 1").fetchall()) == 1

    # Chapter 2 has NO job row for stage S3
    assert repo.get_job(novel_id="nov1", chapter_id=2, stage=STAGE) is None
    assert len(db_conn.execute("SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 2").fetchall()) == 0

    # Chapter 3 was not touched
    assert repo.get_job(novel_id="nov1", chapter_id=3, stage=STAGE) is None
    assert len(db_conn.execute("SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 3").fetchall()) == 0


# --- Acceptance Criteria 7 ---
def test_ac7_second_run_changes_no_rows_and_skips(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 7: A second run changes no rows (full dump comparison of entity_events and s3 jobs

    including updated_at and attempts) and reports them as skipped.
    """
    cfg = make_config(total_chapters=2)
    seed_novel(repo, novel_id="nov1", total_chapters=2)
    seed_entity(db_conn, novel_id="nov1", entity_id=10, name="Hero One", category="character")

    for cid in (1, 2):
        seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=cid)
        seed_s2_job(repo, novel_id="nov1", chapter_id=cid)

    # First run
    res1 = run_s3(repo, novel_id="nov1", config=cfg)
    assert res1.written == 2
    assert res1.skipped == 0

    # Dump entity_events and jobs
    cur = db_conn.cursor()
    events_dump1 = cur.execute(
        "SELECT * FROM entity_events WHERE novel_id = 'nov1' ORDER BY id"
    ).fetchall()
    jobs_dump1 = cur.execute(
        "SELECT * FROM jobs WHERE novel_id = 'nov1' AND stage = 's3' ORDER BY chapter_id"
    ).fetchall()

    # Second run
    res2 = run_s3(repo, novel_id="nov1", config=cfg)
    assert res2 == S3Result(processed=0, written=0, skipped=2, failed=0, stopped_at=None)

    events_dump2 = cur.execute(
        "SELECT * FROM entity_events WHERE novel_id = 'nov1' ORDER BY id"
    ).fetchall()
    jobs_dump2 = cur.execute(
        "SELECT * FROM jobs WHERE novel_id = 'nov1' AND stage = 's3' ORDER BY chapter_id"
    ).fetchall()

    # Compare exact tuples including updated_at and attempts
    assert [tuple(r) for r in events_dump1] == [tuple(r) for r in events_dump2]
    assert [tuple(r) for r in jobs_dump1] == [tuple(r) for r in jobs_dump2]


# --- Acceptance Criteria 8 ---
def test_ac8_extraction_payload_or_s2_hash_change_rewrites_that_chapter_only(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 8: Changing one chapter extraction payload rewrites that chapter only;

    changing one chapter S2 job input_hash does too. Other chapters' rows and jobs stay byte-identical.
    """
    cfg = make_config(total_chapters=3)
    seed_novel(repo, novel_id="nov1", total_chapters=3)
    seed_entity(db_conn, novel_id="nov1", entity_id=10, name="Hero One", category="character")

    for cid in (1, 2, 3):
        seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=cid)
        seed_s2_job(repo, novel_id="nov1", chapter_id=cid)

    res_init = run_s3(repo, novel_id="nov1", config=cfg)
    assert res_init.written == 3

    cur = db_conn.cursor()
    ch1_events_orig = [
        tuple(r) for r in cur.execute("SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 1").fetchall()
    ]
    ch1_job_orig = tuple(cur.execute("SELECT * FROM jobs WHERE novel_id = 'nov1' AND chapter_id = 1 AND stage = 's3'").fetchone())

    ch3_events_orig = [
        tuple(r) for r in cur.execute("SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 3").fetchall()
    ]
    ch3_job_orig = tuple(cur.execute("SELECT * FROM jobs WHERE novel_id = 'nov1' AND chapter_id = 3 AND stage = 's3'").fetchone())

    # Part A: Update Chapter 2 extraction payload
    new_ext2 = make_extraction(
        chapter_id=2,
        events=[
            {
                "name": "Hero One",
                "category": "character",
                "event_type": "UPGRADE",
                "holder": None,
                "tier_or_realm": "Foundation Establishment",
                "same_as": None,
                "description": "Hero One reached Foundation Establishment in Ch 2",
                "evidence": "breakthrough",
            }
        ],
    )
    repo.save_extraction(
        novel_id="nov1",
        chapter_id=2,
        payload=new_ext2.model_dump_json(),
        prompt_version="ext_v1",
        model="test_extractor",
        stage="s1",
        input_hash="new_ext2_hash",
    )

    res_rewrite1 = run_s3(repo, novel_id="nov1", config=cfg)
    assert res_rewrite1 == S3Result(processed=1, written=1, skipped=2, failed=0, stopped_at=None)

    # Check Ch1 and Ch3 stay byte-identical
    ch1_events_now = [
        tuple(r) for r in cur.execute("SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 1").fetchall()
    ]
    ch1_job_now = tuple(cur.execute("SELECT * FROM jobs WHERE novel_id = 'nov1' AND chapter_id = 1 AND stage = 's3'").fetchone())
    assert ch1_events_now == ch1_events_orig
    assert ch1_job_now == ch1_job_orig

    ch3_events_now = [
        tuple(r) for r in cur.execute("SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 3").fetchall()
    ]
    ch3_job_now = tuple(cur.execute("SELECT * FROM jobs WHERE novel_id = 'nov1' AND chapter_id = 3 AND stage = 's3'").fetchone())
    assert ch3_events_now == ch3_events_orig
    assert ch3_job_now == ch3_job_orig

    # Verify Chapter 2 was rewritten
    ch2_events = cur.execute("SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 2").fetchall()
    assert len(ch2_events) == 1
    assert ch2_events[0]["event_type"] == "UPGRADE"
    assert ch2_events[0]["description"] == "Hero One reached Foundation Establishment in Ch 2"

    # Capture Chapter 2 job before Part B
    ch2_job_before_b = tuple(cur.execute("SELECT * FROM jobs WHERE novel_id = 'nov1' AND chapter_id = 2 AND stage = 's3'").fetchone())

    # Part B: Update Chapter 2 S2 job input_hash
    repo.upsert_job(
        novel_id="nov1",
        chapter_id=2,
        stage=S2_STAGE,
        status="done",
        input_hash="new_s2_hash_for_ch2",
    )

    res_rewrite2 = run_s3(repo, novel_id="nov1", config=cfg)
    assert res_rewrite2 == S3Result(processed=1, written=1, skipped=2, failed=0, stopped_at=None)

    # Ch1 and Ch3 remain byte-identical
    assert [tuple(r) for r in cur.execute("SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 1").fetchall()] == ch1_events_orig
    assert tuple(cur.execute("SELECT * FROM jobs WHERE novel_id = 'nov1' AND chapter_id = 1 AND stage = 's3'").fetchone()) == ch1_job_orig
    assert [tuple(r) for r in cur.execute("SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 3").fetchall()] == ch3_events_orig
    assert tuple(cur.execute("SELECT * FROM jobs WHERE novel_id = 'nov1' AND chapter_id = 3 AND stage = 's3'").fetchone()) == ch3_job_orig

    # Chapter 2 job input_hash changed
    ch2_job_after_b = tuple(cur.execute("SELECT * FROM jobs WHERE novel_id = 'nov1' AND chapter_id = 2 AND stage = 's3'").fetchone())
    assert ch2_job_after_b != ch2_job_before_b


# --- Acceptance Criteria 9 ---
def test_ac9_chapter_with_zero_events_gets_done_job_and_no_rows(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 9: A chapter with zero events gets a 'done' job and no rows."""
    cfg = make_config(total_chapters=1)
    seed_novel(repo, novel_id="nov1", total_chapters=1)

    ext = make_extraction(chapter_id=1, events=[])
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=1, extraction=ext)
    seed_s2_job(repo, novel_id="nov1", chapter_id=1)

    result = run_s3(repo, novel_id="nov1", config=cfg)
    assert result == S3Result(processed=1, written=1, skipped=0, failed=0, stopped_at=None)

    job = repo.get_job(novel_id="nov1", chapter_id=1, stage=STAGE)
    assert job is not None
    assert job["status"] == "done"

    rows = db_conn.execute("SELECT * FROM entity_events WHERE novel_id = 'nov1' AND chapter_id = 1").fetchall()
    assert len(rows) == 0


# --- Acceptance Criteria 10 ---
def test_ac10_only_chapters_and_chapter_from_limit(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 10: only_chapters and chapter_from limit which chapters are touched."""
    cfg = make_config(total_chapters=4)
    seed_novel(repo, novel_id="nov1", total_chapters=4)
    seed_entity(db_conn, novel_id="nov1", entity_id=10, name="Hero One", category="character")

    for cid in (1, 2, 3, 4):
        seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=cid)
        seed_s2_job(repo, novel_id="nov1", chapter_id=cid)

    # Run with chapter_from=2, chapter_to=3: chapters 1 and 4 must not be touched
    res = run_s3(repo, novel_id="nov1", config=cfg, chapter_from=2, chapter_to=3)
    assert res.written == 2
    assert res.processed == 2

    assert repo.get_job(novel_id="nov1", chapter_id=1, stage=STAGE) is None
    assert repo.get_job(novel_id="nov1", chapter_id=2, stage=STAGE) is not None
    assert repo.get_job(novel_id="nov1", chapter_id=3, stage=STAGE) is not None
    assert repo.get_job(novel_id="nov1", chapter_id=4, stage=STAGE) is None

    # Test only_chapters: only chapter 4
    res_only = run_s3(repo, novel_id="nov1", config=cfg, only_chapters={4})
    assert res_only.written == 1
    assert res_only.processed == 1
    assert res_only.skipped == 0

    assert repo.get_job(novel_id="nov1", chapter_id=4, stage=STAGE) is not None


# --- Acceptance Criteria 11 ---
def test_ac11_alias_first_chapter_after_event_fails_resolution(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 11: An alias whose first_chapter is after the event chapter is not matched."""
    cfg = make_config(total_chapters=2)
    seed_novel(repo, novel_id="nov1", total_chapters=2)

    # Seed Hero One alias with first_chapter = 2
    seed_entity(
        db_conn,
        novel_id="nov1",
        entity_id=10,
        name="Hero One",
        category="character",
        first_chapter=2,
    )

    # Chapter 1 tries to use Hero One
    ext = make_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Hero One",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": None,
                "same_as": None,
                "description": "Hero appears in chapter 1 before alias first_chapter",
                "evidence": "ch1 ev",
            }
        ],
    )
    seed_chapter_and_extraction(repo, novel_id="nov1", chapter_id=1, extraction=ext)
    seed_s2_job(repo, novel_id="nov1", chapter_id=1)

    result = run_s3(repo, novel_id="nov1", config=cfg)
    assert result.failed == 1
    assert result.stopped_at == 1

    job = repo.get_job(novel_id="nov1", chapter_id=1, stage=STAGE)
    assert job is not None
    assert job["status"] == "failed"
    assert "Hero One" in (job["error"] or "")


# --- Acceptance Criteria 12 ---
def test_ac12_multi_novel_tenant_isolation_and_clear_ledger(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""AC 12: A row from another novel_id is untouched by save_ledger and clear_ledger,

    and clear_ledger removes this novel's events and s3 jobs only, so that clear_resolution then succeeds.
    """
    cfg1 = make_config(novel_id="nov_1", total_chapters=2)
    cfg2 = make_config(novel_id="nov_2", total_chapters=2)
    seed_novel(repo, novel_id="nov_1", total_chapters=2)
    seed_novel(repo, novel_id="nov_2", total_chapters=2)

    seed_entity(db_conn, novel_id="nov_1", entity_id=10, name="Hero 1", category="character")
    seed_entity(db_conn, novel_id="nov_2", entity_id=20, name="Hero 2", category="character")

    for n, e_name in [("nov_1", "Hero 1"), ("nov_2", "Hero 2")]:
        ext = make_extraction(
            chapter_id=1,
            events=[
                {
                    "name": e_name,
                    "category": "character",
                    "event_type": "ACQUISITION",
                    "holder": None,
                    "tier_or_realm": None,
                    "same_as": None,
                    "description": f"Event for {n}",
                    "evidence": "ev",
                }
            ],
        )
        seed_chapter_and_extraction(repo, novel_id=n, chapter_id=1, extraction=ext)
        seed_s2_job(repo, novel_id=n, chapter_id=1)

    # Also add an S1 job for nov_1 to test that clear_ledger deletes stage=S3 jobs only
    repo.upsert_job(novel_id="nov_1", chapter_id=1, stage="s1", status="done")

    # Run S3 for both novels
    run_s3(repo, novel_id="nov_1", config=cfg1)
    run_s3(repo, novel_id="nov_2", config=cfg2)

    # Capture nov_2 events and jobs
    cur = db_conn.cursor()
    nov2_events_before = [
        tuple(r) for r in cur.execute("SELECT * FROM entity_events WHERE novel_id = 'nov_2'").fetchall()
    ]
    nov2_jobs_before = [
        tuple(r) for r in cur.execute("SELECT * FROM jobs WHERE novel_id = 'nov_2' AND stage = 's3'").fetchall()
    ]
    assert len(nov2_events_before) == 1
    assert len(nov2_jobs_before) == 1

    # Call save_ledger for nov_1
    repo.save_ledger(
        novel_id="nov_1",
        chapter_id=1,
        events=[
            {
                "id": 1000,
                "entity_id": 10,
                "event_type": "UPGRADE",
                "ordinal": 0,
                "description": "Manual save_ledger update",
            }
        ],
        stage=STAGE,
        input_hash="manual_hash",
    )

    # nov_2 rows are completely untouched by nov_1 save_ledger
    nov2_events_after_save = [
        tuple(r) for r in cur.execute("SELECT * FROM entity_events WHERE novel_id = 'nov_2'").fetchall()
    ]
    assert nov2_events_after_save == nov2_events_before

    # clear_resolution on nov_1 currently fails because entity_events has rows for nov_1
    with pytest.raises(ValueError, match="entity_events has rows for novel 'nov_1'"):
        repo.clear_resolution(novel_id="nov_1", stage=S2_STAGE)

    # Call clear_ledger on nov_1
    repo.clear_ledger(novel_id="nov_1", stage=STAGE)

    # nov_1 entity_events and s3 jobs are gone
    assert cur.execute("SELECT count(*) FROM entity_events WHERE novel_id = 'nov_1'").fetchone()[0] == 0
    assert repo.get_job(novel_id="nov_1", chapter_id=1, stage=STAGE) is None

    # nov_1 s1 and s2 jobs remain
    assert repo.get_job(novel_id="nov_1", chapter_id=1, stage="s1") is not None
    assert repo.get_job(novel_id="nov_1", chapter_id=1, stage=S2_STAGE) is not None

    # nov_2 entity_events and s3 jobs are completely untouched
    nov2_events_after_clear = [
        tuple(r) for r in cur.execute("SELECT * FROM entity_events WHERE novel_id = 'nov_2'").fetchall()
    ]
    nov2_jobs_after_clear = [
        tuple(r) for r in cur.execute("SELECT * FROM jobs WHERE novel_id = 'nov_2' AND stage = 's3'").fetchall()
    ]
    assert nov2_events_after_clear == nov2_events_before
    assert nov2_jobs_after_clear == nov2_jobs_before

    # Now clear_resolution on nov_1 succeeds
    repo.clear_resolution(novel_id="nov_1", stage=S2_STAGE)
    assert cur.execute("SELECT count(*) FROM entities WHERE novel_id = 'nov_1'").fetchone()[0] == 0


# --- Extra Edge Cases ---
def test_position_reaching_multiplier_raises_overflow_error(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""Verify build_chapter_events raises S3ResolutionError when position reaches ID_MULTIPLIER."""
    seed_novel(repo, novel_id="nov1", total_chapters=1)
    seed_entity(db_conn, novel_id="nov1", entity_id=10, name="Hero One", category="character")

    events = [
        EntityEvent.model_construct(
            name="Hero One",
            category="character",
            event_type=LifecycleEventType.ACQUISITION,
            holder=None,
            tier_or_realm=None,
            same_as=None,
            description=f"Event {i}",
            evidence="ev",
        )
        for i in range(ID_MULTIPLIER + 1)
    ]
    ext_overflow = ChapterExtraction.model_construct(
        chapter_id=1,
        events=events,
    )

    with pytest.raises(S3ResolutionError, match=r"reaches ID_MULTIPLIER"):
        build_chapter_events(
            repo,
            novel_id="nov1",
            extraction=ext_overflow,
            categories=["character"],
        )


def test_entity_lifecycle_record_model() -> None:
    r"""Verify EntityLifecycleRecord Pydantic model validation and serialization."""
    rec = EntityLifecycleRecord(
        novel_id="pntt1",
        id=1000,
        chapter_id=1,
        entity_id=10,
        event_type=LifecycleEventType.ACQUISITION,
        ordinal=0,
        holder_entity_id=10,
        tier_or_realm="Qi Condensation",
        realm_rank=1,
        description="Found treasure",
        evidence="evidence",
    )
    dump = rec.model_dump()
    assert dump["novel_id"] == "pntt1"
    assert dump["id"] == 1000
    assert dump["chapter_id"] == 1
    assert dump["entity_id"] == 10
    assert dump["event_type"] == LifecycleEventType.ACQUISITION
    assert dump["ordinal"] == 0
    assert dump["holder_entity_id"] == 10
    assert dump["tier_or_realm"] == "Qi Condensation"
    assert dump["realm_rank"] == 1
    assert dump["description"] == "Found treasure"
    assert dump["evidence"] == "evidence"
