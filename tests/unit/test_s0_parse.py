"""Unit tests for S0 parse and clean pipeline stage."""

from __future__ import annotations

import builtins
import hashlib
import logging
import math
from pathlib import Path
import sqlite3
import sys
import unicodedata
from typing import Any
import pytest

from xn.pipeline.s0_parse import ChapterSetError, S0Result, run_s0
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository
from xn.text import CHARS_PER_TOKEN, estimate_tokens, normalize_text


@pytest.fixture
def db_conn(tmp_path: Path):
    """Provide an initialized SQLite database connection."""
    db_file = tmp_path / "test_s0.db"
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


def make_config(**overrides: Any) -> dict[str, Any]:
    """Helper to construct a mock novel configuration dictionary."""
    cfg: dict[str, Any] = {
        "novel_id": "pntt1",
        "title": "Phàm Nhân Tu Tiên",
        "author": "Vong Ngữ",
        "genre": "xianxia",
        "total_chapters": 10,
        "cleaning_patterns": [],
    }
    cfg.update(overrides)
    return cfg


def test_integer_order_and_zero_padded_names(tmp_path: Path, repo: Repository) -> None:
    """Verify sorting chapters by integer N with zero-padded names and chapter_10 after chapter_2."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    # Create files out of alphabetical order: 0001, 2, 10, and 3..9
    (raw_dir / "chapter_0001.md").write_text("# Chương 1\n\nKhởi đầu gian nan.", encoding="utf-8")
    (raw_dir / "chapter_2.md").write_text("# Chương 2\n\nBước vào sơn môn.", encoding="utf-8")
    (raw_dir / "chapter_10.md").write_text("# Chương 10\n\nLuyện chế đan dược.", encoding="utf-8")
    for i in range(3, 10):
        (raw_dir / f"chapter_{i:02d}.md").write_text(
            f"# Chương {i}\n\nTu luyện tầng thứ {i}.", encoding="utf-8"
        )

    cfg = make_config(total_chapters=10)
    result = run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)

    assert result.processed == 10
    assert result.written == 10
    assert result.skipped == 0

    chapters = repo.list_chapters(novel_id="pntt1", chapter_max=10)
    chapter_ids = [c["chapter_id"] for c in chapters]
    assert chapter_ids == list(range(1, 11))
    assert chapter_ids[1] == 2
    assert chapter_ids[-1] == 10


def test_missing_numbers_and_duplicates_reported_nothing_written(
    tmp_path: Path, repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify ChapterSetError reports all missing and duplicate numbers before writing anything."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    (raw_dir / "chapter_1.md").write_text("# Chương 1\n\nNội dung 1", encoding="utf-8")
    (raw_dir / "chapter_0001.md").write_text("# Chương 1 trùng\n\nNội dung 1 trùng", encoding="utf-8")
    (raw_dir / "chapter_3.md").write_text("# Chương 3\n\nNội dung 3", encoding="utf-8")
    (raw_dir / "chapter_5.md").write_text("# Chương 5\n\nNội dung 5", encoding="utf-8")
    # Expected: missing 2 and 4; duplicate 1
    cfg = make_config(total_chapters=5)

    with pytest.raises(ChapterSetError) as exc_info:
        run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)

    err = exc_info.value
    assert isinstance(err, ValueError)
    assert err.missing == [2, 4]
    assert err.duplicates == [1]
    assert "[2, 4]" in str(err)
    assert "[1]" in str(err)

    # Verify no database write occurred (including no novel row)
    assert repo.get_novel(novel_id="pntt1") is None
    assert db_conn.execute("SELECT count(*) FROM novels").fetchone()[0] == 0
    assert db_conn.execute("SELECT count(*) FROM chapters").fetchone()[0] == 0
    assert db_conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_non_matching_file_skipped_with_warning(
    tmp_path: Path, repo: Repository, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify non-matching files are skipped with a logging warning."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    (raw_dir / "chapter_1.md").write_text("# Chương 1\n\nĐoạn văn một.", encoding="utf-8")
    (raw_dir / "chapter_2.md").write_text("# Chương 2\n\nĐoạn văn hai.", encoding="utf-8")
    (raw_dir / "readme.txt").write_text("Hướng dẫn", encoding="utf-8")
    (raw_dir / "chapter_abc.md").write_text("Sai định dạng", encoding="utf-8")

    cfg = make_config(total_chapters=2)
    with caplog.at_level(logging.WARNING):
        res = run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)

    assert res.processed == 2
    assert res.written == 2
    assert any("readme.txt" in record.message for record in caplog.records)
    assert any("chapter_abc.md" in record.message for record in caplog.records)


def test_chapter_limit_allows_partial_folder_and_files_above_ignored(
    tmp_path: Path, repo: Repository
) -> None:
    """Verify chapter_limit allows processing a subset and ignores files above the limit."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    for i in range(1, 6):
        (raw_dir / f"chapter_{i}.md").write_text(f"# Chương {i}\n\nNội dung {i}.", encoding="utf-8")

    # Duplicate file above the limit (6) must be ignored without raising duplicate error
    (raw_dir / "chapter_6.md").write_text("# Chương 6\n\nNội dung 6.", encoding="utf-8")
    (raw_dir / "chapter_006.md").write_text("# Chương 6 trùng\n\nNội dung 6 trùng.", encoding="utf-8")

    cfg = make_config(total_chapters=10)
    res = run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg, chapter_limit=3)

    assert res.processed == 3
    assert res.written == 3
    chapters = repo.list_chapters(novel_id="pntt1", chapter_max=10)
    assert [c["chapter_id"] for c in chapters] == [1, 2, 3]


