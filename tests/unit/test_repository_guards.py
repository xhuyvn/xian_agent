"""Unit tests for SQLite schema and repository spoiler guards."""

from __future__ import annotations

import inspect
from pathlib import Path
import sqlite3
from typing import Any
import pytest

from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

EXPECTED_TABLES = [
    "novels",
    "chapters",
    "extractions",
    "entities",
    "entity_aliases",
    "entity_links",
    "entity_events",
    "arcs",
    "entity_arc_digests",
    "arc_prefix_summaries",
    "jobs",
    "llm_runs",
    "answer_cache",
    "lc_map_cache",
    "feedback",
]

STORY_READ_METHODS = [
    "list_chapters",
    "get_entity_events",
    "find_aliases",
    "get_identity_links",
    "list_arcs",
    "get_entity_arc_digests",
    "get_arc_prefix_summaries",
    "list_extractions",
    "list_aliases",
    "find_entities_by_alias",
]

OPERATIONAL_METHODS = [
    "add_novel",
    "get_novel",
    "set_reader_progress",
    "save_chapter",
    "save_extraction",
    "upsert_job",
    "get_job",
    "add_feedback",
    "add_llm_run",
    "next_entity_id",
    "save_resolution",
    "clear_resolution",
    "save_ledger",
    "clear_ledger",
]

EXEMPT_METHODS = [
    "list_novels",
]


@pytest.fixture
def db_conn(tmp_path: Path):
    """Provide an initialized SQLite connection and guarantee closure on teardown."""
    db_file = tmp_path / "test_repo.db"
    conn = connect(db_file)
    init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def repo(db_conn: sqlite3.Connection):
    """Provide a Repository instance backed by db_conn."""
    return Repository(db_conn)


def test_all_15_tables_exist_with_novel_id_column(db_conn: sqlite3.Connection) -> None:
    """Verify that all 15 named tables exist and each contains a novel_id column."""
    cur = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    existing_tables = {row[0] for row in cur.fetchall()}

    assert len(EXPECTED_TABLES) == 15
    for table_name in EXPECTED_TABLES:
        assert table_name in existing_tables, f"Expected table '{table_name}' was not created"
        info = db_conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        column_names = [col["name"] for col in info]
        assert "novel_id" in column_names, f"Table '{table_name}' missing 'novel_id' column"

    idx_cur = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_events_entity'"
    )
    assert idx_cur.fetchone() is not None, "Index 'ix_events_entity' missing"


def test_pragmas_and_foreign_keys_enforced(db_conn: sqlite3.Connection) -> None:
    """Verify journal_mode is WAL, foreign_keys is ON, and foreign key violations raise IntegrityError."""
    journal_mode = db_conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal_mode.lower() == "wal"

    foreign_keys = db_conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert foreign_keys == 1

    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute(
            "INSERT INTO chapters (novel_id, chapter_id, title, clean_text, sha256) "
            "VALUES (?, ?, ?, ?, ?)",
            ("nonexistent_novel", 1, "Ch 1", "Sample text", "sha_dummy"),
        )


def test_init_schema_can_run_twice(db_conn: sqlite3.Connection) -> None:
    """Verify init_schema is idempotent and can be safely executed multiple times."""
    init_schema(db_conn)
    init_schema(db_conn)
    cur = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    existing_tables = {row[0] for row in cur.fetchall()}
    assert set(EXPECTED_TABLES).issubset(existing_tables)


