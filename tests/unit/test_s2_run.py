r"""Unit tests for stage S2 entity resolution chapter driver."""

from __future__ import annotations

from collections import namedtuple
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
import pytest

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from xn.pipeline.s2_resolve import (
    STAGE,
    S2Result,
    S2StaleError,
    run_s2,
)
from xn.schemas.extraction import ChapterExtraction, EntityEvent
from xn.schemas.lifecycle import LifecycleEventType
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

SUMMARY_200 = (
    "Hàn Lập tiến vào Thất Huyền Môn, bắt đầu con đường tu tiên gian nan nhưng đầy kiên định. "
    "Trải qua nhiều thử thách tại Thất Huyền Môn, hắn dần tích lũy kinh nghiệm, tìm kiếm cơ duyên "
    "và chuẩn bị cho những bước tiến xa hơn trên con đường tu tiên đầy hiểm nguy phía trước."
)

_SnapshotBase = namedtuple("Snapshot", ["entities", "aliases", "links", "jobs"])


class Snapshot(_SnapshotBase):
    r"""Snapshot of entities, aliases, links, and jobs for a novel."""

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            if hasattr(self, key):
                return getattr(self, key)
            raise KeyError(key)
        return tuple.__getitem__(self, key)


def snapshot(conn: sqlite3.Connection, novel_id: str, *, comparable: bool = False) -> Snapshot:
    r"""Return snapshot of entities, aliases, links, and jobs in fixed order.

    - Default (comparable=False): jobs query is SELECT * (for before/after checks on one database).
    - comparable=True: jobs query selects every column except updated_at and attempts (keeping error),
      for comparing two separate runs across databases.
    """
    cur = conn.cursor()
    entities = [
        tuple(r)
        for r in cur.execute(
            "SELECT * FROM entities WHERE novel_id = ? ORDER BY entity_id",
            (novel_id,),
        ).fetchall()
    ]
    aliases = [
        tuple(r)
        for r in cur.execute(
            "SELECT * FROM entity_aliases WHERE novel_id = ? ORDER BY entity_id, alias_norm",
            (novel_id,),
        ).fetchall()
    ]
    links = [
        tuple(r)
        for r in cur.execute(
            "SELECT * FROM entity_links WHERE novel_id = ? ORDER BY entity_a, entity_b",
            (novel_id,),
        ).fetchall()
    ]
    if comparable:
        jobs_query = (
            "SELECT novel_id, chapter_id, stage, status, input_hash, prompt_version, model, error "
            "FROM jobs WHERE novel_id = ? AND stage = 's2' ORDER BY chapter_id"
        )
    else:
        jobs_query = (
            "SELECT * FROM jobs WHERE novel_id = ? AND stage = 's2' ORDER BY chapter_id"
        )
    jobs = [
        tuple(r)
        for r in cur.execute(jobs_query, (novel_id,)).fetchall()
    ]
    return Snapshot(entities=entities, aliases=aliases, links=links, jobs=jobs)


@pytest.fixture
def db_conn(tmp_path: Path):
    r"""Provide an initialized SQLite database connection."""
    db_file = tmp_path / "test_s2_run.db"
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
        "novel_id": "pntt1",
        "title": "Phàm Nhân Tu Tiên",
        "author": "Vong Ngữ",
        "genre": "xianxia",
        "total_chapters": 10,
        "models": {
            "local_extractor": {
                "name": "qwen2.5:14b",
                "serving_name": "qwen14b-12k",
                "base_url": "http://localhost:11434/v1",
                "temperature": 0.1,
            }
        },
        "categories": [
            "character",
            "weapon_treasure",
            "puppet_beast",
            "technique",
            "dungeon",
            "nhân vật",
        ],
        "event_types": {
            "ACQUISITION": "Nhận được thực thể",
            "UPGRADE": "Thăng cấp thực thể",
            "USED_IN_BATTLE": "Sử dụng trong chiến đấu",
            "REVEALED_IDENTITY": "Tiết lộ thân phận",
            "MENTIONED": "Nhắc tới",
        },
        "extraction_hints": [
            "Trích xuất thông tin chính xác từ văn bản chương truyện.",
        ],
        "realm_ladder": ["Luyện Khí", "Trúc Cơ", "Kết Đan", "Nguyên Anh"],
        "prompts": {
            "s2_adjudicate": "v1",
        },
    }
    cfg.update(overrides)
    return cfg


def make_canned_extraction(
    chapter_id: int = 1,
    events: list[dict[str, Any]] | None = None,
    micro_summary: str = SUMMARY_200,
    **overrides: Any,
) -> ChapterExtraction:
    r"""Helper to produce a valid ChapterExtraction model instance."""
    if events is None:
        events = [
            {
                "name": "Hàn Lập",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": "Hàn Lập",
                "tier_or_realm": "Luyện Khí kỳ",
                "same_as": None,
                "description": "Nhặt được bình nhỏ bí ẩn dưới chân núi.",
                "evidence": "Hàn Lập nhặt được bình nhỏ",
            }
        ]
    event_objs = [EntityEvent(**ev) for ev in events]
    data: dict[str, Any] = {
        "chapter_id": chapter_id,
        "map_location": "Thất Huyền Môn",
        "location_change": False,
        "mc_realm": "Luyện Khí sơ kỳ",
        "realm_breakthrough": False,
        "major_conflict": False,
        "time_skip_years": 0.0,
        "micro_summary": micro_summary,
        "events": event_objs,
    }
    data.update(overrides)
    return ChapterExtraction(**data)


