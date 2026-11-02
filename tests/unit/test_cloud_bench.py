"""Unit tests for the cloud benchmark, using fake models and made-up chapters; no network."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
from typing import Any

from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest
from typer.testing import CliRunner
import typer

from xn import cloud_bench
from xn.cloud_bench import (
    MAX_CONCURRENCY,
    BenchRequestError,
    check_request,
    filter_events,
    name_overlap,
    pick_chapters,
    run_bench,
)
from xn.gold import GoldChapter, GoldEntity, GoldSet
from xn.gold_run import INVALID_CATEGORY, LOW_EVIDENCE
from xn.schemas.extraction import ChapterExtraction
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

REPO_ROOT = Path(__file__).resolve().parents[2]
TEXT = "Hàn Lập đi vào sơn cốc và gặp Trương Thiết ở đó vào lúc sáng sớm."
SUMMARY = "tóm tắt " * 40
CONFIG: dict[str, Any] = {
    "novel_id": "pntt1",
    "title": "Thử",
    "genre": "xianxia",
    "total_chapters": 4,
    "categories": ["character"],
    "event_types": {"MENTIONED": "x"},
    "prompts": {"s1_extract": "v2"},
    "models": {"local_extractor": {"serving_name": "fake-local", "temperature": 0.1}},
}


def gold_set(*ids: int) -> GoldSet:
    chapters = [
        GoldChapter(
            chapter_id=n, location_change=False, realm_breakthrough=False, major_conflict=False,
            entities=[GoldEntity(name="Hàn Lập", category="character", event_types=["MENTIONED"])],
        )
        for n in ids
    ]
    return GoldSet(meta={"chapter_max": 4}, chapters=chapters, aliases=[], qa=[], spoilers=[])


def payload(*names: str, category: str = "character", evidence: str = TEXT[:20]) -> str:
    return json.dumps({
        "chapter_id": 1, "map_location": "x", "location_change": False, "realm_breakthrough": False,
        "major_conflict": False, "micro_summary": SUMMARY,
        "events": [
            {"name": n, "category": category, "event_type": "MENTIONED", "description": "d", "evidence": evidence}
            for n in names
        ],
    })


def chapter_of(messages: list[ModelMessage]) -> int:
    """Read which fake chapter a request carries, from its marker."""
    text = str(messages[-1].parts[-1].content)
    return int(text.split("#")[1].split("#")[0])


def reply(content: str | None = None) -> FunctionModel:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content or payload("Hàn Lập"))])

    return FunctionModel(respond)


class Env:
    """Temporary raw folder, baseline database and benchmark paths."""

    def __init__(self, tmp_path: Path) -> None:
        self.raw = tmp_path / "raw"
        self.raw.mkdir()
        for n in range(1, 5):
            body = f"#{n}# {TEXT}\n" * 30
            (self.raw / f"chapter_{n:04d}.md").write_text(f"# Chương {n}: Tên\n\n{body}", encoding="utf-8")
        self.db = tmp_path / "bench.db"
        self.out = tmp_path / "report.json"
        self.baseline = tmp_path / "baseline.db"
        conn = connect(self.baseline)
        init_schema(conn)
        repo = Repository(conn)
        cloud_bench.run_s0(repo, novel_id="pntt1", raw_dir=self.raw, config=CONFIG, chapter_limit=4)
        for row in repo.list_chapters(novel_id="pntt1", chapter_max=4):
            repo.save_extraction(
                novel_id="pntt1", chapter_id=row["chapter_id"], payload=payload("Hàn Lập", "Trương Thiết"),
                prompt_version="local", model="fake-local", stage="s1", input_hash=row["sha256"],
            )
        conn.close()

    def run(self, **kwargs: Any) -> dict[str, Any]:
        async def no_sleep(_: float) -> None:
            return None

        options: dict[str, Any] = {
            "db": self.db, "raw_dir": self.raw, "out": self.out, "baseline_db": self.baseline,
            "config": CONFIG, "gold": gold_set(1, 2), "extra_chapters": 1, "allow_raw_egress": True,
            "sleep": no_sleep, "prompts_dir": REPO_ROOT / "prompts",
            "model_factory": lambda _: reply(),
        }
        return run_bench(**{**options, **kwargs})

    def llm_rows(self) -> list[sqlite3.Row]:
        conn = connect(self.db)
        try:
            return conn.execute("SELECT * FROM llm_runs ORDER BY rowid").fetchall()
        finally:
            conn.close()


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_refuses_without_raw_egress_before_building_a_model(env: Env) -> None:
    def factory(_: str) -> Any:
        raise AssertionError("model built")

    with pytest.raises(BenchRequestError, match="raw chapter text"):
        env.run(allow_raw_egress=False, model_factory=factory)
    assert not env.db.exists()


def test_cli_refuses_without_raw_egress(env: Env) -> None:
    app = typer.Typer()
    app.command()(cloud_bench.main)
    result = CliRunner().invoke(app, [
        "--db", str(env.db), "--raw-dir", str(env.raw), "--out", str(env.out),
        "--baseline-db", str(env.baseline),
    ])
    assert result.exit_code == 1
    assert "--allow-raw-egress" in result.output
    assert not env.db.exists()


@pytest.mark.parametrize("concurrency", [0, MAX_CONCURRENCY + 1])
def test_concurrency_outside_the_limit_is_refused(concurrency: int) -> None:
    with pytest.raises(BenchRequestError, match="--concurrency"):
        check_request(allow_raw_egress=True, concurrency=concurrency)


def test_pick_chapters_adds_evenly_spaced_non_gold_chapters() -> None:
    gold = {1, 11, 22}
    picked = pick_chapters(gold, 100, 30)
    assert picked == pick_chapters(gold, 100, 30)
    assert picked == sorted(set(picked))
    assert gold <= set(picked) and len(picked) == 33
    extras = sorted(set(picked) - gold)
    assert max(b - a for a, b in zip(extras, extras[1:])) <= 5


def test_pick_chapters_refuses_too_few_chapters() -> None:
    with pytest.raises(ValueError, match="only 1 exist"):
        pick_chapters({1, 2, 3}, 4, 2)


def test_filter_keeps_only_what_s1_would_keep() -> None:
    extraction = ChapterExtraction.model_validate_json(payload("Hàn Lập"))
    assert [e.name for e in filter_events(extraction, TEXT, {"character"})[0]] == ["Hàn Lập"]

    wrong_category = filter_events(extraction, TEXT, {"weapon"})
    assert wrong_category[0] == [] and wrong_category[1] == {INVALID_CATEGORY: 1}

    no_evidence = ChapterExtraction.model_validate_json(payload("Hàn Lập", evidence="zzzzzzzz qqqqqqqq"))
    dropped = filter_events(no_evidence, TEXT, {"character"})
    assert dropped[0] == [] and dropped[1] == {LOW_EVIDENCE: 1}


def test_peak_in_flight_stays_within_the_limit(env: Env) -> None:
    in_flight = peak = 0

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return ModelResponse(parts=[TextPart(payload("Hàn Lập"))])

    env.run(only=[1, 2, 3, 4], concurrency=2, model_factory=lambda _: FunctionModel(respond))
    assert peak == 2


def test_rate_limit_is_retried_with_one_row_per_attempt(env: Env) -> None:
    calls = 0

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelHTTPError(429, "fake")
        return ModelResponse(parts=[TextPart(payload("Hàn Lập"))])

    report = env.run(only=[1], model_factory=lambda _: FunctionModel(respond))
    rows = env.llm_rows()
    assert [r["ok"] for r in rows] == [0, 1]
    assert {r["egress_class"] for r in rows} == {"long_context"}
    assert {r["payload_chapters"] for r in rows} == {"1"}
    assert report["reliability"]["http_429"] == 1
    assert report["reliability"]["attempts"] == 2
    assert report["reliability"]["first_try_success"] == 0
    assert report["reliability"]["schema_success"] == 1


def test_a_failing_chapter_does_not_stop_the_others(env: Env) -> None:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if chapter_of(messages) == 2:
            raise ModelHTTPError(400, "fake")
        return ModelResponse(parts=[TextPart(payload("Hàn Lập"))])

    report = env.run(only=[1, 2, 3], model_factory=lambda _: FunctionModel(respond))
    assert report["reliability"]["chapters_ok"] == 2
    assert list(report["reliability"]["chapters_failed"]) == [2]
    assert len(env.llm_rows()) == 3


def test_resume_skips_saved_chapters_and_keeps_earlier_counts(env: Env) -> None:
    first = env.run(only=[1])
    calls = 0

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart(payload("Hàn Lập"))])

    second = env.run(only=[1, 2], model_factory=lambda _: FunctionModel(respond))
    assert calls == 1
    assert first["reliability"]["attempts"] == 1
    assert second["reliability"]["attempts"] == 2
    assert second["reliability"]["chapters_ok"] == 2


def test_report_has_every_section_and_compares_with_the_baseline(env: Env) -> None:
    report = env.run()
    assert set(report) == {
        "meta", "reliability", "usage", "dropped", "cloud", "local", "overlap", "projection",
    }
    assert report["meta"]["chapters"] == [1, 2, 3]
    assert report["cloud"]["all"]["recall"] == 1
    assert report["local"]["all"]["recall"] == 1
    assert report["cloud"]["dev"] is not None and report["cloud"]["held_out"] is not None
    assert report["overlap"] == {
        "chapters": 1, "both": 1, "cloud_only": 0, "local_only": 1, "jaccard": 0.5,
    }
    assert report["projection"]["chapters"] == 4
    assert json.loads(env.out.read_text(encoding="utf-8")) == report


def test_a_split_with_no_chapters_run_is_null(env: Env) -> None:
    report = env.run(only=[1])
    assert report["cloud"]["dev"] is not None
    assert report["cloud"]["held_out"] is None


def test_baseline_is_opened_read_only(env: Env) -> None:
    conn = cloud_bench._open_read_only(env.baseline)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM extractions")
    finally:
        conn.close()


def test_name_overlap_with_nothing_in_common_chapters() -> None:
    assert name_overlap({}, {}, set())["jaccard"] is None


def test_spend_cap_is_not_consulted(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    def refuse(*args: Any) -> None:
        raise AssertionError("spend cap checked")

    monkeypatch.setattr("xn.llm._check_spend_cap", refuse)
    assert env.run(only=[1])["reliability"]["chapters_ok"] == 1
