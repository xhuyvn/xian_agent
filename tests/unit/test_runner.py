"""Unit tests for the pipeline runner and xn run CLI."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any
import pytest
from typer.testing import CliRunner

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from xn.cli.main import app
from xn.config import load_config
from xn.pipeline.runner import STAGES, RunRequestError, run_stage
from xn.pipeline.s0_parse import ChapterSetError, S0Result, run_s0
from xn.pipeline.s1_extract import S1Result, run_s1
from xn.pipeline.s2_resolve import S2Result, S2StaleError, run_s2
from xn.pipeline.s3_ledger import S3Result, compute_input_hash, run_s3
from xn.prompts import load_prompt, profile_from_config
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

SUMMARY_200 = (
    "Hàn Lập tiến vào Thất Huyền Môn, bắt đầu con đường tu tiên gian nan nhưng đầy kiên định. "
    "Trải qua nhiều thử thách tại Thất Huyền Môn, hắn dần tích lũy kinh nghiệm, tìm kiếm cơ duyên "
    "và chuẩn bị cho những bước tiến xa hơn trên con đường tu tiên đầy hiểm nguy phía trước."
)

ALTERED_SUMMARY_200 = (
    "Nội dung tóm tắt chương truyện đã được cập nhật với nhiều tình tiết mới mẻ và biến cố bất ngờ. "
    "Hàn Lập tiếp tục nỗ lực tu luyện không ngừng nghỉ, vượt qua mọi cản trở để nâng cao tu vi "
    "trong môi trường đầy hiểm hách và khắc nghiệt của thế giới tu chân vô tận này."
)


def create_chapter_files(raw_dir: Path, count: int = 3) -> None:
    """Create raw chapter Markdown files with unique lines to avoid S0 watermark stripping."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    contents = {
        1: (
            "# Chương 1: Thiếu Niên Xuất Thân\n\n"
            "Hàn Lập sinh ra tại một thôn trang nghèo khó ở phương nam.\n"
            "Hắn nhặt được một cái bình nhỏ màu xanh lục dưới chân vách núi kỳ bí.\n"
            "Vật phẩm này tỏa ra linh khí mờ ảo khiến tâm thần hắn chấn động mạnh mẽ."
        ),
        2: (
            "# Chương 2: Nhập Môn Khảo Hạch\n\n"
            "Trương Thiết cùng Hàn Lập bước vào Thất Huyền Môn để tầm sư học đạo.\n"
            "Trương Thiết luyện tập tượng giáp công trong sân nhỏ vô cùng chăm chỉ.\n"
            "Cả hai cùng nhau vượt qua nhiều thử thách khắc nghiệt nơi sơn môn."
        ),
        3: (
            "# Chương 3: Phong Ba Hiểm Nguy\n\n"
            "Lệ Phi Vũ xuất hiện trên đỉnh núi cao lộng gió phía sau hậu sơn.\n"
            "Lệ Phi Vũ giao chiến với sát thủ Dã Lang Bang bảo vệ sư môn lâm nguy.\n"
            "Trận chiến diễn ra quyết liệt giữa bóng đêm mù mịt nơi rừng sâu hiểm trở."
        ),
        4: (
            "# Chương 4: Bế Quan Khổ Tu\n\n"
            "Mặc đại phu truyền thụ cho đệ tử khẩu quyết độc môn dưỡng khí.\n"
            "Hàn Lập bế quan tu luyện Trường Xuân Công suốt ba tháng ròng rã không nghỉ.\n"
            "Linh lực trong đan điền dần dần ngưng tụ thành từng luồng khí tức thanh khiết."
        ),
    }
    for i in range(1, count + 1):
        (raw_dir / f"chapter_{i}.md").write_text(contents[i], encoding="utf-8")