def save_chapter_and_extraction(
    repo: Repository,
    *,
    novel_id: str = "pntt1",
    chapter_id: int = 1,
    title: str = "Chương",
    clean_text: str = "Hàn Lập tu luyện tại Thất Huyền Môn.",
    extraction: ChapterExtraction | None = None,
) -> ChapterExtraction:
    r"""Helper to seed novel, chapter, and extraction in database."""
    if repo.get_novel(novel_id=novel_id) is None:
        repo.add_novel(
            novel_id=novel_id,
            title="Phàm Nhân Tu Tiên",
            genre_profile="xianxia",
            total_chapters=10,
        )
    sha256 = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
    repo.save_chapter(
        novel_id=novel_id,
        chapter_id=chapter_id,
        title=f"{title} {chapter_id}",
        clean_text=clean_text,
        sha256=sha256,
        token_count=100,
        stage="s0",
        input_hash=sha256,
    )
    if extraction is None:
        extraction = make_canned_extraction(chapter_id=chapter_id)
    payload_str = extraction.model_dump_json()
    repo.save_extraction(
        novel_id=novel_id,
        chapter_id=chapter_id,
        payload=payload_str,
        prompt_version="v1hash01",
        model="qwen14b-12k",
        stage="s1",
        input_hash=sha256,
    )
    return extraction


class FakeAdjudicator:
    r"""Fake FunctionModel callable tracking calls and capturing prompts."""

    def __init__(
        self,
        decision_map: dict[str, bool] | None = None,
        default_decision: bool = True,
        reason: str = "Fake model reason",
    ) -> None:
        self.calls: int = 0
        self.captured_prompts: list[str] = []
        self.decision_map = decision_map or {}
        self.default_decision = default_decision
        self.reason = reason
        self.should_fail: bool = False

    def __call__(self, messages: list[Any], info: Any) -> ModelResponse:
        self.calls += 1
        prompt_str = ""
        for m in messages:
            for p in getattr(m, "parts", []):
                c = getattr(p, "content", None)
                if isinstance(c, str):
                    prompt_str = c
        self.captured_prompts.append(prompt_str)

        if self.should_fail:
            raise RuntimeError("Fake model failure during adjudication")

        same = self.default_decision
        for k, v in self.decision_map.items():
            if k in prompt_str:
                same = v
                break

        tool_name = info.output_tools[0].name
        payload = {"same_entity": same, "reason": self.reason}
        part = ToolCallPart(tool_name, payload)
        return ModelResponse(parts=[part], usage=RequestUsage(input_tokens=50, output_tokens=30))


