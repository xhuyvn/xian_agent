"""Unit tests for the L5 spoiler scan: future aliases, higher realms, redaction."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from xn.graph.nodes.spoiler_scan import (
    REDACTED,
    find_leaks,
    forbidden_terms,
    realm_ladder,
    realm_ranks,
    redact,
)
from xn.config import load_config
from xn.schemas.state import ChunkEvidence, EvidenceItem, EvidencePack
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

REPO_ROOT = Path(__file__).resolve().parents[2]
LADDER = ["Luyện Khí", "Trúc Cơ", "Kết Đan", "Nguyên Anh"]


@pytest.fixture
def repo(tmp_path: Path):
    """Seed aliases on both sides of chapter 28 and realms seen up to Trúc Cơ."""
    conn = connect(tmp_path / "xn.db")
    init_schema(conn)
    repo = Repository(conn)
    for novel in ("nov", "other"):
        repo.add_novel(novel_id=novel, title="N", genre_profile="xianxia", total_chapters=100)
    conn.execute(
        "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) VALUES "
        "('nov', 1, 'Mặc đại phu', 'character', 5), ('nov', 2, 'Hàn Lập', 'character', 1), "
        "('nov', 3, 'Mặc Cư Nhân', 'character', 29), ('other', 9, 'Lục Tuyết', 'character', 50)"
    )
    conn.executemany(
        "INSERT INTO entity_aliases (novel_id, alias_norm, entity_id, first_chapter, source) "
        "VALUES (?, ?, ?, ?, 'exact')",
        [
            ("nov", "mặc đại phu", 1, 5),
            ("nov", "hàn lập", 2, 1),
            ("nov", "mặc cư nhân", 3, 29),
            ("nov", "hàn lập", 3, 40),  # known at <= 28 through entity 2
            ("nov", "mặc", 3, 29),  # one syllable, too common to scan
            ("other", "lục tuyết", 9, 50),
        ],
    )
    conn.executemany(
        "INSERT INTO entity_events (novel_id, id, chapter_id, entity_id, event_type, ordinal, "
        "tier_or_realm, description) VALUES ('nov', ?, ?, 2, 'UPGRADE', 0, ?, 'đột phá')",
        [(1, 10, "Luyện Khí tầng 3"), (2, 25, "Trúc Cơ sơ kỳ"), (3, 60, "Kết Đan")],
    )
    conn.commit()
    try:
        yield repo
    finally:
        conn.close()


def terms(repo: Repository, chapter_max: int = 28, evidence: EvidencePack | None = None) -> list[str]:
    return forbidden_terms(
        repo, novel_id="nov", chapter_max=chapter_max, ladder=LADDER, evidence=evidence
    )


def test_future_alias_and_higher_realms_are_forbidden(repo: Repository) -> None:
    assert terms(repo) == ["mặc cư nhân", "Kết Đan", "Nguyên Anh"]


def test_alias_known_by_n_and_one_syllable_aliases_are_not(repo: Repository) -> None:
    found = terms(repo)
    assert "hàn lập" not in found
    assert "mặc" not in found


def test_another_novels_aliases_never_count(repo: Repository) -> None:
    assert "lục tuyết" not in terms(repo)


def test_bound_moves_the_lists(repo: Repository) -> None:
    assert terms(repo, chapter_max=9) == ["mặc cư nhân", *LADDER]
    assert terms(repo, chapter_max=60) == ["Nguyên Anh"]


def test_terms_in_the_evidence_are_allowed(repo: Repository) -> None:
    evidence = EvidencePack(
        items=[
            EvidenceItem(
                event_id=1, chapter_id=20, entity_id=2, entity_name="Hàn Lập",
                event_type="MEET", description="gặp một trưởng lão Kết Đan",
            )
        ],
        chunks=[ChunkEvidence(chapter_id=21, kind="text", chunk_no=0, text="x", score=0.9)],
    )
    assert terms(repo, evidence=evidence) == ["mặc cư nhân", "Nguyên Anh"]


def test_realm_rank_reads_free_text() -> None:
    assert realm_ranks(LADDER, ["Trúc Cơ sơ kỳ", "phàm nhân"]) == 2
    assert realm_ranks(LADDER, []) == 0


@pytest.mark.parametrize(
    "text, leaks",
    [
        ("Tên thật của ông là Mặc Cư Nhân [chương 29].", ["mặc cư nhân"]),
        ("ten that la MAC CU NHAN.", ["mặc cư nhân"]),
        ("Hắn đạt Kết Đan.", ["Kết Đan"]),
        ("Mặc đại phu dạy Hàn Lập [chương 10].", []),
        ("Kết Đanh không phải cảnh giới.", []),
    ],
)
def test_find_leaks_folds_case_and_diacritics_on_word_bounds(text: str, leaks: list[str]) -> None:
    assert find_leaks(text, ["mặc cư nhân", "Kết Đan"]) == leaks


def test_clean_answer_passes_unchanged() -> None:
    markdown = "Mặc đại phu dạy Hàn Lập [chương 10]. Hàn Lập luyện công [chương 12]."
    assert redact(markdown, ["mặc cư nhân"]) == markdown


def test_redact_replaces_only_the_leaking_sentence() -> None:
    markdown = (
        "Mặc đại phu dạy Hàn Lập [chương 10]. Thật ra ông là Mặc Cư Nhân! Hàn Lập ở lại.\n"
        "- Ông sắp lên Kết Đan [chương 12].\n- Hàn Lập luyện công."
    )
    assert redact(markdown, ["mặc cư nhân", "Kết Đan"]) == (
        f"Mặc đại phu dạy Hàn Lập [chương 10]. {REDACTED} Hàn Lập ở lại.\n"
        f"- {REDACTED}\n- Hàn Lập luyện công."
    )


def test_pntt1_config_carries_the_realm_ladder() -> None:
    ladder = realm_ladder(load_config("pntt1", configs_dir=REPO_ROOT / "configs"))
    assert ladder[:3] == ["Luyện Khí", "Trúc Cơ", "Kết Đan"]
    assert len(ladder) == 9


def test_scan_reads_are_spoiler_guarded(repo: Repository) -> None:
    with pytest.raises(ValueError):
        repo.list_future_aliases(novel_id="nov", chapter_max=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        repo.list_realms_seen(novel_id="", chapter_max=5)