def test_public_methods_classified_by_inspect() -> None:
    """Verify by inspect that every public method belongs to exactly one category."""
    public_methods = [
        name for name, _ in inspect.getmembers(Repository, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    all_classified = STORY_READ_METHODS + OPERATIONAL_METHODS + EXEMPT_METHODS
    assert len(all_classified) == len(set(all_classified)), "Duplicate method in classification lists"

    assert set(public_methods) == set(all_classified), (
        f"Mismatch between Repository public methods and classification lists: "
        f"extra={set(public_methods) - set(all_classified)}, "
        f"missing={set(all_classified) - set(public_methods)}"
    )

    for method_name in public_methods:
        occurrences = sum([
            method_name in STORY_READ_METHODS,
            method_name in OPERATIONAL_METHODS,
            method_name in EXEMPT_METHODS,
        ])
        assert occurrences == 1, f"Method '{method_name}' must be classified in exactly 1 category"


def test_story_reads_parameter_guards(repo: Repository) -> None:
    """Verify story reads enforce required keyword novel_id and chapter_max with strict type checks."""
    story_dummy_kwargs: dict[str, dict[str, object]] = {
        "list_chapters": {},
        "get_entity_events": {"entity_id": 1},
        "find_aliases": {"alias_norm": "hl"},
        "get_identity_links": {"entity_id": 1},
        "list_arcs": {},
        "get_entity_arc_digests": {"entity_id": 1},
        "get_arc_prefix_summaries": {"arc_id": 1},
        "list_extractions": {},
        "list_aliases": {},
        "find_entities_by_alias": {"alias_norm": "hl", "category": "character"},
    }

    for method_name in STORY_READ_METHODS:
        fn = getattr(repo, method_name)
        sig = inspect.signature(fn)
        params = sig.parameters

        assert "novel_id" in params, f"{method_name} must have 'novel_id'"
        assert "chapter_max" in params, f"{method_name} must have 'chapter_max'"
        assert params["novel_id"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["chapter_max"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["novel_id"].default is inspect.Parameter.empty
        assert params["chapter_max"].default is inspect.Parameter.empty

        kwargs = story_dummy_kwargs[method_name]

        # Missing all arguments raises TypeError
        with pytest.raises(TypeError):
            fn()

        # Missing chapter_max raises TypeError
        with pytest.raises(TypeError):
            fn(novel_id="pntt1", **kwargs)

        # Missing novel_id raises TypeError
        with pytest.raises(TypeError):
            fn(chapter_max=100, **kwargs)

        # Positional arguments raise TypeError
        with pytest.raises(TypeError):
            fn("pntt1", 100)

        # chapter_max=None raises ValueError
        with pytest.raises(ValueError):
            fn(novel_id="pntt1", chapter_max=None, **kwargs)

        # chapter_max as bool raises ValueError
        with pytest.raises(ValueError):
            fn(novel_id="pntt1", chapter_max=True, **kwargs)

        with pytest.raises(ValueError):
            fn(novel_id="pntt1", chapter_max=False, **kwargs)

        # chapter_max not an int raises ValueError
        with pytest.raises(ValueError):
            fn(novel_id="pntt1", chapter_max="50", **kwargs)

        # novel_id empty or None raises ValueError
        with pytest.raises(ValueError):
            fn(novel_id="", chapter_max=50, **kwargs)

        with pytest.raises(ValueError):
            fn(novel_id=None, chapter_max=50, **kwargs)


def test_operational_parameter_guards(repo: Repository) -> None:
    """Verify operational methods require keyword novel_id and reject None or empty novel_id."""
    op_dummy_kwargs: dict[str, dict[str, object]] = {
        "add_novel": {"title": "Title", "genre_profile": "xianxia", "total_chapters": 100},
        "get_novel": {},
        "set_reader_progress": {"chapter": 10},
        "save_chapter": {
            "chapter_id": 1,
            "title": "Title",
            "clean_text": "Text",
            "sha256": "h",
            "token_count": 10,
            "stage": "s0",
            "input_hash": "h",
        },
        "save_extraction": {
            "chapter_id": 1,
            "payload": "{}",
            "prompt_version": "v1hash01",
            "model": "qwen14b-12k",
            "stage": "s1",
            "input_hash": "h",
        },
        "upsert_job": {"chapter_id": 1, "stage": "s1", "status": "pending"},
        "get_job": {"chapter_id": 1, "stage": "s1"},
        "add_feedback": {"run_id": "r1", "rating": 5},
        "add_llm_run": {
            "run_id": "r1",
            "stage": "s1",
            "chapter_id": 1,
            "model": "qwen14b-12k",
            "prompt_version": "v1hash01",
            "input_tokens": 100,
            "output_tokens": 50,
            "latency_ms": 120,
            "retries": 0,
            "ok": 1,
            "cost_usd": 0.0,
            "egress_class": "local",
            "payload_chapters": "1",
        },
        "next_entity_id": {},
        "save_resolution": {
            "chapter_id": 1,
            "entities": [],
            "entity_updates": [],
            "aliases": [],
            "links": [],
            "stage": "s2",
            "input_hash": "h",
            "prompt_version": "v1",
            "model": "m",
        },
        "clear_resolution": {"stage": "s2"},
        "save_ledger": {
            "chapter_id": 1,
            "events": [],
            "stage": "s3",
            "input_hash": "h",
        },
        "clear_ledger": {"stage": "s3"},
    }

    for method_name in OPERATIONAL_METHODS:
        fn = getattr(repo, method_name)
        sig = inspect.signature(fn)
        params = sig.parameters

        assert "novel_id" in params, f"{method_name} must have 'novel_id'"
        assert params["novel_id"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["novel_id"].default is inspect.Parameter.empty

        kwargs = op_dummy_kwargs[method_name]

        # Missing novel_id raises TypeError
        with pytest.raises(TypeError):
            fn()

        # Positional call raises TypeError
        with pytest.raises(TypeError):
            fn("novel_id")

        # novel_id=None raises ValueError
        with pytest.raises(ValueError):
            fn(novel_id=None, **kwargs)

        # novel_id="" raises ValueError
        with pytest.raises(ValueError):
            fn(novel_id="", **kwargs)


def test_story_reads_enforce_spoiler_bounds(repo: Repository, db_conn: sqlite3.Connection) -> None:
    """Verify that with rows on both sides of chapter_max, each story read returns only rows <= bound."""
    repo.add_novel(novel_id="pntt1", title="PNTT", genre_profile="xianxia", total_chapters=200)

    # 1. Chapters: 20, 50 (<= 50) and 80 (> 50)
    db_conn.execute(
        "INSERT INTO chapters (novel_id, chapter_id, title, clean_text, sha256) VALUES "
        "('pntt1', 20, 'Ch 20', 'text20', 'h20'), "
        "('pntt1', 50, 'Ch 50', 'text50', 'h50'), "
        "('pntt1', 80, 'Ch 80', 'text80', 'h80')"
    )

    # 2. Entity events: chapter 20, 50 (<= 50) and 80 (> 50), with ordering check
    db_conn.execute(
        "INSERT INTO entity_events (novel_id, id, chapter_id, entity_id, event_type, ordinal, description) VALUES "
        "('pntt1', 1, 50, 10, 'ACQUISITION', 1, 'Event 50'), "
        "('pntt1', 2, 20, 10, 'ACQUISITION', 2, 'Event 20 ord 2'), "
        "('pntt1', 3, 20, 10, 'ACQUISITION', 1, 'Event 20 ord 1'), "
        "('pntt1', 4, 80, 10, 'ACQUISITION', 1, 'Event 80')"
    )

    # 3. Entity aliases: first_chapter 30 (<= 50) and 70 (> 50)
    db_conn.execute(
        "INSERT INTO entity_aliases (novel_id, alias_norm, entity_id, first_chapter, source) VALUES "
        "('pntt1', 'han lap', 10, 30, 'exact'), "
        "('pntt1', 'han lap', 11, 70, 'exact')"
    )

    # 4. Entity links: revealed_chapter 25, 45 (<= 50) and 75 (> 50)
    db_conn.execute(
        "INSERT INTO entity_links (novel_id, entity_a, entity_b, revealed_chapter) VALUES "
        "('pntt1', 10, 20, 25), "
        "('pntt1', 30, 10, 45), "
        "('pntt1', 10, 40, 75)"
    )

    # 5. Arcs: end_chapter 40 (<= 50) and 60 (> 50)
    db_conn.execute(
        "INSERT INTO arcs (novel_id, arc_id, start_chapter, end_chapter) VALUES "
        "('pntt1', 1, 1, 40), "
        "('pntt1', 2, 41, 60)"
    )

    # 6. Entity arc digests: upto_chapter 40 (<= 50) and 60 (> 50)
    db_conn.execute(
        "INSERT INTO entity_arc_digests (novel_id, entity_id, arc_id, upto_chapter, digest) VALUES "
        "('pntt1', 10, 1, 40, 'Digest 40'), "
        "('pntt1', 10, 2, 60, 'Digest 60')"
    )

    # 7. Arc prefix summaries: upto_chapter 30 (<= 50) and 70 (> 50)
    db_conn.execute(
        "INSERT INTO arc_prefix_summaries (novel_id, arc_id, upto_chapter, summary) VALUES "
        "('pntt1', 1, 30, 'Summary 30'), "
        "('pntt1', 1, 70, 'Summary 70')"
    )
    db_conn.commit()

    bound = 50

    # Assert list_chapters
    chapters = repo.list_chapters(novel_id="pntt1", chapter_max=bound)
    assert [c["chapter_id"] for c in chapters] == [20, 50]

    # Assert get_entity_events (ordered by chapter_id, ordinal)
    events = repo.get_entity_events(novel_id="pntt1", entity_id=10, chapter_max=bound)
    assert [(e["chapter_id"], e["ordinal"]) for e in events] == [(20, 1), (20, 2), (50, 1)]

    # Assert find_aliases
    aliases = repo.find_aliases(novel_id="pntt1", alias_norm="han lap", chapter_max=bound)
    assert [a["first_chapter"] for a in aliases] == [30]

    # Assert get_identity_links (matching either entity_a or entity_b)
    links = repo.get_identity_links(novel_id="pntt1", entity_id=10, chapter_max=bound)
    assert [l["revealed_chapter"] for l in links] == [25, 45]

    # Assert list_arcs (end_chapter <= bound)
    arcs = repo.list_arcs(novel_id="pntt1", chapter_max=bound)
    assert [a["arc_id"] for a in arcs] == [1]

    # Assert get_entity_arc_digests
    digests = repo.get_entity_arc_digests(novel_id="pntt1", entity_id=10, chapter_max=bound)
    assert [d["upto_chapter"] for d in digests] == [40]

    # Assert get_arc_prefix_summaries
    prefix_sums = repo.get_arc_prefix_summaries(novel_id="pntt1", arc_id=1, chapter_max=bound)
    assert [s["upto_chapter"] for s in prefix_sums] == [30]


def test_novel_tenant_isolation(repo: Repository, db_conn: sqlite3.Connection) -> None:
    """Verify that with two novels, every read returns only the targeted novel's rows."""
    repo.add_novel(novel_id="nov_a", title="Novel A", genre_profile="xianxia", total_chapters=100)
    repo.add_novel(novel_id="nov_b", title="Novel B", genre_profile="xianxia", total_chapters=100)

    # Chapters
    db_conn.execute(
        "INSERT INTO chapters (novel_id, chapter_id, title, clean_text, sha256) VALUES "
        "('nov_a', 1, 'Ch A1', 'Text A1', 'shaA'), "
        "('nov_b', 1, 'Ch B1', 'Text B1', 'shaB')"
    )

    # Entity events
    db_conn.execute(
        "INSERT INTO entity_events (novel_id, id, chapter_id, entity_id, event_type, ordinal, description) VALUES "
        "('nov_a', 1, 1, 100, 'ACQUISITION', 1, 'Event A'), "
        "('nov_b', 1, 1, 100, 'ACQUISITION', 1, 'Event B')"
    )

    # Aliases
    db_conn.execute(
        "INSERT INTO entity_aliases (novel_id, alias_norm, entity_id, first_chapter, source) VALUES "
        "('nov_a', 'common_alias', 100, 1, 'exact'), "
        "('nov_b', 'common_alias', 100, 1, 'exact')"
    )

    # Links
    db_conn.execute(
        "INSERT INTO entity_links (novel_id, entity_a, entity_b, revealed_chapter) VALUES "
        "('nov_a', 100, 101, 1), "
        "('nov_b', 100, 102, 1)"
    )

    # Arcs
    db_conn.execute(
        "INSERT INTO arcs (novel_id, arc_id, start_chapter, end_chapter) VALUES "
        "('nov_a', 1, 1, 10), "
        "('nov_b', 1, 1, 10)"
    )

    # Entity arc digests
    db_conn.execute(
        "INSERT INTO entity_arc_digests (novel_id, entity_id, arc_id, upto_chapter, digest) VALUES "
        "('nov_a', 100, 1, 10, 'Digest A'), "
        "('nov_b', 100, 1, 10, 'Digest B')"
    )

    # Arc prefix summaries
    db_conn.execute(
        "INSERT INTO arc_prefix_summaries (novel_id, arc_id, upto_chapter, summary) VALUES "
        "('nov_a', 1, 10, 'Summary A'), "
        "('nov_b', 1, 10, 'Summary B')"
    )

    # Jobs
    repo.upsert_job(novel_id="nov_a", chapter_id=1, stage="s1", status="done")
    repo.upsert_job(novel_id="nov_b", chapter_id=1, stage="s1", status="failed")

    # Feedback
    repo.add_feedback(novel_id="nov_a", run_id="run_a", rating=5)
    repo.add_feedback(novel_id="nov_b", run_id="run_b", rating=1)

    db_conn.commit()

    # Query novel_a
    assert [c["title"] for c in repo.list_chapters(novel_id="nov_a", chapter_max=100)] == ["Ch A1"]
    assert [e["description"] for e in repo.get_entity_events(novel_id="nov_a", entity_id=100, chapter_max=100)] == ["Event A"]
    assert [a["novel_id"] for a in repo.find_aliases(novel_id="nov_a", alias_norm="common_alias", chapter_max=100)] == ["nov_a"]
    assert [l["entity_b"] for l in repo.get_identity_links(novel_id="nov_a", entity_id=100, chapter_max=100)] == [101]
    assert [a["novel_id"] for a in repo.list_arcs(novel_id="nov_a", chapter_max=100)] == ["nov_a"]
    assert [d["digest"] for d in repo.get_entity_arc_digests(novel_id="nov_a", entity_id=100, chapter_max=100)] == ["Digest A"]
    assert [s["summary"] for s in repo.get_arc_prefix_summaries(novel_id="nov_a", arc_id=1, chapter_max=100)] == ["Summary A"]

    novel_a = repo.get_novel(novel_id="nov_a")
    assert novel_a is not None and novel_a["title"] == "Novel A"

    job_a = repo.get_job(novel_id="nov_a", chapter_id=1, stage="s1")
    assert job_a is not None and job_a["status"] == "done"

    # Query novel_b
    assert [c["title"] for c in repo.list_chapters(novel_id="nov_b", chapter_max=100)] == ["Ch B1"]
    assert [e["description"] for e in repo.get_entity_events(novel_id="nov_b", entity_id=100, chapter_max=100)] == ["Event B"]
    novel_b = repo.get_novel(novel_id="nov_b")
    assert novel_b is not None and novel_b["title"] == "Novel B"
    job_b = repo.get_job(novel_id="nov_b", chapter_id=1, stage="s1")
    assert job_b is not None and job_b["status"] == "failed"

    # Test list_novels exempt method lists both
    all_novels = [n["novel_id"] for n in repo.list_novels()]
    assert set(all_novels) == {"nov_a", "nov_b"}

    # Test set_reader_progress
    repo.set_reader_progress(novel_id="nov_a", chapter=42)
    updated_a = repo.get_novel(novel_id="nov_a")
    assert updated_a is not None and updated_a["reader_progress"] == 42
    novel_b_check = repo.get_novel(novel_id="nov_b")
    assert novel_b_check is not None and novel_b_check["reader_progress"] is None


def test_list_aliases_and_find_entities_by_alias_bounds_and_tenant(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify list_aliases and find_entities_by_alias exclude first_chapter > bound and are novel-scoped."""
    repo.add_novel(novel_id="nov_1", title="Novel 1", genre_profile="xianxia", total_chapters=100)
    repo.add_novel(novel_id="nov_2", title="Novel 2", genre_profile="xianxia", total_chapters=100)

    # Entities for nov_1
    db_conn.execute(
        "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) VALUES "
        "('nov_1', 1, 'Hàn Lập', 'character', 5), "
        "('nov_1', 2, 'Lệ Phi Vũ', 'character', 25)"
    )
    # Entities for nov_2
    db_conn.execute(
        "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) VALUES "
        "('nov_2', 1, 'Hàn Lập', 'character', 5)"
    )

    # Aliases for nov_1: first_chapter 10 (<= 20) and 25 (> 20)
    db_conn.execute(
        "INSERT INTO entity_aliases (novel_id, alias_norm, entity_id, first_chapter, source) VALUES "
        "('nov_1', 'han lap', 1, 10, 'exact'), "
        "('nov_1', 'le phi vu', 2, 25, 'exact')"
    )
    # Alias for nov_2: first_chapter 10 (<= 20)
    db_conn.execute(
        "INSERT INTO entity_aliases (novel_id, alias_norm, entity_id, first_chapter, source) VALUES "
        "('nov_2', 'han lap', 1, 10, 'exact')"
    )
    db_conn.commit()

    bound = 20

    # Test list_aliases: returns only nov_1 alias with first_chapter <= 20
    aliases = repo.list_aliases(novel_id="nov_1", chapter_max=bound)
    assert len(aliases) == 1
    assert aliases[0]["alias_norm"] == "han lap"
    assert aliases[0]["entity_id"] == 1
    assert aliases[0]["first_chapter"] == 10
    assert aliases[0]["source"] == "exact"
    assert aliases[0]["category"] == "character"

    # Test find_entities_by_alias:
    entities_hl = repo.find_entities_by_alias(
        novel_id="nov_1", alias_norm="han lap", category="character", chapter_max=bound
    )
    assert len(entities_hl) == 1
    assert entities_hl[0]["entity_id"] == 1
    assert entities_hl[0]["category"] == "character"

    # Beyond bound for nov_1 -> empty
    entities_lpv = repo.find_entities_by_alias(
        novel_id="nov_1", alias_norm="le phi vu", category="character", chapter_max=bound
    )
    assert len(entities_lpv) == 0

    # Verify nov_2 alias is not returned when querying nov_1
    aliases_nov2 = repo.list_aliases(novel_id="nov_2", chapter_max=bound)
    assert len(aliases_nov2) == 1
    assert aliases_nov2[0]["entity_id"] == 1


def test_list_aliases_rows_exclude_last_chapter_and_mention_count(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify list_aliases rows do not contain last_chapter or mention_count keys."""
    repo.add_novel(novel_id="nov_1", title="Novel 1", genre_profile="xianxia", total_chapters=100)
    db_conn.execute(
        "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter, last_chapter, mention_count) "
        "VALUES ('nov_1', 1, 'Hàn Lập', 'character', 1, 50, 100)"
    )
    db_conn.execute(
        "INSERT INTO entity_aliases (novel_id, alias_norm, entity_id, first_chapter, source) "
        "VALUES ('nov_1', 'han lap', 1, 1, 'exact')"
    )
    db_conn.commit()

    rows = repo.list_aliases(novel_id="nov_1", chapter_max=10)
    assert len(rows) == 1
    row_keys = set(rows[0].keys())
    assert "last_chapter" not in row_keys
    assert "mention_count" not in row_keys
    assert row_keys == {"alias_norm", "entity_id", "first_chapter", "source", "category"}


def test_list_extractions_honours_bound(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify list_extractions returns rows <= chapter_max ordered by chapter_id and novel-scoped."""
    repo.add_novel(novel_id="nov_1", title="Novel 1", genre_profile="xianxia", total_chapters=100)
    repo.add_novel(novel_id="nov_2", title="Novel 2", genre_profile="xianxia", total_chapters=100)

    # Seed chapters (parent rows for extractions foreign key)
    db_conn.execute(
        "INSERT INTO chapters (novel_id, chapter_id, title, clean_text, sha256) VALUES "
        "('nov_1', 1, 'Ch 1', 't1', 'h1'), "
        "('nov_1', 2, 'Ch 2', 't2', 'h2'), "
        "('nov_1', 3, 'Ch 3', 't3', 'h3'), "
        "('nov_2', 1, 'Ch 1', 't1', 'h1')"
    )
    db_conn.execute(
        "INSERT INTO extractions (novel_id, chapter_id, payload, prompt_version, model) VALUES "
        "('nov_1', 2, '{\"c\": 2}', 'v1', 'm1'), "
        "('nov_1', 1, '{\"c\": 1}', 'v1', 'm1'), "
        "('nov_1', 3, '{\"c\": 3}', 'v1', 'm1'), "
        "('nov_2', 1, '{\"c\": 1}', 'v1', 'm1')"
    )
    db_conn.commit()

    extractions = repo.list_extractions(novel_id="nov_1", chapter_max=2)
    assert [e["chapter_id"] for e in extractions] == [1, 2]
    assert extractions[0]["payload"] == '{"c": 1}'
    assert extractions[1]["payload"] == '{"c": 2}'


def test_save_resolution_writes_all_rows_and_job(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify save_resolution persists entities, updates, aliases, links, and done job."""
    repo.add_novel(novel_id="nov_1", title="Novel 1", genre_profile="xianxia", total_chapters=100)

    entities = [
        {
            "entity_id": 1,
            "canonical_name": "Hàn Lập",
            "category": "character",
            "first_chapter": 1,
            "last_chapter": 1,
            "mention_count": 2,
        },
        {
            "entity_id": 2,
            "canonical_name": "Lệ Phi Vũ",
            "category": "character",
            "first_chapter": 1,
            "last_chapter": 1,
            "mention_count": 1,
        },
    ]
    entity_updates = [
        {"entity_id": 1, "last_chapter": 2, "mention_delta": 3},
    ]
    aliases = [
        {"alias_norm": "han lap", "entity_id": 1, "first_chapter": 1, "source": "exact"},
        {"alias_norm": "le phi vu", "entity_id": 2, "first_chapter": 1, "source": "exact"},
    ]
    links = [
        {"entity_a": 1, "entity_b": 2, "revealed_chapter": 1, "evidence": "bằng chứng liên kết"},
    ]

    repo.save_resolution(
        novel_id="nov_1",
        chapter_id=1,
        entities=entities,
        entity_updates=entity_updates,
        aliases=aliases,
        links=links,
        stage="s2",
        input_hash="hash_s2_01",
        prompt_version="v1hash02",
        model="qwen14b-12k",
    )

    # Check entities
    cur_ent = db_conn.execute("SELECT * FROM entities WHERE novel_id = 'nov_1' ORDER BY entity_id")
    ents = cur_ent.fetchall()
    assert len(ents) == 2
    assert ents[0]["entity_id"] == 1
    assert ents[0]["last_chapter"] == 2
    assert ents[0]["mention_count"] == 5  # 2 + 3

    # Check aliases
    cur_al = db_conn.execute("SELECT * FROM entity_aliases WHERE novel_id = 'nov_1' ORDER BY entity_id")
    als = cur_al.fetchall()
    assert len(als) == 2
    assert als[0]["alias_norm"] == "han lap"

    # Check links
    cur_lk = db_conn.execute("SELECT * FROM entity_links WHERE novel_id = 'nov_1'")
    lks = cur_lk.fetchall()
    assert len(lks) == 1
    assert lks[0]["entity_a"] == 1
    assert lks[0]["entity_b"] == 2
    assert lks[0]["revealed_chapter"] == 1
    assert lks[0]["evidence"] == "bằng chứng liên kết"

    # Check job
    job = repo.get_job(novel_id="nov_1", chapter_id=1, stage="s2")
    assert job is not None
    assert job["status"] == "done"
    assert job["input_hash"] == "hash_s2_01"
    assert job["prompt_version"] == "v1hash02"
    assert job["model"] == "qwen14b-12k"


def test_save_resolution_conflict_handling_and_earliest_link(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify repeated alias or link changes nothing and raises nothing; earliest link stays."""
    repo.add_novel(novel_id="nov_1", title="Novel 1", genre_profile="xianxia", total_chapters=100)

    # First resolution write
    repo.save_resolution(
        novel_id="nov_1",
        chapter_id=1,
        entities=[
            {"entity_id": 1, "canonical_name": "Hàn Lập", "category": "character", "first_chapter": 1, "last_chapter": 1, "mention_count": 1},
            {"entity_id": 2, "canonical_name": "Lệ Phi Vũ", "category": "character", "first_chapter": 1, "last_chapter": 1, "mention_count": 1},
        ],
        entity_updates=[],
        aliases=[{"alias_norm": "han lap", "entity_id": 1, "first_chapter": 1, "source": "exact"}],
        links=[{"entity_a": 1, "entity_b": 2, "revealed_chapter": 1, "evidence": "ch 1 evidence"}],
        stage="s2",
        input_hash="hash1",
        prompt_version="v1",
        model="m1",
    )

    # Second resolution write with same alias (different first_chapter) and repeated link with later chapter
    repo.save_resolution(
        novel_id="nov_1",
        chapter_id=2,
        entities=[],
        entity_updates=[],
        aliases=[{"alias_norm": "han lap", "entity_id": 1, "first_chapter": 2, "source": "fuzzy"}],
        links=[{"entity_a": 1, "entity_b": 2, "revealed_chapter": 5, "evidence": "ch 5 evidence"}],
        stage="s2",
        input_hash="hash2",
        prompt_version="v1",
        model="m1",
    )

    # Alias keeps original first_chapter 1 and source exact
    cur_al = db_conn.execute("SELECT * FROM entity_aliases WHERE novel_id = 'nov_1' AND alias_norm = 'han lap'")
    al = cur_al.fetchone()
    assert al["first_chapter"] == 1
    assert al["source"] == "exact"

    # Link keeps earliest revealed_chapter 1
    cur_lk = db_conn.execute("SELECT * FROM entity_links WHERE novel_id = 'nov_1'")
    lk = cur_lk.fetchone()
    assert lk["revealed_chapter"] == 1
    assert lk["evidence"] == "ch 1 evidence"


def test_save_resolution_link_ordering_and_equal_ids(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify links store smaller id as entity_a, and equal ids raise ValueError."""
    repo.add_novel(novel_id="nov_1", title="Novel 1", genre_profile="xianxia", total_chapters=100)

    # Inverted ids: entity_a=5, entity_b=2
    repo.save_resolution(
        novel_id="nov_1",
        chapter_id=1,
        entities=[
            {"entity_id": 2, "canonical_name": "Ent 2", "category": "character", "first_chapter": 1, "last_chapter": 1, "mention_count": 1},
            {"entity_id": 5, "canonical_name": "Ent 5", "category": "character", "first_chapter": 1, "last_chapter": 1, "mention_count": 1},
        ],
        entity_updates=[],
        aliases=[],
        links=[{"entity_a": 5, "entity_b": 2, "revealed_chapter": 1, "evidence": "ev"}],
        stage="s2",
        input_hash="h1",
        prompt_version="v1",
        model="m1",
    )

    cur = db_conn.execute("SELECT entity_a, entity_b FROM entity_links WHERE novel_id = 'nov_1'")
    link_row = cur.fetchone()
    assert link_row["entity_a"] == 2
    assert link_row["entity_b"] == 5

    # Equal ids: entity_a=3, entity_b=3 raises ValueError
    with pytest.raises(ValueError, match="cannot be equal"):
        repo.save_resolution(
            novel_id="nov_1",
            chapter_id=2,
            entities=[],
            entity_updates=[],
            aliases=[],
            links=[{"entity_a": 3, "entity_b": 3, "revealed_chapter": 2, "evidence": "ev"}],
            stage="s2",
            input_hash="h2",
            prompt_version="v1",
            model="m1",
        )


def test_save_resolution_leaves_nothing_if_job_write_fails(
    repo: Repository, db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify atomicity of save_resolution: transaction rollback leaves no entity, alias, link, or job rows."""
    repo.add_novel(novel_id="nov_1", title="Novel 1", genre_profile="xianxia", total_chapters=100)

    def crash_upsert(*args: Any, **kwargs: Any) -> None:
        raise sqlite3.OperationalError("Simulated job upsert failure in save_resolution")

    monkeypatch.setattr(repo, "_upsert_job", crash_upsert)

    with pytest.raises(sqlite3.OperationalError, match="Simulated job upsert failure"):
        repo.save_resolution(
            novel_id="nov_1",
            chapter_id=1,
            entities=[
                {"entity_id": 1, "canonical_name": "Hàn Lập", "category": "character", "first_chapter": 1, "last_chapter": 1, "mention_count": 1}
            ],
            entity_updates=[],
            aliases=[{"alias_norm": "han lap", "entity_id": 1, "first_chapter": 1, "source": "exact"}],
            links=[],
            stage="s2",
            input_hash="hash01",
            prompt_version="v1",
            model="m1",
        )

    assert db_conn.execute("SELECT count(*) FROM entities WHERE novel_id = 'nov_1'").fetchone()[0] == 0
    assert db_conn.execute("SELECT count(*) FROM entity_aliases WHERE novel_id = 'nov_1'").fetchone()[0] == 0
    assert db_conn.execute("SELECT count(*) FROM entity_links WHERE novel_id = 'nov_1'").fetchone()[0] == 0
    assert db_conn.execute("SELECT count(*) FROM jobs WHERE novel_id = 'nov_1' AND stage = 's2'").fetchone()[0] == 0


def test_clear_resolution_clears_only_given_novel_and_refuses_on_events(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify clear_resolution clears only the targeted novel and raises when entity_events exist."""
    repo.add_novel(novel_id="nov_1", title="Novel 1", genre_profile="xianxia", total_chapters=100)
    repo.add_novel(novel_id="nov_2", title="Novel 2", genre_profile="xianxia", total_chapters=100)

    # Seed nov_1 and nov_2 entities, aliases, links, jobs
    for n in ["nov_1", "nov_2"]:
        repo.save_resolution(
            novel_id=n,
            chapter_id=1,
            entities=[
                {"entity_id": 1, "canonical_name": f"Ent {n}", "category": "character", "first_chapter": 1, "last_chapter": 1, "mention_count": 1}
            ],
            entity_updates=[],
            aliases=[{"alias_norm": f"alias_{n}", "entity_id": 1, "first_chapter": 1, "source": "exact"}],
            links=[],
            stage="s2",
            input_hash=f"h_{n}",
            prompt_version="v1",
            model="m1",
        )
    # Also add an s1 job for nov_1 to test that only stage='s2' jobs are deleted
    repo.upsert_job(novel_id="nov_1", chapter_id=1, stage="s1", status="done")

    # Seed entity_events for nov_1 -> clear_resolution must raise ValueError
    db_conn.execute(
        "INSERT INTO entity_events (novel_id, id, chapter_id, entity_id, event_type, ordinal, description) "
        "VALUES ('nov_1', 1, 1, 1, 'ACQUISITION', 1, 'Ev 1')"
    )
    db_conn.commit()

    with pytest.raises(ValueError, match="entity_events has rows"):
        repo.clear_resolution(novel_id="nov_1", stage="s2")

    # Remove the entity_event row
    db_conn.execute("DELETE FROM entity_events WHERE novel_id = 'nov_1'")
    db_conn.commit()

    # Now clear_resolution should succeed
    repo.clear_resolution(novel_id="nov_1", stage="s2")

    # nov_1 resolution rows are gone
    assert db_conn.execute("SELECT count(*) FROM entities WHERE novel_id = 'nov_1'").fetchone()[0] == 0
    assert db_conn.execute("SELECT count(*) FROM entity_aliases WHERE novel_id = 'nov_1'").fetchone()[0] == 0
    assert db_conn.execute("SELECT count(*) FROM entity_links WHERE novel_id = 'nov_1'").fetchone()[0] == 0
    assert repo.get_job(novel_id="nov_1", chapter_id=1, stage="s2") is None
    # nov_1 s1 job remains
    assert repo.get_job(novel_id="nov_1", chapter_id=1, stage="s1") is not None

    # nov_2 resolution rows are completely untouched
    assert db_conn.execute("SELECT count(*) FROM entities WHERE novel_id = 'nov_2'").fetchone()[0] == 1
    assert db_conn.execute("SELECT count(*) FROM entity_aliases WHERE novel_id = 'nov_2'").fetchone()[0] == 1
    assert repo.get_job(novel_id="nov_2", chapter_id=1, stage="s2") is not None


def test_next_entity_id_scoped_to_novel(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify next_entity_id returns 1 for empty novel, max+1 otherwise, and is novel-scoped."""
    repo.add_novel(novel_id="nov_a", title="Novel A", genre_profile="xianxia", total_chapters=100)
    repo.add_novel(novel_id="nov_b", title="Novel B", genre_profile="xianxia", total_chapters=100)

    # Empty novel returns 1
    assert repo.next_entity_id(novel_id="nov_a") == 1
    assert repo.next_entity_id(novel_id="nov_b") == 1

    # Insert entity for nov_a with id 1
    db_conn.execute(
        "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) "
        "VALUES ('nov_a', 1, 'Ent A1', 'character', 1)"
    )
    # Insert entity for nov_b with id 10
    db_conn.execute(
        "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) "
        "VALUES ('nov_b', 10, 'Ent B10', 'character', 1)"
    )
    db_conn.commit()

    assert repo.next_entity_id(novel_id="nov_a") == 2
    assert repo.next_entity_id(novel_id="nov_b") == 11