def test_full_run_fills_tables_and_jobs(repo: Repository, db_conn: sqlite3.Connection) -> None:
    r"""Verify a full run fills entities, entity_aliases (with first_chapter), entity_links and jobs."""
    cfg = make_config(
        total_chapters=3,
        identity_links=[
            {
                "category": "character",
                "name_a": "Hàn Lập",
                "name_b": "Lệ Phi Vũ",
                "revealed_chapter": 3,
                "evidence": "Hàn Lập lấy danh nghĩa Lệ Phi Vũ",
            }
        ],
    )
    ext1 = make_canned_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Hàn Lập",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": "Hàn Lập",
                "tier_or_realm": "Luyện Khí",
                "same_as": None,
                "description": "Hàn Lập bước vào Thất Huyền Môn",
                "evidence": "Hàn Lập bước vào môn phái",
            }
        ],
    )
    ext2 = make_canned_extraction(
        chapter_id=2,
        events=[
            {
                "name": "Lệ Phi Vũ",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": "Lệ Phi Vũ",
                "tier_or_realm": "Phàm nhân",
                "same_as": None,
                "description": "Lệ Phi Vũ kết giao bằng hữu",
                "evidence": "Gặp gỡ Lệ Phi Vũ",
            },
            {
                "name": "Thanh Trúc Kiếm",
                "category": "weapon_treasure",
                "event_type": "ACQUISITION",
                "holder": "Hàn Lập",
                "tier_or_realm": "Pháp bảo",
                "same_as": None,
                "description": "Hàn Lập thu thập phi kiếm",
                "evidence": "Thanh Trúc Kiếm xuất thế",
            },
        ],
    )
    ext3 = make_canned_extraction(
        chapter_id=3,
        events=[
            {
                "name": "Hàn Lập",
                "category": "character",
                "event_type": "USED_IN_BATTLE",
                "holder": "Hàn Lập",
                "tier_or_realm": "Luyện Khí",
                "same_as": None,
                "description": "Hàn Lập xuất thủ đại chiến",
                "evidence": "Hàn Lập ra tay quyết liệt",
            }
        ],
    )
    save_chapter_and_extraction(repo, chapter_id=1, extraction=ext1)
    save_chapter_and_extraction(repo, chapter_id=2, extraction=ext2)
    save_chapter_and_extraction(repo, chapter_id=3, extraction=ext3)

    fake = FakeAdjudicator()
    res = run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(fake), prompts_dir=PROMPTS_DIR)

    assert res == S2Result(processed=3, written=3, skipped=0, failed=0, stopped_at=None)

    cur = db_conn.cursor()
    entities = cur.execute("SELECT * FROM entities WHERE novel_id = 'pntt1' ORDER BY entity_id").fetchall()
    assert len(entities) == 3

    hl = next(e for e in entities if e["canonical_name"] == "Hàn Lập")
    assert hl["first_chapter"] == 1
    assert hl["last_chapter"] == 3
    assert hl["mention_count"] >= 3

    lpv = next(e for e in entities if e["canonical_name"] == "Lệ Phi Vũ")
    assert lpv["first_chapter"] == 2
    assert lpv["last_chapter"] == 2

    ttk = next(e for e in entities if e["canonical_name"] == "Thanh Trúc Kiếm")
    assert ttk["first_chapter"] == 2
    assert ttk["category"] == "weapon_treasure"

    aliases = cur.execute("SELECT * FROM entity_aliases WHERE novel_id = 'pntt1' ORDER BY first_chapter").fetchall()
    hl_alias = next(a for a in aliases if a["alias_norm"] == "hàn lập")
    assert hl_alias["first_chapter"] == 1
    lpv_alias = next(a for a in aliases if a["alias_norm"] == "lệ phi vũ")
    assert lpv_alias["first_chapter"] == 2

    links = cur.execute("SELECT * FROM entity_links WHERE novel_id = 'pntt1'").fetchall()
    assert len(links) == 1
    assert links[0]["revealed_chapter"] == 3
    assert links[0]["entity_a"] == min(hl["entity_id"], lpv["entity_id"])
    assert links[0]["entity_b"] == max(hl["entity_id"], lpv["entity_id"])

    jobs = cur.execute("SELECT * FROM jobs WHERE novel_id = 'pntt1' AND stage = 's2' ORDER BY chapter_id").fetchall()
    assert len(jobs) == 3
    for j in jobs:
        assert j["status"] == "done"
        assert len(j["prompt_version"]) == 8
        assert j["model"] == "qwen14b-12k"
        assert j["input_hash"] is not None