def test_front_matter_stripped_and_does_not_affect_hash(
    tmp_path: Path, repo: Repository
) -> None:
    """Verify YAML front matter is stripped and does not affect the sha256 clean_text hash."""
    raw_dir_1 = tmp_path / "raw_1"
    raw_dir_1.mkdir()
    (raw_dir_1 / "chapter_1.md").write_text(
        "---\n"
        "chapter: 1\n"
        "title: Khởi Đầu Ảo\n"
        "url: https://truyen.example/ch1\n"
        "scraped_at: 2026-01-01T00:00:00Z\n"
        "words: 1200\n"
        "---\n"
        "# Chương 1: Tiêu Đề Thật\n\n"
        "Hàn Lập ngẩng đầu nhìn lên bầu trời xanh thẳm.\n",
        encoding="utf-8-sig",
    )

    cfg = make_config(total_chapters=1)
    run_s0(repo, novel_id="pntt1", raw_dir=raw_dir_1, config=cfg)
    ch1 = repo.list_chapters(novel_id="pntt1", chapter_max=1)[0]

    # Front matter attributes must not leak into clean_text
    assert "scraped_at" not in ch1["clean_text"]
    assert "words" not in ch1["clean_text"]
    assert "https://truyen.example" not in ch1["clean_text"]
    assert "Khởi Đầu Ảo" not in ch1["clean_text"]

    # Second database with different front matter metadata but identical body
    raw_dir_2 = tmp_path / "raw_2"
    raw_dir_2.mkdir()
    (raw_dir_2 / "chapter_1.md").write_text(
        "---\n"
        "chapter: 1\n"
        "title: Khởi Đầu Khác\n"
        "scraped_at: 2026-10-31T23:59:59Z\n"
        "words: 8888\n"
        "---\n"
        "# Chương 1: Tiêu Đề Thật\n\n"
        "Hàn Lập ngẩng đầu nhìn lên bầu trời xanh thẳm.\n",
        encoding="utf-8-sig",
    )
    db2_file = tmp_path / "db2.db"
    conn2 = connect(db2_file)
    init_schema(conn2)
    repo2 = Repository(conn2)
    try:
        run_s0(repo2, novel_id="pntt1", raw_dir=raw_dir_2, config=cfg)
        ch2 = repo2.list_chapters(novel_id="pntt1", chapter_max=1)[0]
        assert ch1["clean_text"] == ch2["clean_text"]
        assert ch1["sha256"] == ch2["sha256"]
    finally:
        conn2.close()


