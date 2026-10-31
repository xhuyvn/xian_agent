"""Repository layer with spoiler boundary enforcement."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
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

    @story_read
    def list_extractions(
        self, *, novel_id: str, chapter_max: int
    ) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM extractions WHERE novel_id = ? AND chapter_id <= ? ORDER BY chapter_id",
            (novel_id, chapter_max),
        )
        return cur.fetchall()

    @story_read
    def list_aliases(
        self, *, novel_id: str, chapter_max: int
    ) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT entity_aliases.alias_norm, entity_aliases.entity_id, "
            "entity_aliases.first_chapter, entity_aliases.source, entities.category "
            "FROM entity_aliases "
            "JOIN entities ON entity_aliases.novel_id = entities.novel_id "
            "AND entity_aliases.entity_id = entities.entity_id "
            "WHERE entity_aliases.novel_id = ? AND entity_aliases.first_chapter <= ? "
            "ORDER BY entity_aliases.first_chapter, entity_aliases.alias_norm, entity_aliases.entity_id",
            (novel_id, chapter_max),
        )
        return cur.fetchall()

    @story_read
    def find_entities_by_alias(
        self, *, novel_id: str, alias_norm: str, category: str, chapter_max: int
    ) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT DISTINCT entity_aliases.entity_id, entities.category "
            "FROM entity_aliases "
            "JOIN entities ON entity_aliases.novel_id = entities.novel_id "
            "AND entity_aliases.entity_id = entities.entity_id "
            "WHERE entity_aliases.novel_id = ? AND entity_aliases.alias_norm = ? "
            "AND entities.category = ? AND entity_aliases.first_chapter <= ? "
            "ORDER BY entity_aliases.entity_id",
            (novel_id, alias_norm, category, chapter_max),
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

    def _upsert_job(
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
        now = datetime.now(timezone.utc).isoformat()
        initial_attempts = 1 if status == "running" else 0
        self._conn.execute(
            "INSERT INTO jobs ("
            "novel_id, chapter_id, stage, status, input_hash, prompt_version, model, error, attempts, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(novel_id, chapter_id, stage) DO UPDATE SET "
            "status = excluded.status, "
            "input_hash = excluded.input_hash, "
            "prompt_version = excluded.prompt_version, "
            "model = excluded.model, "
            "error = excluded.error, "
            "attempts = CASE WHEN excluded.status = 'running' THEN jobs.attempts + 1 ELSE jobs.attempts END, "
            "updated_at = excluded.updated_at",
            (
                novel_id,
                chapter_id,
                stage,
                status,
                input_hash,
                prompt_version,
                model,
                error,
                initial_attempts,
                now,
            ),
        )

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
        self._upsert_job(
            novel_id=novel_id,
            chapter_id=chapter_id,
            stage=stage,
            status=status,
            input_hash=input_hash,
            prompt_version=prompt_version,
            model=model,
            error=error,
        )
        self._conn.commit()

    def save_chapter(
        self,
        *,
        novel_id: str,
        chapter_id: int,
        title: str | None,
        clean_text: str,
        sha256: str,
        token_count: int,
        stage: str,
        input_hash: str,
    ) -> None:
        _validate_novel_id(novel_id)
        with self._conn:
            self._conn.execute(
                "INSERT INTO chapters (novel_id, chapter_id, title, clean_text, sha256, token_count) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(novel_id, chapter_id) DO UPDATE SET "
                "title = excluded.title, "
                "clean_text = excluded.clean_text, "
                "sha256 = excluded.sha256, "
                "token_count = excluded.token_count",
                (novel_id, chapter_id, title, clean_text, sha256, token_count),
            )
            self._upsert_job(
                novel_id=novel_id,
                chapter_id=chapter_id,
                stage=stage,
                status="done",
                input_hash=input_hash,
            )

    def save_extraction(
        self,
        *,
        novel_id: str,
        chapter_id: int,
        payload: str,
        prompt_version: str,
        model: str,
        stage: str,
        input_hash: str,
    ) -> None:
        _validate_novel_id(novel_id)
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO extractions (novel_id, chapter_id, payload, prompt_version, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(novel_id, chapter_id) DO UPDATE SET "
                "payload = excluded.payload, "
                "prompt_version = excluded.prompt_version, "
                "model = excluded.model, "
                "created_at = excluded.created_at",
                (novel_id, chapter_id, payload, prompt_version, model, now),
            )
            self._upsert_job(
                novel_id=novel_id,
                chapter_id=chapter_id,
                stage=stage,
                status="done",
                input_hash=input_hash,
                prompt_version=prompt_version,
                model=model,
            )

    def next_entity_id(self, *, novel_id: str) -> int:
        _validate_novel_id(novel_id)
        cur = self._conn.execute(
            "SELECT COALESCE(MAX(entity_id), 0) + 1 FROM entities WHERE novel_id = ?",
            (novel_id,),
        )
        return int(cur.fetchone()[0])

    def save_resolution(
        self,
        *,
        novel_id: str,
        chapter_id: int,
        entities: list[dict[str, Any]],
        entity_updates: list[dict[str, Any]],
        aliases: list[dict[str, Any]],
        links: list[dict[str, Any]],
        stage: str,
        input_hash: str,
        prompt_version: str,
        model: str,
    ) -> None:
        _validate_novel_id(novel_id)
        with self._conn:
            for ent in entities:
                self._conn.execute(
                    "INSERT INTO entities ("
                    "novel_id, entity_id, canonical_name, category, first_chapter, last_chapter, mention_count"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        novel_id,
                        ent["entity_id"],
                        ent["canonical_name"],
                        ent["category"],
                        ent["first_chapter"],
                        ent.get("last_chapter"),
                        ent.get("mention_count", 0),
                    ),
                )
            for upd in entity_updates:
                self._conn.execute(
                    "UPDATE entities "
                    "SET last_chapter = MAX(COALESCE(last_chapter, 0), ?), "
                    "mention_count = mention_count + ? "
                    "WHERE novel_id = ? AND entity_id = ?",
                    (
                        upd["last_chapter"],
                        upd["mention_delta"],
                        novel_id,
                        upd["entity_id"],
                    ),
                )
            for al in aliases:
                self._conn.execute(
                    "INSERT INTO entity_aliases ("
                    "novel_id, alias_norm, entity_id, first_chapter, source"
                    ") VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(novel_id, alias_norm, entity_id) DO NOTHING",
                    (
                        novel_id,
                        al["alias_norm"],
                        al["entity_id"],
                        al["first_chapter"],
                        al["source"],
                    ),
                )
            for lk in links:
                ea = lk["entity_a"]
                eb = lk["entity_b"]
                if ea == eb:
                    raise ValueError(f"entity_a and entity_b cannot be equal: {ea}")
                smaller = min(ea, eb)
                larger = max(ea, eb)
                self._conn.execute(
                    "INSERT INTO entity_links ("
                    "novel_id, entity_a, entity_b, revealed_chapter, evidence"
                    ") VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(novel_id, entity_a, entity_b) DO NOTHING",
                    (
                        novel_id,
                        smaller,
                        larger,
                        lk["revealed_chapter"],
                        lk.get("evidence"),
                    ),
                )
            self._upsert_job(
                novel_id=novel_id,
                chapter_id=chapter_id,
                stage=stage,
                status="done",
                input_hash=input_hash,
                prompt_version=prompt_version,
                model=model,
            )

    def clear_resolution(self, *, novel_id: str, stage: str) -> None:
        _validate_novel_id(novel_id)
        cur = self._conn.execute(
            "SELECT 1 FROM entity_events WHERE novel_id = ? LIMIT 1",
            (novel_id,),
        )
        if cur.fetchone() is not None:
            raise ValueError(
                f"Cannot clear resolution: entity_events has rows for novel '{novel_id}'"
            )
        with self._conn:
            self._conn.execute(
                "DELETE FROM entity_links WHERE novel_id = ?", (novel_id,)
            )
            self._conn.execute(
                "DELETE FROM entity_aliases WHERE novel_id = ?", (novel_id,)
            )
            self._conn.execute(
                "DELETE FROM entities WHERE novel_id = ?", (novel_id,)
            )
            self._conn.execute(
                "DELETE FROM jobs WHERE novel_id = ? AND stage = ?",
                (novel_id, stage),
            )

    def save_ledger(
        self,
        *,
        novel_id: str,
        chapter_id: int,
        events: list[dict[str, Any]],
        stage: str,
        input_hash: str,
    ) -> None:
        _validate_novel_id(novel_id)
        with self._conn:
            self._conn.execute(
                "DELETE FROM entity_events WHERE novel_id = ? AND chapter_id = ?",
                (novel_id, chapter_id),
            )
            for ev in events:
                event_type = ev["event_type"]
                if isinstance(event_type, Enum):
                    event_type = event_type.value
                self._conn.execute(
                    "INSERT INTO entity_events ("
                    "novel_id, id, chapter_id, entity_id, event_type, ordinal, "
                    "holder_entity_id, tier_or_realm, realm_rank, description, evidence"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        novel_id,
                        ev["id"],
                        chapter_id,
                        ev["entity_id"],
                        event_type,
                        ev["ordinal"],
                        ev.get("holder_entity_id"),
                        ev.get("tier_or_realm"),
                        ev.get("realm_rank"),
                        ev["description"],
                        ev.get("evidence"),
                    ),
                )
            self._upsert_job(
                novel_id=novel_id,
                chapter_id=chapter_id,
                stage=stage,
                status="done",
                input_hash=input_hash,
            )

    def clear_ledger(self, *, novel_id: str, stage: str) -> None:
        _validate_novel_id(novel_id)
        with self._conn:
            self._conn.execute(
                "DELETE FROM entity_events WHERE novel_id = ?", (novel_id,)
            )
            self._conn.execute(
                "DELETE FROM jobs WHERE novel_id = ? AND stage = ?",
                (novel_id, stage),
            )

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

    def add_llm_run(
        self,
        *,
        novel_id: str,
        run_id: str,
        stage: str,
        chapter_id: int | None,
        model: str,
        prompt_version: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int,
        retries: int | None,
        ok: int,
        cost_usd: float,
        egress_class: str,
        payload_chapters: str | None,
    ) -> None:
        _validate_novel_id(novel_id)
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO llm_runs ("
            "run_id, novel_id, stage, chapter_id, model, prompt_version, "
            "input_tokens, output_tokens, latency_ms, retries, ok, cost_usd, "
            "egress_class, payload_chapters, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                novel_id,
                stage,
                chapter_id,
                model,
                prompt_version,
                input_tokens,
                output_tokens,
                latency_ms,
                retries,
                ok,
                cost_usd,
                egress_class,
                payload_chapters,
                now,
            ),
        )
        self._conn.commit()

    # --- Exempt ---

    def list_novels(self) -> list[sqlite3.Row]:
        cur = self._conn.execute("SELECT * FROM novels ORDER BY novel_id")
        return cur.fetchall()
