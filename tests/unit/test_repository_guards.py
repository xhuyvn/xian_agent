"""Unit tests for SQLite schema and repository spoiler guards."""

from __future__ import annotations

import inspect
from pathlib import Path
import sqlite3
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
]

OPERATIONAL_METHODS = [
    "add_novel",
    "get_novel",
    "set_reader_progress",
    "save_chapter",
    "upsert_job",
    "get_job",
    "add_feedback",
    "add_llm_run",
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