def test_three_title_fallbacks(tmp_path: Path, repo: Repository) -> None:
    """Verify the three title extraction fallbacks in order of precedence."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    # 1. First heading matching ^#{1,3}\s*(.+)$
    (raw_dir / "chapter_1.md").write_text(
        "## Tiêu Đề Từ Heading Markdown\n\nĐoạn văn mở đầu.", encoding="utf-8"
    )
    # 2. First line matching ^Chương\s+\d+
    (raw_dir / "chapter_2.md").write_text(
        "Chương 2: Tiêu Đề Dòng Đầu Tiên\n\nĐoạn văn tiếp nối.", encoding="utf-8"
    )
    # 3. Fallback to Chương N
    (raw_dir / "chapter_3.md").write_text(
        "Hàn Lập im lặng bước đi trong đêm tối.\n\nKhông có tiêu đề nào.", encoding="utf-8"
    )

    cfg = make_config(total_chapters=3)
    run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)

    chapters = repo.list_chapters(novel_id="pntt1", chapter_max=3)
    assert chapters[0]["title"] == "Tiêu Đề Từ Heading Markdown"
    assert chapters[1]["title"] == "Chương 2: Tiêu Đề Dòng Đầu Tiên"
    assert chapters[2]["title"] == "Chương 3"


def test_markdown_cleanup_images_links_html_paragraphs(
    tmp_path: Path, repo: Repository
) -> None:
    """Verify markdown-it conversion drops images, links, HTML and keeps paragraphs and softbreaks."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    content = (
        "# Chương 1: Thử Thách\n\n"
        "Đoạn một có ảnh ![Hình bảo vật](https://example.com/kiem.png) ở giữa câu.\n\n"
        "Đoạn hai có [liên kết tham khảo](https://example.com/ref) và <span class='gold'>chữ vàng HTML</span> inline.\n\n"
        "<div class='watermark-block'><p>Nội dung HTML block cần loại bỏ</p></div>\n\n"
        "Đoạn ba có dòng thứ nhất\n"
        "và dòng thứ hai nối tiếp nhau bằng softbreak."
    )
    (raw_dir / "chapter_1.md").write_text(content, encoding="utf-8")

    cfg = make_config(total_chapters=1)
    run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)

    ch = repo.list_chapters(novel_id="pntt1", chapter_max=1)[0]
    clean = ch["clean_text"]

    # Image dropped completely
    assert "Hình bảo vật" not in clean
    assert "kiem.png" not in clean
    # Link dropped with link text
    assert "liên kết tham khảo" not in clean
    assert "https://example.com/ref" not in clean
    # Inline HTML tags dropped, inner text retained
    assert "<span" not in clean
    assert "class='gold'" not in clean
    assert "chữ vàng HTML" in clean
    # HTML block dropped completely
    assert "Nội dung HTML block cần loại bỏ" not in clean
    assert "<div" not in clean
    # Soft breaks converted to newlines
    assert "dòng thứ nhất\nvà dòng thứ hai" in clean
    # Paragraph separation preserved
    assert "\n\n" in clean