class FakeModelTracker:
    """Fake model tracking execution calls and returning valid Pydantic-AI responses."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, messages: list[Any], info: Any) -> ModelResponse:
        self.calls += 1
        tool_name = info.output_tools[0].name
        props = info.output_tools[0].parameters_json_schema.get("properties", {})

        if "events" in props:
            prompt_str = ""
            for m in messages:
                for p in getattr(m, "parts", []):
                    c = getattr(p, "content", None)
                    if isinstance(c, str):
                        prompt_str = c

            if "Trương Thiết" in prompt_str:
                cid = 2
                name = "Trương Thiết"
                evidence = "Trương Thiết luyện tập tượng giáp công"
            elif "Lệ Phi Vũ" in prompt_str:
                cid = 3
                name = "Lệ Phi Vũ"
                evidence = "Lệ Phi Vũ giao chiến với sát thủ"
            elif "Trường Xuân Công" in prompt_str:
                cid = 4
                name = "Hàn Lập"
                evidence = "Hàn Lập bế quan tu luyện Trường Xuân Công"
            else:
                cid = 1
                name = "Hàn Lập"
                evidence = "Hàn Lập sinh ra tại một thôn trang nghèo khó"

            payload = {
                "chapter_id": cid,
                "map_location": "Thất Huyền Môn",
                "location_change": False,
                "mc_realm": "Luyện Khí sơ kỳ",
                "realm_breakthrough": False,
                "major_conflict": False,
                "time_skip_years": 0.0,
                "micro_summary": SUMMARY_200,
                "events": [
                    {
                        "name": name,
                        "category": "character",
                        "event_type": "ACQUISITION",
                        "holder": name,
                        "tier_or_realm": "Luyện Khí",
                        "same_as": None,
                        "description": f"{name} trải qua biến cố tại Thất Huyền Môn.",
                        "evidence": evidence,
                    }
                ],
            }
            part = ToolCallPart(tool_name, payload)
            return ModelResponse(parts=[part], usage=RequestUsage(input_tokens=100, output_tokens=50))

        if "same_entity" in props:
            payload = {"same_entity": False, "reason": "Distinct entities in test"}
            part = ToolCallPart(tool_name, payload)
            return ModelResponse(parts=[part], usage=RequestUsage(input_tokens=50, output_tokens=20))

        payload = {"text": SUMMARY_200}
        part = ToolCallPart(tool_name, payload)
        return ModelResponse(parts=[part], usage=RequestUsage(input_tokens=50, output_tokens=20))


# ---------------------------------------------------------------------------
# Criterion 1: End-to-end s0, s1, s2, s3; second run identical & no model calls
# ---------------------------------------------------------------------------


def test_criterion_1_end_to_end_and_idempotent_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir = tmp_path / "raw"
    create_chapter_files(raw_dir, count=3)
    db_path = tmp_path / "xn_test.db"

    tracker = FakeModelTracker()
    fake_model = FunctionModel(tracker)
    monkeypatch.setattr("xn.llm.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s1_extract.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s2_resolve.local_model", lambda cfg: fake_model)

    runner = CliRunner()
    common_args = [
        "--novel", "pntt1",
        "--to", "3",
        "--db", str(db_path),
        "--raw-dir", str(raw_dir),
        "--configs-dir", str(CONFIGS_DIR),
        "--prompts-dir", str(PROMPTS_DIR),
    ]

    for stage in ("s0", "s1", "s2", "s3"):
        res = runner.invoke(app, ["run", "--stage", stage, *common_args])
        assert res.exit_code == 0, f"Stage {stage} failed: {res.output}"

    # Verify database contents
    conn = connect(db_path)
    try:
        events_count = conn.execute("SELECT COUNT(*) FROM entity_events WHERE novel_id = 'pntt1'").fetchone()[0]
        assert events_count >= 1

        chapters_1 = conn.execute("SELECT COUNT(*) FROM chapters WHERE novel_id = 'pntt1'").fetchone()[0]
        extractions_1 = conn.execute("SELECT COUNT(*) FROM extractions WHERE novel_id = 'pntt1'").fetchone()[0]
        entities_1 = conn.execute("SELECT COUNT(*) FROM entities WHERE novel_id = 'pntt1'").fetchone()[0]
        aliases_1 = conn.execute("SELECT COUNT(*) FROM entity_aliases WHERE novel_id = 'pntt1'").fetchone()[0]
        links_1 = conn.execute("SELECT COUNT(*) FROM entity_links WHERE novel_id = 'pntt1'").fetchone()[0]
        events_1 = events_count
        jobs_1 = conn.execute("SELECT COUNT(*) FROM jobs WHERE novel_id = 'pntt1'").fetchone()[0]
    finally:
        conn.close()

    calls_first_run = tracker.calls
    assert calls_first_run > 0

    # Run all four stages a second time
    for stage in ("s0", "s1", "s2", "s3"):
        res = runner.invoke(app, ["run", "--stage", stage, *common_args])
        assert res.exit_code == 0, f"Stage {stage} second run failed: {res.output}"

    # Verify identical counts and model was not called
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM chapters WHERE novel_id = 'pntt1'").fetchone()[0] == chapters_1
        assert conn.execute("SELECT COUNT(*) FROM extractions WHERE novel_id = 'pntt1'").fetchone()[0] == extractions_1
        assert conn.execute("SELECT COUNT(*) FROM entities WHERE novel_id = 'pntt1'").fetchone()[0] == entities_1
        assert conn.execute("SELECT COUNT(*) FROM entity_aliases WHERE novel_id = 'pntt1'").fetchone()[0] == aliases_1
        assert conn.execute("SELECT COUNT(*) FROM entity_links WHERE novel_id = 'pntt1'").fetchone()[0] == links_1
        assert conn.execute("SELECT COUNT(*) FROM entity_events WHERE novel_id = 'pntt1'").fetchone()[0] == events_1
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE novel_id = 'pntt1'").fetchone()[0] == jobs_1
    finally:
        conn.close()

    assert tracker.calls == calls_first_run


# ---------------------------------------------------------------------------
# Criterion 2: --stale-only on s1 and s3 redoes only stale chapters
# ---------------------------------------------------------------------------


def test_criterion_2_stale_only_on_s1_and_s3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir = tmp_path / "raw"
    create_chapter_files(raw_dir, count=4)
    db_path = tmp_path / "xn_test.db"

    tracker = FakeModelTracker()
    fake_model = FunctionModel(tracker)
    monkeypatch.setattr("xn.llm.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s1_extract.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s2_resolve.local_model", lambda cfg: fake_model)

    runner = CliRunner()
    common_args = [
        "--novel", "pntt1",
        "--db", str(db_path),
        "--raw-dir", str(raw_dir),
        "--configs-dir", str(CONFIGS_DIR),
        "--prompts-dir", str(PROMPTS_DIR),
    ]

    # Run S0 for all 4 chapters
    res = runner.invoke(app, ["run", "--stage", "s0", "--to", "4", *common_args])
    assert res.exit_code == 0

    # Run S1 for chapters 1, 2, 3
    res = runner.invoke(app, ["run", "--stage", "s1", "--to", "3", *common_args])
    assert res.exit_code == 0

    # Intentionally corrupt chapters in jobs table:
    # - Chapter 1: remains done and up to date
    # - Chapter 2: status changed to 'failed'
    # - Chapter 3: input_hash altered (simulates sha256 changed)
    # - Chapter 4: never run (no s1 job)
    conn = connect(db_path)
    try:
        conn.execute("UPDATE jobs SET status = 'failed' WHERE novel_id = 'pntt1' AND chapter_id = 2 AND stage = 's1'")
        conn.execute("UPDATE jobs SET input_hash = 'changed_hash' WHERE novel_id = 'pntt1' AND chapter_id = 3 AND stage = 's1'")
        conn.commit()
    finally:
        conn.close()

    calls_before = tracker.calls

    # Run S1 with --stale-only
    res = runner.invoke(app, ["run", "--stage", "s1", "--to", "4", "--stale-only", *common_args])
    assert res.exit_code == 0
    assert "s1: processed=2 written=2 skipped=0 failed=0" in res.output
    assert tracker.calls == calls_before + 2

    # Verify chapter 4 still has no job, chapter 2 and 3 are now done
    conn = connect(db_path)
    try:
        j2 = conn.execute("SELECT status FROM jobs WHERE novel_id = 'pntt1' AND chapter_id = 2 AND stage = 's1'").fetchone()
        assert j2 is not None and j2[0] == "done"
        j3 = conn.execute("SELECT status FROM jobs WHERE novel_id = 'pntt1' AND chapter_id = 3 AND stage = 's1'").fetchone()
        assert j3 is not None and j3[0] == "done"
        j4 = conn.execute("SELECT status FROM jobs WHERE novel_id = 'pntt1' AND chapter_id = 4 AND stage = 's1'").fetchone()
        assert j4 is None
    finally:
        conn.close()

    # Now for S3:
    # First, run S2 for chapters 1..3
    res = runner.invoke(app, ["run", "--stage", "s2", "--to", "3", *common_args])
    assert res.exit_code == 0

    # Run S3 for chapters 1 and 2
    res = runner.invoke(app, ["run", "--stage", "s3", "--to", "2", *common_args])
    assert res.exit_code == 0

    # Alter chapter 2's extraction payload in extractions table using ALTERED_SUMMARY_200 (length >= 200)
    conn = connect(db_path)
    try:
        ext2 = conn.execute("SELECT payload FROM extractions WHERE novel_id = 'pntt1' AND chapter_id = 2").fetchone()[0]
        data2 = json.loads(ext2)
        data2["micro_summary"] = ALTERED_SUMMARY_200
        conn.execute(
            "UPDATE extractions SET payload = ? WHERE novel_id = 'pntt1' AND chapter_id = 2",
            (json.dumps(data2),),
        )
        conn.commit()
    finally:
        conn.close()

    # Run S3 with --stale-only for chapters 1..3
    # Chapter 1 is up to date. Chapter 2 payload changed. Chapter 3 never had an s3 job.
    res = runner.invoke(app, ["run", "--stage", "s3", "--to", "3", "--stale-only", *common_args])
    assert res.exit_code == 0
    assert "s3: processed=1 written=1 skipped=0 failed=0" in res.output

    conn = connect(db_path)
    try:
        j3_s3 = conn.execute("SELECT status FROM jobs WHERE novel_id = 'pntt1' AND chapter_id = 3 AND stage = 's3'").fetchone()
        assert j3_s3 is None  # Chapter 3 never run, not touched
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Criterion 3: --stale-only with nothing stale makes no model calls, no changes
# ---------------------------------------------------------------------------


def test_criterion_3_stale_only_nothing_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir = tmp_path / "raw"
    create_chapter_files(raw_dir, count=2)
    db_path = tmp_path / "xn_test.db"

    tracker = FakeModelTracker()
    fake_model = FunctionModel(tracker)
    monkeypatch.setattr("xn.llm.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s1_extract.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s2_resolve.local_model", lambda cfg: fake_model)

    runner = CliRunner()
    common_args = [
        "--novel", "pntt1",
        "--to", "2",
        "--db", str(db_path),
        "--raw-dir", str(raw_dir),
        "--configs-dir", str(CONFIGS_DIR),
        "--prompts-dir", str(PROMPTS_DIR),
    ]

    for stage in ("s0", "s1", "s2", "s3"):
        res = runner.invoke(app, ["run", "--stage", stage, *common_args])
        assert res.exit_code == 0

    calls_before = tracker.calls

    # Run S1 with --stale-only
    res1 = runner.invoke(app, ["run", "--stage", "s1", "--stale-only", *common_args])
    assert res1.exit_code == 0
    assert "s1: processed=0 written=0 skipped=0 failed=0" in res1.output
    assert tracker.calls == calls_before

    # Run S3 with --stale-only
    res3 = runner.invoke(app, ["run", "--stage", "s3", "--stale-only", *common_args])
    assert res3.exit_code == 0
    assert "s3: processed=0 written=0 skipped=0 failed=0" in res3.output
    assert tracker.calls == calls_before


# ---------------------------------------------------------------------------
# Criterion 4: Every refusal in section 2 raises RunRequestError & exits 1
# ---------------------------------------------------------------------------


def test_criterion_4_all_section_2_refusals_raise_and_cli_exits_1_no_traceback(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "refusals.db"
    conn = connect(db_path)
    init_schema(conn)
    repo = Repository(conn)
    cfg = load_config("pntt1", configs_dir=CONFIGS_DIR)

    raw_dir = tmp_path / "raw"
    create_chapter_files(raw_dir, count=2)

    # 1. Unknown stage
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s99", novel_id="pntt1", config=cfg)

    # 2. chapter_from below 1
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s1", novel_id="pntt1", config=cfg, chapter_from=0)

    # 3. chapter_from above chapter_to
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s1", novel_id="pntt1", config=cfg, chapter_from=5, chapter_to=3)

    # 4. stale_only on s0 or s2
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s0", novel_id="pntt1", config=cfg, stale_only=True, raw_dir=raw_dir)
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s2", novel_id="pntt1", config=cfg, stale_only=True)

    # 5. chapter_from above 1 on s0 or s2
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s0", novel_id="pntt1", config=cfg, chapter_from=2, raw_dir=raw_dir)
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s2", novel_id="pntt1", config=cfg, chapter_from=2)

    # 6. rebuild on any stage but s2
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s0", novel_id="pntt1", config=cfg, rebuild=True, raw_dir=raw_dir)
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s1", novel_id="pntt1", config=cfg, rebuild=True)
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s3", novel_id="pntt1", config=cfg, rebuild=True)

    # 7. raw_dir missing on s0
    with pytest.raises(RunRequestError):
        run_stage(repo, stage="s0", novel_id="pntt1", config=cfg, raw_dir=None)

    conn.close()

    # Now verify all refusals exit 1 with NO traceback via CliRunner
    runner = CliRunner()
    base_args = [
        "--novel", "pntt1",
        "--db", str(db_path),
        "--configs-dir", str(CONFIGS_DIR),
        "--prompts-dir", str(PROMPTS_DIR),
    ]

    cli_test_cases = [
        ["run", "--stage", "s99", *base_args],
        ["run", "--stage", "s1", "--from", "0", *base_args],
        ["run", "--stage", "s1", "--from", "5", "--to", "3", *base_args],
        ["run", "--stage", "s0", "--stale-only", "--raw-dir", str(raw_dir), *base_args],
        ["run", "--stage", "s2", "--stale-only", *base_args],
        ["run", "--stage", "s0", "--from", "2", "--raw-dir", str(raw_dir), *base_args],
        ["run", "--stage", "s2", "--from", "2", *base_args],
        ["run", "--stage", "s1", "--rebuild", *base_args],
        ["run", "--stage", "s3", "--rebuild", *base_args],
    ]

    for args in cli_test_cases:
        res = runner.invoke(app, args)
        assert res.exit_code == 1, f"Failed for args {args}: {res.output}"
        assert "Traceback" not in res.output, f"Traceback found in: {res.output}"
        assert res.output.strip().startswith("Error:")


# ---------------------------------------------------------------------------
# Criterion 5: --rebuild on s2 with ledger rows clears ledger and re-resolves
# ---------------------------------------------------------------------------


def test_criterion_5_rebuild_on_s2_clears_ledger_and_s3_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir = tmp_path / "raw"
    create_chapter_files(raw_dir, count=2)
    db_path = tmp_path / "xn_test.db"

    tracker = FakeModelTracker()
    fake_model = FunctionModel(tracker)
    monkeypatch.setattr("xn.llm.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s1_extract.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s2_resolve.local_model", lambda cfg: fake_model)

    runner = CliRunner()
    common_args = [
        "--novel", "pntt1",
        "--to", "2",
        "--db", str(db_path),
        "--raw-dir", str(raw_dir),
        "--configs-dir", str(CONFIGS_DIR),
        "--prompts-dir", str(PROMPTS_DIR),
    ]

    for stage in ("s0", "s1", "s2", "s3"):
        res = runner.invoke(app, ["run", "--stage", stage, *common_args])
        assert res.exit_code == 0

    conn = connect(db_path)
    try:
        events_count = conn.execute("SELECT COUNT(*) FROM entity_events WHERE novel_id = 'pntt1'").fetchone()[0]
        assert events_count > 0
        s3_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE novel_id = 'pntt1' AND stage = 's3'").fetchone()[0]
        assert s3_jobs > 0
    finally:
        conn.close()

    # Run S2 with --rebuild
    res = runner.invoke(app, ["run", "--stage", "s2", "--rebuild", *common_args])
    assert res.exit_code == 0

    # Ledger rows and S3 jobs must now be cleared
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM entity_events WHERE novel_id = 'pntt1'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE novel_id = 'pntt1' AND stage = 's3'").fetchone()[0] == 0
    finally:
        conn.close()

    # Run S3 again: rewrites the events
    res = runner.invoke(app, ["run", "--stage", "s3", *common_args])
    assert res.exit_code == 0

    conn = connect(db_path)
    try:
        new_events = conn.execute("SELECT COUNT(*) FROM entity_events WHERE novel_id = 'pntt1'").fetchone()[0]
        assert new_events > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Criterion 6: Stale S2 without --rebuild exits 1 and mentions --rebuild
# ---------------------------------------------------------------------------


def test_criterion_6_stale_s2_without_rebuild_mentions_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir = tmp_path / "raw"
    create_chapter_files(raw_dir, count=2)
    db_path = tmp_path / "xn_test.db"

    tracker = FakeModelTracker()
    fake_model = FunctionModel(tracker)
    monkeypatch.setattr("xn.llm.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s1_extract.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s2_resolve.local_model", lambda cfg: fake_model)

    runner = CliRunner()
    common_args = [
        "--novel", "pntt1",
        "--to", "2",
        "--db", str(db_path),
        "--raw-dir", str(raw_dir),
        "--configs-dir", str(CONFIGS_DIR),
        "--prompts-dir", str(PROMPTS_DIR),
    ]

    for stage in ("s0", "s1", "s2"):
        res = runner.invoke(app, ["run", "--stage", stage, *common_args])
        assert res.exit_code == 0

    # Modify chapter 1's extraction payload to invalidate S2 input_hash using ALTERED_SUMMARY_200 (length >= 200)
    conn = connect(db_path)
    try:
        ext1 = conn.execute("SELECT payload FROM extractions WHERE novel_id = 'pntt1' AND chapter_id = 1").fetchone()[0]
        data1 = json.loads(ext1)
        data1["micro_summary"] = ALTERED_SUMMARY_200
        conn.execute(
            "UPDATE extractions SET payload = ? WHERE novel_id = 'pntt1' AND chapter_id = 1",
            (json.dumps(data1),),
        )
        conn.commit()
    finally:
        conn.close()

    # Running S2 without --rebuild must fail and mention --rebuild
    res = runner.invoke(app, ["run", "--stage", "s2", *common_args])
    assert res.exit_code == 1
    assert "--rebuild" in res.output
    assert "Traceback" not in res.output


# ---------------------------------------------------------------------------
# Criterion 7: Failed or stopped stage exits 1; successful exits 0 & prints counts
# ---------------------------------------------------------------------------


def test_criterion_7_failed_or_stopped_exits_1_successful_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir = tmp_path / "raw"
    create_chapter_files(raw_dir, count=2)
    db_path = tmp_path / "xn_test.db"

    tracker = FakeModelTracker()
    fake_model = FunctionModel(tracker)
    monkeypatch.setattr("xn.llm.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s1_extract.local_model", lambda cfg: fake_model)
    monkeypatch.setattr("xn.pipeline.s2_resolve.local_model", lambda cfg: fake_model)

    runner = CliRunner()
    common_args = [
        "--novel", "pntt1",
        "--to", "2",
        "--db", str(db_path),
        "--raw-dir", str(raw_dir),
        "--configs-dir", str(CONFIGS_DIR),
        "--prompts-dir", str(PROMPTS_DIR),
    ]

    # Successful stage S0 exits 0 and prints counts
    res0 = runner.invoke(app, ["run", "--stage", "s0", *common_args])
    assert res0.exit_code == 0
    assert "s0: processed=2 written=2 skipped=0" in res0.output

    # Run S1 successfully
    res1 = runner.invoke(app, ["run", "--stage", "s1", *common_args])
    assert res1.exit_code == 0
    assert "s1: processed=2 written=2 skipped=0 failed=0" in res1.output

    # Attempt to run S3 without having run S2
    # S3 will stop at chapter 1 because S2 job is missing
    res3 = runner.invoke(app, ["run", "--stage", "s3", *common_args])
    assert res3.exit_code == 1
    assert "stopped_at=1" in res3.output


def test_cli_raw_dir_defaults_to_novel_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_chapter_files(tmp_path / "data" / "novels" / "pntt1" / "raw", count=2)
    monkeypatch.chdir(tmp_path)

    res = CliRunner().invoke(app, [
        "run", "--novel", "pntt1", "--stage", "s0", "--to", "2",
        "--configs-dir", str(CONFIGS_DIR),
        "--prompts-dir", str(PROMPTS_DIR),
    ])

    assert res.exit_code == 0, res.output
    assert "s0: processed=2 written=2 skipped=0" in res.output
    assert (tmp_path / "data" / "xn.db").is_file()


# ---------------------------------------------------------------------------
# Criterion 8: Subprocess python -m xn run --help exits 0 & pyproject declared
# ---------------------------------------------------------------------------


def test_criterion_8_module_execution_and_pyproject_scripts() -> None:
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
    proc = subprocess.run(
        [sys.executable, "-m", "xn", "run", "--help"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "--novel" in proc.stdout
    assert "--stage" in proc.stdout

    pyproject_text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'xn = "xn.cli.main:app"' in pyproject_text


# ---------------------------------------------------------------------------
# Criterion 9: run_s1 with only_chapters handles only those, empty handles none
# ---------------------------------------------------------------------------


def test_criterion_9_run_s1_with_only_chapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_dir = tmp_path / "raw"
    create_chapter_files(raw_dir, count=3)
    db_path = tmp_path / "xn_test.db"

    conn = connect(db_path)
    init_schema(conn)
    repo = Repository(conn)
    cfg = load_config("pntt1", configs_dir=CONFIGS_DIR)

    # Run S0 first so chapters exist
    run_s0(repo, novel_id="pntt1", raw_dir=raw_dir, config=cfg, chapter_limit=3)

    tracker = FakeModelTracker()
    fake_model = FunctionModel(tracker)

    # 1. only_chapters={2}
    res = run_s1(
        repo,
        novel_id="pntt1",
        config=cfg,
        chapter_to=3,
        only_chapters={2},
        model=fake_model,
        prompts_dir=PROMPTS_DIR,
    )
    assert res == S1Result(processed=1, written=1, skipped=0, failed=0)

    rows = conn.execute("SELECT chapter_id FROM extractions WHERE novel_id = 'pntt1' ORDER BY chapter_id").fetchall()
    assert [r[0] for r in rows] == [2]

    # 2. only_chapters=set() (empty set)
    res_empty = run_s1(
        repo,
        novel_id="pntt1",
        config=cfg,
        chapter_to=3,
        only_chapters=set(),
        model=fake_model,
        prompts_dir=PROMPTS_DIR,
    )
    assert res_empty == S1Result(processed=0, written=0, skipped=0, failed=0)

    # Extractions row count unchanged
    rows_after = conn.execute("SELECT chapter_id FROM extractions WHERE novel_id = 'pntt1' ORDER BY chapter_id").fetchall()
    assert [r[0] for r in rows_after] == [2]

    conn.close()