def test_second_run_changes_no_row_and_makes_no_model_call(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""Verify a second run changes no row in any table and makes no model call."""
    cfg = make_config(total_chapters=2)
    ext1 = make_canned_extraction(chapter_id=1)
    ext2 = make_canned_extraction(chapter_id=2)
    save_chapter_and_extraction(repo, chapter_id=1, extraction=ext1)
    save_chapter_and_extraction(repo, chapter_id=2, extraction=ext2)

    fake1 = FakeAdjudicator()
    res1 = run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(fake1), prompts_dir=PROMPTS_DIR)
    assert res1.written == 2

    # Default comparable=False uses SELECT * on jobs, detecting any change to attempts, updated_at, or error
    snap_before = snapshot(db_conn, "pntt1")

    fake2 = FakeAdjudicator()
    res2 = run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(fake2), prompts_dir=PROMPTS_DIR)
    assert res2 == S2Result(processed=0, written=0, skipped=2, failed=0, stopped_at=None)
    assert fake2.calls == 0

    snap_after = snapshot(db_conn, "pntt1")
    assert snap_before == snap_after


def test_resuming_chapter_to_2_then_3(
    repo: Repository, db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    r"""Verify resuming chapter_to=2 then 3 gives same snapshot as one run to 3, and chapter 3 adjudication sees context from 1 and 2."""
    cfg = make_config(total_chapters=3)
    # Hàn Lập recurs across all 3 chapters, is a holder in Ch2, and has a reveal in Ch3.
    # Trưởng Lão in Ch1 and Ch2 recurs, and in Ch3 Mặc Trưởng Lão triggers adjudication against Trưởng Lão.
    ext1 = make_canned_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Hàn Lập",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": "Hàn Lập",
                "tier_or_realm": "Luyện Khí",
                "same_as": None,
                "description": "Hàn Lập nhập Thất Huyền Môn",
                "evidence": "Hàn Lập",
            },
            {
                "name": "Trưởng Lão",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Trúc Cơ",
                "same_as": None,
                "description": "Trưởng Lão Ch1 context marker",
                "evidence": "Ch1 evidence marker",
            },
        ],
    )
    ext2 = make_canned_extraction(
        chapter_id=2,
        events=[
            {
                "name": "Lệ Phi Vũ",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": "Lệ Phi Vũ",
                "tier_or_realm": "Phàm nhân",
                "same_as": None,
                "description": "Lệ Phi Vũ xuất hiện",
                "evidence": "Lệ Phi Vũ",
            },
            {
                "name": "Thanh Trúc Kiếm",
                "category": "weapon_treasure",
                "event_type": "ACQUISITION",
                "holder": "Hàn Lập",
                "tier_or_realm": "Pháp bảo",
                "same_as": None,
                "description": "Bảo kiếm của Hàn Lập",
                "evidence": "Thanh Trúc Kiếm",
            },
            {
                "name": "Trưởng Lão",
                "category": "character",
                "event_type": "USED_IN_BATTLE",
                "holder": None,
                "tier_or_realm": "Trúc Cơ",
                "same_as": None,
                "description": "Trưởng Lão Ch2 context marker",
                "evidence": "Ch2 evidence marker",
            },
        ],
    )
    ext3 = make_canned_extraction(
        chapter_id=3,
        events=[
            {
                "name": "Hàn Lập",
                "category": "character",
                "event_type": "REVEALED_IDENTITY",
                "holder": None,
                "tier_or_realm": "Luyện Khí",
                "same_as": "Lệ Phi Vũ",
                "description": "Hàn Lập tiết lộ danh tính Lệ Phi Vũ",
                "evidence": "Hàn Lập chính là Lệ Phi Vũ",
            },
            {
                "name": "Mặc Trưởng Lão",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Trúc Cơ",
                "same_as": None,
                "description": "Mặc Trưởng Lão Ch3 context marker",
                "evidence": "Ch3 evidence marker",
            },
        ],
    )
    save_chapter_and_extraction(repo, chapter_id=1, extraction=ext1)
    save_chapter_and_extraction(repo, chapter_id=2, extraction=ext2)
    save_chapter_and_extraction(repo, chapter_id=3, extraction=ext3)

    # Resumed run: chapter_to=2, then chapter_to=3
    fake1 = FakeAdjudicator(default_decision=True)
    res1 = run_s2(repo, novel_id="pntt1", config=cfg, chapter_to=2, model=FunctionModel(fake1), prompts_dir=PROMPTS_DIR)
    assert res1.written == 2
    assert res1.skipped == 0

    fake2 = FakeAdjudicator(default_decision=True)
    res2 = run_s2(repo, novel_id="pntt1", config=cfg, chapter_to=3, model=FunctionModel(fake2), prompts_dir=PROMPTS_DIR)
    assert res2.skipped == 2
    assert res2.written == 1
    assert fake2.calls == 1

    prompt_captured = fake2.captured_prompts[0]
    assert "Trưởng Lão Ch1 context marker | Ch1 evidence marker" in prompt_captured
    assert "Trưởng Lão Ch2 context marker | Ch2 evidence marker" in prompt_captured

    # Single clean run straight through chapter_to=3 in a fresh database
    clean_conn = connect(tmp_path / "clean_resume.db")
    init_schema(clean_conn)
    clean_repo = Repository(clean_conn)
    save_chapter_and_extraction(clean_repo, chapter_id=1, extraction=ext1)
    save_chapter_and_extraction(clean_repo, chapter_id=2, extraction=ext2)
    save_chapter_and_extraction(clean_repo, chapter_id=3, extraction=ext3)

    fake_clean = FakeAdjudicator(default_decision=True)
    res_clean = run_s2(clean_repo, novel_id="pntt1", config=cfg, chapter_to=3, model=FunctionModel(fake_clean), prompts_dir=PROMPTS_DIR)
    assert res_clean.written == 3
    assert res_clean.skipped == 0
    assert fake_clean.calls == 1

    snap_resumed = snapshot(db_conn, "pntt1", comparable=True)
    snap_clean = snapshot(clean_conn, "pntt1", comparable=True)
    assert snap_resumed == snap_clean
    clean_conn.close()

    # Meaningful counts verified
    cur = db_conn.cursor()
    hl = cur.execute("SELECT * FROM entities WHERE canonical_name = 'Hàn Lập'").fetchone()
    assert hl is not None
    assert hl["first_chapter"] == 1
    assert hl["last_chapter"] == 3
    assert hl["mention_count"] >= 3

    links = cur.execute("SELECT * FROM entity_links WHERE novel_id = 'pntt1'").fetchall()
    assert len(links) == 1
    assert links[0]["revealed_chapter"] == 3


def test_spoiler_bound(repo: Repository) -> None:
    r"""Verify an adjudication triggered in chapter 2 has no chapter 3 marker in captured prompts, even after restart."""
    cfg = make_config(total_chapters=3)
    ext1 = make_canned_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Trưởng Lão",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Trúc Cơ",
                "same_as": None,
                "description": "DESC_CH1_MARKER_ALPHA",
                "evidence": "EVID_CH1_MARKER_ALPHA",
            }
        ],
    )
    ext2 = make_canned_extraction(
        chapter_id=2,
        events=[
            {
                "name": "Mặc Trưởng Lão",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Trúc Cơ",
                "same_as": None,
                "description": "DESC_CH2_MARKER_BETA",
                "evidence": "EVID_CH2_MARKER_BETA",
            }
        ],
    )
    ext3 = make_canned_extraction(
        chapter_id=3,
        events=[
            {
                "name": "Trưởng Lão",
                "category": "character",
                "event_type": "USED_IN_BATTLE",
                "holder": None,
                "tier_or_realm": "Trúc Cơ",
                "same_as": None,
                "description": "DESC_CH3_SPOILER_FUTURE",
                "evidence": "EVID_CH3_SPOILER_FUTURE",
            }
        ],
    )
    save_chapter_and_extraction(repo, chapter_id=1, extraction=ext1)
    save_chapter_and_extraction(repo, chapter_id=2, extraction=ext2)
    save_chapter_and_extraction(repo, chapter_id=3, extraction=ext3)

    # 1. First run does chapter 1 with chapter_to=1
    fake_ch1 = FakeAdjudicator(default_decision=True)
    res_ch1 = run_s2(repo, novel_id="pntt1", config=cfg, chapter_to=1, model=FunctionModel(fake_ch1), prompts_dir=PROMPTS_DIR)
    assert res_ch1.written == 1
    assert res_ch1.skipped == 0

    # 2. Resumed run uses chapter_to=3 with extractions for chapters 2 and 3 both present.
    # Start chapter is K=2. Chapter 2 triggers adjudication against Trưởng Lão (which also has a mention in chapter 3).
    fake_resumed = FakeAdjudicator(default_decision=True)
    res_resumed = run_s2(repo, novel_id="pntt1", config=cfg, chapter_to=3, model=FunctionModel(fake_resumed), prompts_dir=PROMPTS_DIR)
    assert res_resumed.skipped == 1
    assert res_resumed.written == 2
    assert fake_resumed.calls >= 1

    # Filter prompts sent while processing chapter 2 (identifiable by "Chapter: 2" in the <mention> block)
    ch2_prompts = [p for p in fake_resumed.captured_prompts if "Chapter: 2" in p]
    assert len(ch2_prompts) >= 1
    for p in ch2_prompts:
        assert "DESC_CH1_MARKER_ALPHA" in p
        assert "EVID_CH1_MARKER_ALPHA" in p
        assert "DESC_CH3_SPOILER_FUTURE" not in p
        assert "EVID_CH3_SPOILER_FUTURE" not in p


def test_identity_reveal_end_to_end(repo: Repository) -> None:
    r"""Verify identity reveal keeps entities A and B separate, bounded reads work, and find_entities_by_alias never returns A for B."""
    cfg = make_config(total_chapters=3)
    ext1 = make_canned_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Hàn Lập",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Luyện Khí",
                "same_as": None,
                "description": "Hàn Lập xuất hiện",
                "evidence": "Hàn Lập",
            }
        ],
    )
    ext2 = make_canned_extraction(
        chapter_id=2,
        events=[
            {
                "name": "Lệ Phi Vũ",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Phàm nhân",
                "same_as": None,
                "description": "Lệ Phi Vũ xuất hiện",
                "evidence": "Lệ Phi Vũ",
            }
        ],
    )
    ext3 = make_canned_extraction(
        chapter_id=3,
        events=[
            {
                "name": "Hàn Lập",
                "category": "character",
                "event_type": "REVEALED_IDENTITY",
                "holder": None,
                "tier_or_realm": "Luyện Khí",
                "same_as": "Lệ Phi Vũ",
                "description": "Hàn Lập dùng tên Lệ Phi Vũ hành tẩu",
                "evidence": "Hàn Lập chính là Lệ Phi Vũ",
            }
        ],
    )
    save_chapter_and_extraction(repo, chapter_id=1, extraction=ext1)
    save_chapter_and_extraction(repo, chapter_id=2, extraction=ext2)
    save_chapter_and_extraction(repo, chapter_id=3, extraction=ext3)

    run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)

    a_rows = repo.find_entities_by_alias(novel_id="pntt1", alias_norm="hàn lập", category="character", chapter_max=3)
    b_rows = repo.find_entities_by_alias(novel_id="pntt1", alias_norm="lệ phi vũ", category="character", chapter_max=3)

    assert len(a_rows) == 1
    assert len(b_rows) == 1
    eid_a = a_rows[0]["entity_id"]
    eid_b = b_rows[0]["entity_id"]
    assert eid_a != eid_b

    assert repo.get_identity_links(novel_id="pntt1", entity_id=eid_a, chapter_max=2) == []
    assert repo.get_identity_links(novel_id="pntt1", entity_id=eid_b, chapter_max=2) == []

    links = repo.get_identity_links(novel_id="pntt1", entity_id=eid_a, chapter_max=3)
    assert len(links) == 1
    assert links[0]["revealed_chapter"] == 3
    assert links[0]["entity_a"] == min(eid_a, eid_b)
    assert links[0]["entity_b"] == max(eid_a, eid_b)

    for ch_bound in (1, 2, 3):
        results = repo.find_entities_by_alias(novel_id="pntt1", alias_norm="lệ phi vũ", category="character", chapter_max=ch_bound)
        for r in results:
            assert r["entity_id"] != eid_a


def test_model_failure_in_chapter_2_of_3(
    repo: Repository, db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    r"""Verify model failure in chapter 2 stops run, marks job failed, leaves chapter 3 untouched, and resume recovers."""
    cfg = make_config(total_chapters=3)
    ext1 = make_canned_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Trưởng Lão",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Trúc Cơ",
                "same_as": None,
                "description": "Ch1 Trưởng Lão",
                "evidence": "Trưởng Lão",
            }
        ],
    )
    ext2 = make_canned_extraction(
        chapter_id=2,
        events=[
            {
                "name": "Mặc Trưởng Lão",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Trúc Cơ",
                "same_as": None,
                "description": "Ch2 Mặc Trưởng Lão triggers adjudication",
                "evidence": "Mặc Trưởng Lão",
            }
        ],
    )
    ext3 = make_canned_extraction(
        chapter_id=3,
        events=[
            {
                "name": "Thanh Trúc Kiếm",
                "category": "weapon_treasure",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Pháp bảo",
                "same_as": None,
                "description": "Ch3 Kiếm",
                "evidence": "Thanh Trúc Kiếm",
            }
        ],
    )
    save_chapter_and_extraction(repo, chapter_id=1, extraction=ext1)
    save_chapter_and_extraction(repo, chapter_id=2, extraction=ext2)
    save_chapter_and_extraction(repo, chapter_id=3, extraction=ext3)

    failing_fake = FakeAdjudicator()
    failing_fake.should_fail = True
    res_fail = run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(failing_fake), prompts_dir=PROMPTS_DIR)

    assert res_fail.stopped_at == 2
    assert res_fail.failed == 1
    assert res_fail.written == 1

    job1 = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s2")
    assert job1 is not None and job1["status"] == "done"

    job2 = repo.get_job(novel_id="pntt1", chapter_id=2, stage="s2")
    assert job2 is not None and job2["status"] == "failed"
    assert "Fake model failure" in (job2["error"] or "")

    cur = db_conn.cursor()
    assert cur.execute("SELECT * FROM entities WHERE novel_id = 'pntt1' AND first_chapter = 2").fetchall() == []
    assert cur.execute("SELECT * FROM entity_aliases WHERE novel_id = 'pntt1' AND first_chapter = 2").fetchall() == []
    assert cur.execute("SELECT * FROM entity_links WHERE novel_id = 'pntt1' AND revealed_chapter = 2").fetchall() == []

    assert repo.get_job(novel_id="pntt1", chapter_id=3, stage="s2") is None

    working_fake = FakeAdjudicator(default_decision=True)
    res_resume = run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(working_fake), prompts_dir=PROMPTS_DIR)

    assert res_resume.stopped_at is None
    assert res_resume.failed == 0
    assert res_resume.skipped == 1
    assert res_resume.written == 2

    # Verify error is null after recovery
    recovered_job2 = repo.get_job(novel_id="pntt1", chapter_id=2, stage="s2")
    assert recovered_job2 is not None
    assert recovered_job2["status"] == "done"
    assert recovered_job2["error"] is None

    # Clean run in separate DB for snapshot comparison with comparable=True
    clean_conn = connect(tmp_path / "clean.db")
    init_schema(clean_conn)
    clean_repo = Repository(clean_conn)
    save_chapter_and_extraction(clean_repo, chapter_id=1, extraction=ext1)
    save_chapter_and_extraction(clean_repo, chapter_id=2, extraction=ext2)
    save_chapter_and_extraction(clean_repo, chapter_id=3, extraction=ext3)

    clean_fake = FakeAdjudicator(default_decision=True)
    run_s2(clean_repo, novel_id="pntt1", config=cfg, model=FunctionModel(clean_fake), prompts_dir=PROMPTS_DIR)

    snap_recovered = snapshot(db_conn, "pntt1", comparable=True)
    snap_clean = snapshot(clean_conn, "pntt1", comparable=True)
    assert snap_recovered == snap_clean
    clean_conn.close()


def test_chapter_with_no_extraction_stops_run(repo: Repository) -> None:
    r"""Verify a chapter with no extraction stops run with stopped_at set, failed == 0, and no job row."""
    cfg = make_config(total_chapters=3)
    save_chapter_and_extraction(repo, chapter_id=1)
    for cid in (2, 3):
        repo.save_chapter(
            novel_id="pntt1",
            chapter_id=cid,
            title=f"Chương {cid}",
            clean_text=f"Văn bản chương {cid}",
            sha256=hashlib.sha256(f"text{cid}".encode()).hexdigest(),
            token_count=50,
            stage="s0",
            input_hash=f"hash{cid}",
        )

    res = run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)

    assert res.stopped_at == 2
    assert res.failed == 0
    assert res.written == 1

    job1 = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s2")
    assert job1 is not None and job1["status"] == "done"
    assert repo.get_job(novel_id="pntt1", chapter_id=2, stage="s2") is None
    assert repo.get_job(novel_id="pntt1", chapter_id=3, stage="s2") is None


def test_stale_detection_and_rebuild(repo: Repository, db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    r"""Verify staleness detection for changed extraction, seed_aliases, prompt_version, and rebuild=True clears and recovers."""
    cfg = make_config(total_chapters=2)
    ext1 = make_canned_extraction(chapter_id=1)
    ext2 = make_canned_extraction(chapter_id=2)
    save_chapter_and_extraction(repo, chapter_id=1, extraction=ext1)
    save_chapter_and_extraction(repo, chapter_id=2, extraction=ext2)

    fake = FakeAdjudicator()
    res1 = run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(fake), prompts_dir=PROMPTS_DIR)
    assert res1.written == 2

    # 1. Changing done chapter extraction raises S2StaleError naming it
    mod_ext1 = ext1.model_copy(update={"map_location": "Vạn Bảo Lâu mới"})
    repo.save_extraction(
        novel_id="pntt1",
        chapter_id=1,
        payload=mod_ext1.model_dump_json(),
        prompt_version="v1hash01",
        model="qwen14b-12k",
        stage="s1",
        input_hash="diffhash",
    )

    # Default comparable=False uses SELECT * on jobs, verifying no attempts/updated_at/error bump
    snap_before1 = snapshot(db_conn, "pntt1")
    fake_stale1 = FakeAdjudicator()
    call_count_before1 = fake_stale1.calls
    with pytest.raises(S2StaleError) as exc_info1:
        run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(fake_stale1), prompts_dir=PROMPTS_DIR)
    assert exc_info1.value.chapters == [1]
    assert "1" in str(exc_info1.value)
    assert "rebuild=True redoes the novel" in str(exc_info1.value)
    assert snapshot(db_conn, "pntt1") == snap_before1
    assert fake_stale1.calls == call_count_before1

    # Restore extraction
    repo.save_extraction(
        novel_id="pntt1",
        chapter_id=1,
        payload=ext1.model_dump_json(),
        prompt_version="v1hash01",
        model="qwen14b-12k",
        stage="s1",
        input_hash="orig",
    )

    # 2. Changing seed_aliases also raises
    snap_before2 = snapshot(db_conn, "pntt1")
    cfg_seeds = copy.deepcopy(cfg)
    cfg_seeds["seed_aliases"] = [{"category": "character", "names": ["Hàn Lão Ma", "Hàn Lập"]}]
    fake_stale2 = FakeAdjudicator()
    call_count_before2 = fake_stale2.calls
    with pytest.raises(S2StaleError) as exc_info2:
        run_s2(repo, novel_id="pntt1", config=cfg_seeds, model=FunctionModel(fake_stale2), prompts_dir=PROMPTS_DIR)
    assert 1 in exc_info2.value.chapters
    assert "rebuild=True redoes the novel" in str(exc_info2.value)
    assert snapshot(db_conn, "pntt1") == snap_before2
    assert fake_stale2.calls == call_count_before2

    # 3. Changing prompt version also raises
    prompts_copy = tmp_path / "prompts"
    p_sub = prompts_copy / "s2_adjudicate"
    p_sub.mkdir(parents=True, exist_ok=True)
    orig_text = (PROMPTS_DIR / "s2_adjudicate" / "v1.md").read_text(encoding="utf-8")
    (p_sub / "v1.md").write_text(orig_text, encoding="utf-8")
    (p_sub / "v2.md").write_text(orig_text + "\n# Modified prompt template", encoding="utf-8")

    cfg_prompt = copy.deepcopy(cfg)
    cfg_prompt["prompts"] = {"s2_adjudicate": "v2"}
    snap_before3 = snapshot(db_conn, "pntt1")
    fake_stale3 = FakeAdjudicator()
    call_count_before3 = fake_stale3.calls
    with pytest.raises(S2StaleError) as exc_info3:
        run_s2(repo, novel_id="pntt1", config=cfg_prompt, model=FunctionModel(fake_stale3), prompts_dir=prompts_copy)
    assert 1 in exc_info3.value.chapters
    assert "rebuild=True redoes the novel" in str(exc_info3.value)
    assert snapshot(db_conn, "pntt1") == snap_before3
    assert fake_stale3.calls == call_count_before3

    # 4. rebuild=True clears and rebuilds with fresh ids from 1
    res_rebuild = run_s2(repo, novel_id="pntt1", config=cfg, rebuild=True, model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)
    assert res_rebuild.written == 2
    cur = db_conn.cursor()
    ent = cur.execute("SELECT * FROM entities WHERE novel_id = 'pntt1'").fetchone()
    assert ent["entity_id"] == 1


def test_rebuild_raises_value_error_when_entity_events_exist(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""Verify rebuild=True raises ValueError when entity_events has rows, and changes nothing."""
    cfg = make_config(total_chapters=1)
    save_chapter_and_extraction(repo, chapter_id=1)
    run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)

    # Default comparable=False uses SELECT * on jobs
    snap_before = snapshot(db_conn, "pntt1")

    cur = db_conn.cursor()
    cur.execute(
        "INSERT INTO entity_events (novel_id, id, chapter_id, entity_id, event_type, ordinal, description) "
        "VALUES ('pntt1', 1, 1, 1, 'ACQUISITION', 1, 'Event description')"
    )
    db_conn.commit()

    with pytest.raises(ValueError, match="entity_events has rows"):
        run_s2(repo, novel_id="pntt1", config=cfg, rebuild=True, prompts_dir=PROMPTS_DIR)

    snap_after = snapshot(db_conn, "pntt1")
    assert snap_before == snap_after