def test_nfc_normalization_with_nfd_input(tmp_path: Path, repo: Repository) -> None:
    """Verify that input text in NFD form is converted to NFC form."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    nfd_text = unicodedata.normalize("NFD", "Hàn Lập bái nhập Thất Huyền Môn.")
    assert not unicodedata.is_normalized("NFC", nfd_text)

    (raw_dir / "chapter_1.md").write_text(f"# Chương 1\n\n{nfd_text}", encoding="utf-8")
    cfg = make_config(total_chapters=1)
    run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)

    ch = repo.list_chapters(novel_id="pntt1", chapter_max=1)[0]
    clean = ch["clean_text"]
    assert unicodedata.is_normalized("NFC", clean)
    assert clean == unicodedata.normalize("NFC", clean)


def test_cleaning_pattern_applied(tmp_path: Path, repo: Repository) -> None:
    """Verify regex cleaning patterns from config are applied line by line."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    (raw_dir / "chapter_1.md").write_text(
        "# Chương 1\n\n"
        "Hàn Lập vận khí điều tức. [Truyện đăng tải tại webxian.vn]\n\n"
        "Nguồn: truyenchu.vn\n\n"
        "Khí tức đan điền dần dần bình ổn.",
        encoding="utf-8",
    )
    cfg = make_config(
        total_chapters=1,
        cleaning_patterns=[r"\[Truyện đăng tải tại .*?\]", r"^Nguồn:.*$"],
    )
    run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)

    ch = repo.list_chapters(novel_id="pntt1", chapter_max=1)[0]
    clean = ch["clean_text"]
    assert "webxian.vn" not in clean
    assert "Nguồn:" not in clean
    assert "Hàn Lập vận khí điều tức." in clean
    assert "Khí tức đan điền dần dần bình ổn." in clean


def test_watermark_rule_above_5_percent_and_at_least_3(
    tmp_path: Path, repo: Repository
) -> None:
    """Verify watermark lines (> 5% of chapters AND >= 3 chapters) are dropped, while others are kept."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    # 7 chapters in run:
    # 5% of 7 is 0.35.
    # Line A appears in 3 chapters (1, 2, 3) -> count = 3 > 0.35 and >= 3 -> REMOVED.
    # Line B appears in 2 chapters (1, 2) -> count = 2 > 0.35, but < 3 -> KEPT.
    watermark_line_a = "Truyện chỉ được đăng tải chính thức tại trang bachngockiem.vn"
    non_watermark_line_b = "Lời bình của người dịch"

    for i in range(1, 8):
        parts = [f"# Chương {i}", f"Nội dung tu luyện chương thứ {i}."]
        if i in (1, 2, 3):
            parts.append(watermark_line_a)
        if i in (1, 2):
            parts.append(non_watermark_line_b)
        (raw_dir / f"chapter_{i}.md").write_text("\n\n".join(parts), encoding="utf-8")

    cfg = make_config(total_chapters=7)
    run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)

    chapters = repo.list_chapters(novel_id="pntt1", chapter_max=7)
    for ch in chapters:
        assert watermark_line_a not in ch["clean_text"]

    assert non_watermark_line_b in chapters[0]["clean_text"]
    assert non_watermark_line_b in chapters[1]["clean_text"]


def test_sha256_and_token_count_stored_job_row_done(
    tmp_path: Path, repo: Repository
) -> None:
    """Verify sha256 and token_count are correctly computed and jobs row status is done."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    (raw_dir / "chapter_1.md").write_text(
        "# Chương 1\n\nNội dung chương kiểm tra băm và đếm token.", encoding="utf-8"
    )
    cfg = make_config(total_chapters=1)
    run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)

    ch = repo.list_chapters(novel_id="pntt1", chapter_max=1)[0]
    expected_hash = hashlib.sha256(ch["clean_text"].encode("utf-8")).hexdigest()
    assert ch["sha256"] == expected_hash
    assert ch["token_count"] == math.ceil(len(ch["clean_text"]) / CHARS_PER_TOKEN)

    job = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s0")
    assert job is not None
    assert job["status"] == "done"
    assert job["input_hash"] == expected_hash


