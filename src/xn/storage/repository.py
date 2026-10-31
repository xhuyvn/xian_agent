"""Repository layer with spoiler boundary enforcement."""

from __future__ import annotations

import functools
import inspect
import sqlite3
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def story_read(func: F) -> F:
    """Decorator for story reads: enforces non-empty novel_id and valid integer chapter_max."""

    @functools.wraps(func)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        sig = inspect.signature(func)
        bound = sig.bind(self, *args, **kwargs)
        bound.apply_defaults()
        novel_id = bound.arguments.get("novel_id")
        chapter_max = bound.arguments.get("chapter_max")
        if novel_id is None or not isinstance(novel_id, str) or not novel_id:
            raise ValueError("novel_id must be a non-empty string")
        if chapter_max is None or isinstance(chapter_max, bool) or not isinstance(chapter_max, int):
            raise ValueError("chapter_max must be an int, not None or bool")
        return func(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _validate_novel_id(novel_id: Any) -> None:
    """Validate that novel_id is a non-empty string."""
    if novel_id is None or not isinstance(novel_id, str) or not novel_id:
        raise ValueError("novel_id must be a non-empty string")


class Repository:
    """Guarded data repository for XN-Agent storing and reading novel records."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- Story reads ---

    @story_read
    def list_chapters(self, *, novel_id: str, chapter_max: int) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM chapters WHERE novel_id = ? AND chapter_id <= ? ORDER BY chapter_id",
            (novel_id, chapter_max),
        )
        return cur.fetchall()

    @story_read
    def get_entity_events(
        self, *, novel_id: str, entity_id: int, chapter_max: int
    ) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM entity_events WHERE novel_id = ? AND entity_id = ? AND chapter_id <= ? "
            "ORDER BY chapter_id, ordinal",
            (novel_id, entity_id, chapter_max),
        )
        return cur.fetchall()

    @story_read
    def find_aliases(
        self, *, novel_id: str, alias_norm: str, chapter_max: int
    ) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM entity_aliases WHERE novel_id = ? AND alias_norm = ? AND first_chapter <= ? "
            "ORDER BY first_chapter",
            (novel_id, alias_norm, chapter_max),
        )
        return cur.fetchall()

    @story_read
    def get_identity_links(
        self, *, novel_id: str, entity_id: int, chapter_max: int
    ) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM entity_links WHERE novel_id = ? AND (entity_a = ? OR entity_b = ?) "
            "AND revealed_chapter <= ? ORDER BY revealed_chapter",
            (novel_id, entity_id, entity_id, chapter_max),
        )
        return cur.fetchall()

    @story_read
    def list_arcs(self, *, novel_id: str, chapter_max: int) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM arcs WHERE novel_id = ? AND end_chapter <= ? ORDER BY start_chapter",
            (novel_id, chapter_max),
        )
        return cur.fetchall()

    @story_read
    def get_entity_arc_digests(
        self, *, novel_id: str, entity_id: int, chapter_max: int
    ) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM entity_arc_digests WHERE novel_id = ? AND entity_id = ? AND upto_chapter <= ? "
            "ORDER BY upto_chapter",
            (novel_id, entity_id, chapter_max),
        )
        return cur.fetchall()

    @story_read
    def get_arc_prefix_summaries(
        self, *, novel_id: str, arc_id: int, chapter_max: int
    ) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM arc_prefix_summaries WHERE novel_id = ? AND arc_id = ? AND upto_chapter <= ? "
            "ORDER BY upto_chapter",
            (novel_id, arc_id, chapter_max),
        )
        return cur.fetchall()

    # --- Operational ---

    def add_novel(
        self,
        *,
        novel_id: str,
        title: str,
        genre_profile: str,
        total_chapters: int,
        author: str | None = None,
    ) -> None:
        _validate_novel_id(novel_id)
        self._conn.execute(
            "INSERT INTO novels (novel_id, title, genre_profile, total_chapters, author) "
            "VALUES (?, ?, ?, ?, ?)",
            (novel_id, title, genre_profile, total_chapters, author),
        )
        self._conn.commit()

    def get_novel(self, *, novel_id: str) -> sqlite3.Row | None:
        _validate_novel_id(novel_id)
        cur = self._conn.execute("SELECT * FROM novels WHERE novel_id = ?", (novel_id,))
        return cur.fetchone()

    def set_reader_progress(self, *, novel_id: str, chapter: int) -> None:
        _validate_novel_id(novel_id)
        self._conn.execute(
            "UPDATE novels SET reader_progress = ? WHERE novel_id = ?",
            (chapter, novel_id),
        )
        self._conn.commit()

    def upsert_job(
        self,
        *,
        novel_id: str,
        chapter_id: int,
        stage: str,
        status: str,
        input_hash: str | None = None,
        prompt_version: str | None = None,
        model: str | None = None,
        error: str | None = None,
    ) -> None:
        _validate_novel_id(novel_id)
        self._conn.execute(
            "INSERT INTO jobs (novel_id, chapter_id, stage, status, input_hash, prompt_version, model, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(novel_id, chapter_id, stage) DO UPDATE SET "
            "status = excluded.status, "
            "input_hash = excluded.input_hash, "
            "prompt_version = excluded.prompt_version, "
            "model = excluded.model, "
            "error = excluded.error",
            (novel_id, chapter_id, stage, status, input_hash, prompt_version, model, error),
        )
        self._conn.commit()

    def get_job(
        self, *, novel_id: str, chapter_id: int, stage: str
    ) -> sqlite3.Row | None:
        _validate_novel_id(novel_id)
        cur = self._conn.execute(
            "SELECT * FROM jobs WHERE novel_id = ? AND chapter_id = ? AND stage = ?",
            (novel_id, chapter_id, stage),
        )
        return cur.fetchone()

    def add_feedback(
        self,
        *,
        novel_id: str,
        run_id: str,
        rating: int,
        note: str | None = None,
    ) -> None:
        _validate_novel_id(novel_id)
        self._conn.execute(
            "INSERT INTO feedback (novel_id, run_id, rating, note) VALUES (?, ?, ?, ?)",
            (novel_id, run_id, rating, note),
        )
        self._conn.commit()

    # --- Exempt ---

    def list_novels(self) -> list[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM novels ORDER BY novel_id")
        return cur.fetchall()