def test_seed_name_never_used_has_no_alias_row(repo: Repository) -> None:
    r"""Verify a seed name never used in the text has no alias row."""
    cfg = make_config(
        total_chapters=1,
        seed_aliases=[
            {
                "category": "character",
                "names": ["Hàn Lập", "Hàn Thiên Tôn", "Nhị Lăng"],
            }
        ],
    )
    ext = make_canned_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Hàn Lập",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Luyện Khí",
                "same_as": None,
                "description": "Hàn Lập xuất hiện",
                "evidence": "Hàn Lập",
            }
        ],
    )
    save_chapter_and_extraction(repo, chapter_id=1, extraction=ext)

    run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)

    assert len(repo.find_aliases(novel_id="pntt1", alias_norm="hàn lập", chapter_max=1)) == 1
    assert len(repo.find_aliases(novel_id="pntt1", alias_norm="hàn thiên tôn", chapter_max=1)) == 0
    assert len(repo.find_aliases(novel_id="pntt1", alias_norm="nhị lăng", chapter_max=1)) == 0


def test_every_event_name_same_as_and_holder_maps_to_one_entity(repo: Repository) -> None:
    r"""Verify every event name, same_as, and holder maps to exactly one entity through find_entities_by_alias."""
    cfg = make_config(total_chapters=1)
    ext = make_canned_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Hàn Lập",
                "category": "character",
                "event_type": "REVEALED_IDENTITY",
                "holder": "Mặc Trưởng Lão",
                "tier_or_realm": "Luyện Khí",
                "same_as": "Hàn Lập Huynh",
                "description": "Tiết lộ danh tính",
                "evidence": "Minh chứng",
            }
        ],
    )
    save_chapter_and_extraction(repo, chapter_id=1, extraction=ext)

    run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)

    hl_matches = repo.find_entities_by_alias(novel_id="pntt1", alias_norm="hàn lập", category="character", chapter_max=1)
    same_matches = repo.find_entities_by_alias(novel_id="pntt1", alias_norm="hàn lập huynh", category="character", chapter_max=1)
    holder_matches = repo.find_entities_by_alias(novel_id="pntt1", alias_norm="mặc trưởng lão", category="character", chapter_max=1)

    assert len(hl_matches) == 1
    assert len(same_matches) == 1
    assert len(holder_matches) == 1