def test_novels_row_created_when_missing_left_alone_when_present(
    tmp_path: Path, repo: Repository
) -> None:
    """Verify novels row is created from config if absent, and left untouched if present."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    (raw_dir / "chapter_1.md").write_text("# Chương 1\n\nNội dung.", encoding="utf-8")
    cfg = make_config(total_chapters=1, title="Tiêu Đề Config", author="Tác Giả Config")

    # 1. Missing novel row: created
    assert repo.get_novel(novel_id="pntt1") is None
    run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)
    novel = repo.get_novel(novel_id="pntt1")
    assert novel is not None
    assert novel["title"] == "Tiêu Đề Config"
    assert novel["author"] == "Tác Giả Config"
    assert novel["genre_profile"] == "xianxia"
    assert novel["total_chapters"] == 1

    # 2. Existing novel row: left alone
    repo._conn.execute("UPDATE novels SET title = 'Tiêu Đề Tùy Biến' WHERE novel_id = 'pntt1'")
    repo._conn.commit()

    run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)
    novel_after = repo.get_novel(novel_id="pntt1")
    assert novel_after is not None
    assert novel_after["title"] == "Tiêu Đề Tùy Biến"


def test_second_run_idempotent_no_writes(tmp_path: Path, repo: Repository) -> None:
    """Verify that a second run with identical files skips all chapters and performs no writes."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    for i in range(1, 3):
        (raw_dir / f"chapter_{i}.md").write_text(f"# Chương {i}\n\nNội dung {i}.", encoding="utf-8")

    cfg = make_config(total_chapters=2)
    res1 = run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)
    assert res1.processed == 2
    assert res1.written == 2
    assert res1.skipped == 0

    job1_before = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s0")
    job2_before = repo.get_job(novel_id="pntt1", chapter_id=2, stage="s0")
    assert job1_before is not None and job2_before is not None

    res2 = run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)
    assert res2.processed == 2
    assert res2.written == 0
    assert res2.skipped == 2

    job1_after = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s0")
    job2_after = repo.get_job(novel_id="pntt1", chapter_id=2, stage="s0")
    assert job1_after is not None and job2_after is not None

    assert job1_after["updated_at"] == job1_before["updated_at"]
    assert job1_after["attempts"] == job1_before["attempts"]
    assert job2_after["updated_at"] == job2_before["updated_at"]
    assert job2_after["attempts"] == job2_before["attempts"]


