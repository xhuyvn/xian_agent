"""SQLite database connection and schema management for XN-Agent."""

from __future__ import annotations

from pathlib import Path
import sqlite3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS novels (novel_id TEXT PRIMARY KEY, title TEXT NOT NULL, author TEXT, language TEXT DEFAULT 'vi',
  genre_profile TEXT NOT NULL, series_id TEXT, total_chapters INTEGER NOT NULL,
  reader_progress INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS chapters (novel_id TEXT REFERENCES novels, chapter_id INTEGER, title TEXT, clean_text TEXT NOT NULL,
  sha256 TEXT NOT NULL, token_count INTEGER, arc_id INTEGER, PRIMARY KEY(novel_id, chapter_id));
CREATE TABLE IF NOT EXISTS extractions (novel_id TEXT, chapter_id INTEGER, payload JSON NOT NULL, prompt_version TEXT NOT NULL,
  model TEXT NOT NULL, created_at TEXT, PRIMARY KEY(novel_id, chapter_id),
  FOREIGN KEY(novel_id, chapter_id) REFERENCES chapters);
CREATE TABLE IF NOT EXISTS entities (novel_id TEXT REFERENCES novels, entity_id INTEGER, canonical_name TEXT NOT NULL,
  category TEXT NOT NULL, first_chapter INTEGER NOT NULL, last_chapter INTEGER, mention_count INTEGER DEFAULT 0,
  PRIMARY KEY(novel_id, entity_id), UNIQUE(novel_id, canonical_name, category));
CREATE TABLE IF NOT EXISTS entity_aliases (novel_id TEXT, alias_norm TEXT, entity_id INTEGER, first_chapter INTEGER NOT NULL,
  source TEXT CHECK (source IN ('seed','exact','fuzzy','llm')), PRIMARY KEY(novel_id, alias_norm, entity_id));
CREATE TABLE IF NOT EXISTS entity_links (novel_id TEXT, entity_a INTEGER, entity_b INTEGER, relation TEXT DEFAULT 'same_identity',
  revealed_chapter INTEGER NOT NULL, evidence TEXT, PRIMARY KEY(novel_id, entity_a, entity_b));
CREATE TABLE IF NOT EXISTS entity_events (novel_id TEXT, id INTEGER, chapter_id INTEGER NOT NULL, entity_id INTEGER NOT NULL,
  event_type TEXT NOT NULL, ordinal INTEGER NOT NULL, holder_entity_id INTEGER, tier_or_realm TEXT,
  realm_rank INTEGER, description TEXT NOT NULL, evidence TEXT, PRIMARY KEY(novel_id, id),
  UNIQUE(novel_id, chapter_id, entity_id, event_type, ordinal));
CREATE INDEX IF NOT EXISTS ix_events_entity ON entity_events(novel_id, entity_id, chapter_id);
CREATE TABLE IF NOT EXISTS arcs (novel_id TEXT, arc_id INTEGER, start_chapter INTEGER, end_chapter INTEGER, payload JSON,
  prompt_version TEXT, model TEXT, PRIMARY KEY(novel_id, arc_id));
CREATE TABLE IF NOT EXISTS entity_arc_digests (novel_id TEXT, entity_id INTEGER, arc_id INTEGER, upto_chapter INTEGER,
  digest TEXT, event_count INTEGER, PRIMARY KEY(novel_id, entity_id, arc_id, upto_chapter));
CREATE TABLE IF NOT EXISTS arc_prefix_summaries (novel_id TEXT, arc_id INTEGER, upto_chapter INTEGER, summary TEXT,
  PRIMARY KEY(novel_id, arc_id, upto_chapter));
CREATE TABLE IF NOT EXISTS jobs (novel_id TEXT, chapter_id INTEGER, stage TEXT, status TEXT
  CHECK (status IN ('pending','running','done','failed')), input_hash TEXT, prompt_version TEXT, model TEXT,
  attempts INTEGER DEFAULT 0, error TEXT, updated_at TEXT, PRIMARY KEY(novel_id, chapter_id, stage));
CREATE TABLE IF NOT EXISTS llm_runs (run_id TEXT PRIMARY KEY, novel_id TEXT, stage TEXT, chapter_id INTEGER, model TEXT,
  prompt_version TEXT, input_tokens INTEGER, output_tokens INTEGER, latency_ms INTEGER, retries INTEGER,
  ok INTEGER, cost_usd REAL, egress_class TEXT CHECK (egress_class IN ('local','compact','long_context')),
  payload_chapters TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS answer_cache (novel_id TEXT, cache_key TEXT, chapter_max INTEGER, answer JSON, created_at TEXT,
  PRIMARY KEY(novel_id, cache_key));
CREATE TABLE IF NOT EXISTS lc_map_cache (novel_id TEXT, entity_id INTEGER, arc_id INTEGER, upto_chapter INTEGER,
  prompt_version TEXT, model TEXT, payload JSON, input_tokens INTEGER,
  PRIMARY KEY(novel_id, entity_id, arc_id, upto_chapter, prompt_version, model));
CREATE TABLE IF NOT EXISTS feedback (novel_id TEXT NOT NULL, run_id TEXT, rating INTEGER, note TEXT, created_at TEXT);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Connect to SQLite database, configure WAL mode, foreign keys, and Row factory."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Initialize the SQLite schema idempotently."""
    conn.executescript(SCHEMA_SQL)