def test_two_novels_using_same_names_get_separate_entities(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""Verify two novels using the same names get separate entities and aliases."""
    cfg1 = make_config(novel_id="novel1", total_chapters=1)
    cfg2 = make_config(novel_id="novel2", total_chapters=1)

    save_chapter_and_extraction(repo, novel_id="novel1", chapter_id=1)
    save_chapter_and_extraction(repo, novel_id="novel2", chapter_id=1)

    run_s2(repo, novel_id="novel1", config=cfg1, model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)
    run_s2(repo, novel_id="novel2", config=cfg2, model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)

    res1 = repo.find_entities_by_alias(novel_id="novel1", alias_norm="hàn lập", category="character", chapter_max=1)
    res2 = repo.find_entities_by_alias(novel_id="novel2", alias_norm="hàn lập", category="character", chapter_max=1)

    assert len(res1) == 1
    assert len(res2) == 1
    assert res1[0]["entity_id"] == 1
    assert res2[0]["entity_id"] == 1

    cur = db_conn.cursor()
    n1_ents = cur.execute("SELECT * FROM entities WHERE novel_id = 'novel1'").fetchall()
    n2_ents = cur.execute("SELECT * FROM entities WHERE novel_id = 'novel2'").fetchall()
    assert len(n1_ents) == 1
    assert len(n2_ents) == 1


def test_one_llm_runs_row_per_adjudication(repo: Repository, db_conn: sqlite3.Connection) -> None:
    r"""Verify every adjudication creates exactly one llm_runs row with stage s2."""
    cfg = make_config(total_chapters=2)
    ext1 = make_canned_extraction(
        chapter_id=1,
        events=[
            {
                "name": "Trưởng Lão",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Trúc Cơ",
                "same_as": None,
                "description": "Trưởng Lão Ch1",
                "evidence": "Trưởng Lão",
            }
        ],
    )
    ext2 = make_canned_extraction(
        chapter_id=2,
        events=[
            {
                "name": "Mặc Trưởng Lão",
                "category": "character",
                "event_type": "ACQUISITION",
                "holder": None,
                "tier_or_realm": "Trúc Cơ",
                "same_as": None,
                "description": "Mặc Trưởng Lão Ch2",
                "evidence": "Mặc Trưởng Lão",
            }
        ],
    )
    save_chapter_and_extraction(repo, chapter_id=1, extraction=ext1)
    save_chapter_and_extraction(repo, chapter_id=2, extraction=ext2)

    fake = FakeAdjudicator(default_decision=True)
    run_s2(repo, novel_id="pntt1", config=cfg, model=FunctionModel(fake), prompts_dir=PROMPTS_DIR)

    assert fake.calls == 1

    cur = db_conn.cursor()
    runs = cur.execute("SELECT * FROM llm_runs WHERE novel_id = 'pntt1'").fetchall()
    assert len(runs) == 1
    r = runs[0]
    assert r["stage"] == "s2"
    assert r["model"] == "qwen14b-12k"
    assert r["chapter_id"] == 2
    assert r["ok"] == 1
    assert len(r["prompt_version"]) == 8