def test_changed_source_text_rewrites_that_chapter_only(
    tmp_path: Path, repo: Repository
) -> None:
    """Verify that modifying one chapter causes only that chapter to be rewritten on rerun."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    (raw_dir / "chapter_1.md").write_text("# Chương 1\n\nNội dung 1 cũ.", encoding="utf-8")
    (raw_dir / "chapter_2.md").write_text("# Chương 2\n\nNội dung 2 cũ.", encoding="utf-8")
    cfg = make_config(total_chapters=2)

    run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)
    job1_initial = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s0")
    job2_initial = repo.get_job(novel_id="pntt1", chapter_id=2, stage="s0")
    assert job1_initial is not None and job2_initial is not None

    # Change only chapter 2
    (raw_dir / "chapter_2.md").write_text("# Chương 2\n\nNội dung 2 đã được cập nhật.", encoding="utf-8")

    res2 = run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg)
    assert res2.processed == 2
    assert res2.written == 1
    assert res2.skipped == 1

    job1_after = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s0")
    job2_after = repo.get_job(novel_id="pntt1", chapter_id=2, stage="s0")
    assert job1_after is not None and job2_after is not None

    # Chapter 1 was skipped
    assert job1_after["updated_at"] == job1_initial["updated_at"]
    assert job1_after["input_hash"] == job1_initial["input_hash"]

    # Chapter 2 was rewritten
    assert job2_after["input_hash"] != job2_initial["input_hash"]
    ch2 = repo.list_chapters(novel_id="pntt1", chapter_max=2)[1]
    assert "đã được cập nhật" in ch2["clean_text"]


def test_upsert_job_attempts_and_updated_at_behavior(repo: Repository) -> None:
    """Verify attempts and updated_at behavior across job statuses."""
    repo.add_novel(novel_id="pntt1", title="Title", genre_profile="xianxia", total_chapters=10)

    # 1. New row with non-running status starts at attempts = 0
    repo.upsert_job(novel_id="pntt1", chapter_id=1, stage="s0", status="pending")
    job = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s0")
    assert job is not None
    assert job["attempts"] == 0
    assert job["updated_at"] is not None

    # 2. Update to running increments attempts to 1
    repo.upsert_job(novel_id="pntt1", chapter_id=1, stage="s0", status="running")
    job = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s0")
    assert job is not None
    assert job["attempts"] == 1

    # 3. Update to running again increments attempts to 2
    repo.upsert_job(novel_id="pntt1", chapter_id=1, stage="s0", status="running")
    job = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s0")
    assert job is not None
    assert job["attempts"] == 2

    # 4. Update to done leaves attempts at 2
    repo.upsert_job(novel_id="pntt1", chapter_id=1, stage="s0", status="done")
    job = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s0")
    assert job is not None
    assert job["attempts"] == 2

    # 5. Brand new row with running status starts at attempts = 1
    repo.upsert_job(novel_id="pntt1", chapter_id=2, stage="s0", status="running")
    job2 = repo.get_job(novel_id="pntt1", chapter_id=2, stage="s0")
    assert job2 is not None
    assert job2["attempts"] == 1


def test_save_chapter_leaves_nothing_if_job_write_fails(
    repo: Repository, db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify transaction atomicity: failure during job write rolls back chapter write."""
    repo.add_novel(novel_id="pntt1", title="Title", genre_profile="xianxia", total_chapters=10)

    def failing_upsert(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Simulated failure during job write")

    monkeypatch.setattr(repo, "_upsert_job", failing_upsert)

    with pytest.raises(RuntimeError, match="Simulated failure during job write"):
        repo.save_chapter(
            novel_id="pntt1",
            chapter_id=1,
            title="Chương 1",
            clean_text="Nội dung kiểm tra thất bại",
            sha256="fake_sha",
            token_count=10,
            stage="s0",
            input_hash="fake_sha",
        )

    # Neither chapter nor job should exist
    ch = db_conn.execute("SELECT * FROM chapters WHERE novel_id = 'pntt1' AND chapter_id = 1").fetchone()
    assert ch is None
    job = db_conn.execute("SELECT * FROM jobs WHERE novel_id = 'pntt1' AND chapter_id = 1").fetchone()
    assert job is None


def test_save_chapter_preserves_existing_arc_id(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify that save_chapter preserves an existing arc_id on the chapters row."""
    repo.add_novel(novel_id="pntt1", title="Title", genre_profile="xianxia", total_chapters=10)

    # Initial save
    repo.save_chapter(
        novel_id="pntt1",
        chapter_id=1,
        title="Chương 1 Ban Đầu",
        clean_text="Nội dung ban đầu",
        sha256="hash1",
        token_count=10,
        stage="s0",
        input_hash="hash1",
    )

    # Set arc_id manually
    db_conn.execute("UPDATE chapters SET arc_id = 42 WHERE novel_id = 'pntt1' AND chapter_id = 1")
    db_conn.commit()

    # Re-save chapter with updated text
    repo.save_chapter(
        novel_id="pntt1",
        chapter_id=1,
        title="Chương 1 Đã Cập Nhật",
        clean_text="Nội dung đã cập nhật",
        sha256="hash2",
        token_count=20,
        stage="s0",
        input_hash="hash2",
    )

    ch = db_conn.execute("SELECT * FROM chapters WHERE novel_id = 'pntt1' AND chapter_id = 1").fetchone()
    assert ch is not None
    assert ch["title"] == "Chương 1 Đã Cập Nhật"
    assert ch["clean_text"] == "Nội dung đã cập nhật"
    assert ch["arc_id"] == 42


def test_import_pipeline_modules_does_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that importing xn.text and xn.pipeline.s0_parse performs no file I/O."""
    sys.modules.pop("xn.text", None)
    sys.modules.pop("xn.pipeline.s0_parse", None)
    sys.modules.pop("xn.pipeline", None)

    opened_files: list[str] = []
    orig_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        fname = str(file)
        if not (fname.endswith(".py") or fname.endswith(".pyc")):
            opened_files.append(fname)
        return orig_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    import xn.text  # noqa: F401
    import xn.pipeline.s0_parse  # noqa: F401
    import xn.pipeline  # noqa: F401

    assert opened_files == []
