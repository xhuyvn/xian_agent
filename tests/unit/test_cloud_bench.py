"""Unit tests for the cloud benchmark, using fake models and made-up chapters; no network."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import RequestUsage
import pytest
from typer.testing import CliRunner
import typer

from xn import cloud_bench
from xn.cloud_bench import (
    MAX_CONCURRENCY,
    MISATTRIBUTED,
    PAIR_RULES,
    BenchRequestError,
    check_pairs,
    check_request,
    deepseek_cost,
    filter_events,
    is_deepseek_peak,
    name_overlap,
    pair_chapters,
    pick_chapters,
    run_bench,
)
from xn.gold import GoldChapter, GoldEntity, GoldSet
from xn.config import CloudSettings
from xn.gold_run import INVALID_CATEGORY, LOW_EVIDENCE
from xn.llm import UpstreamError
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


def test_bench_model_turns_on_json_schema_output(monkeypatch: pytest.MonkeyPatch) -> None:
    base = OpenRouterModel("deepseek/fake", provider=OpenRouterProvider(api_key="fake"))
    assert not base.profile.get("supports_json_schema_output", False)
    monkeypatch.setattr(cloud_bench, "cloud_model", lambda _: base)
    assert cloud_bench.bench_model("deepseek/fake").profile["supports_json_schema_output"]


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

    spaced = ChapterExtraction.model_validate_json(payload("Hàn Lập", evidence=f"  {TEXT[:20]} \n"))
    assert len(filter_events(spaced, TEXT, {"character"})[0]) == 1

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


def test_cli_exits_nonzero_after_writing_the_report_when_a_chapter_fails(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(400, "fake")

    original = cloud_bench.run_bench

    def fake_run_bench(**kwargs: Any) -> dict[str, Any]:
        kwargs.update(model_factory=lambda _: FunctionModel(respond), prompts_dir=REPO_ROOT / "prompts")
        return original(**kwargs)

    monkeypatch.setattr(cloud_bench, "load_config", lambda *a, **k: CONFIG)
    monkeypatch.setattr(cloud_bench, "load_gold", lambda _: gold_set(1, 2))
    monkeypatch.setattr(cloud_bench, "run_bench", fake_run_bench)
    app = typer.Typer()
    app.command()(cloud_bench.main)
    result = CliRunner().invoke(app, [
        "--db", str(env.db), "--raw-dir", str(env.raw), "--out", str(env.out),
        "--baseline-db", str(env.baseline), "--only", "1", "--allow-raw-egress",
    ])
    assert result.exit_code == 1
    assert json.loads(env.out.read_text(encoding="utf-8"))["reliability"]["chapters_failed"] == {
        "1": "ModelHTTPError 400"
    }


def test_failure_text_holds_no_error_message(env: Env) -> None:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(400, "fake", body=TEXT)

    env.run(only=[1], model_factory=lambda _: FunctionModel(respond))
    assert TEXT not in env.out.read_text(encoding="utf-8")


def test_waiting_for_a_backoff_holds_no_slot(env: Env) -> None:
    calls = 0
    other_chapter_ran = False

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelHTTPError(500, "fake")
        return ModelResponse(parts=[TextPart(payload("Hàn Lập"))])

    async def backoff(_: float) -> None:
        nonlocal other_chapter_ran
        before = calls
        await asyncio.sleep(0.05)
        other_chapter_ran = other_chapter_ran or calls > before

    env.run(only=[1, 2], concurrency=1, sleep=backoff, model_factory=lambda _: FunctionModel(respond))
    assert other_chapter_ran


def test_a_rerun_with_nothing_to_do_keeps_counts_timing_and_projection(env: Env) -> None:
    calls = 0

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ModelHTTPError(429, "fake")
        return ModelResponse(parts=[TextPart(payload("Hàn Lập"))])

    first = env.run(only=[1], model_factory=lambda _: FunctionModel(respond))
    second = env.run(only=[1])
    assert second["reliability"]["http_429"] == 1
    assert second["dropped"] == first["dropped"]
    assert second["meta"]["wall_seconds"] == first["meta"]["wall_seconds"]
    assert second["projection"] == first["projection"]
    assert second["projection"]["hours"] is not None


def test_a_partial_resume_times_every_chapter(env: Env) -> None:
    first = env.run(only=[1])
    second = env.run(only=[1, 2])
    assert second["meta"]["wall_seconds"] > first["meta"]["wall_seconds"]
    assert second["usage"]["wall_seconds_per_chapter"] == pytest.approx(second["meta"]["wall_seconds"] / 2)


def test_a_new_database_ignores_the_old_report(env: Env) -> None:
    async def low_evidence(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(payload("Hàn Lập", evidence="zzzzzzzz qqqqqqqq"))])

    env.run(only=[1], model_factory=lambda _: FunctionModel(low_evidence))
    for path in env.db.parent.glob("bench.db*"):
        path.unlink()
    report = env.run(only=[1], model_factory=lambda _: FunctionModel(low_evidence))
    assert report["dropped"]["by_reason"] == {LOW_EVIDENCE: 1}


def test_a_failed_chapter_does_not_score_another_models_extraction(env: Env) -> None:
    async def fails(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(400, "fake")

    env.run(only=[1], model_name="first-model")
    report = env.run(only=[1], model_name="second-model", model_factory=lambda _: FunctionModel(fails))
    assert report["reliability"]["chapters_ok"] == 0
    assert report["cloud"]["all"] is None


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


def test_run_bench_opens_the_baseline_through_the_read_only_helper(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = []
    original = cloud_bench._open_read_only

    def spy(path: Path) -> sqlite3.Connection:
        opened.append(path)
        return original(path)

    monkeypatch.setattr(cloud_bench, "_open_read_only", spy)
    env.run(only=[1])
    assert opened == [env.baseline]


def pair_payload(first: str, second: str, **kwargs: Any) -> str:
    return json.dumps({
        "first_chapter": json.loads(payload(first, **kwargs)),
        "second_chapter": json.loads(payload(second, **kwargs)),
    })


def test_pair_chapters_keeps_each_gold_chapter_with_its_fixed_partner() -> None:
    assert pair_chapters({1, 4, 7, 8, 100}, 100) == [1, 2, 3, 4, 7, 8, 99, 100]
    assert pair_chapters({5}, 5) == [5]


def test_check_pairs_names_a_lone_chapter() -> None:
    check_pairs([1, 2, 3, 4])
    with pytest.raises(BenchRequestError, match=r"\[2, 3\]"):
        check_pairs([2, 3])


def test_pairs_refuses_a_lone_chapter_before_building_anything(env: Env) -> None:
    def factory(_: str) -> Any:
        raise AssertionError("model built")

    with pytest.raises(BenchRequestError, match="whole pairs"):
        env.run(only=[1, 2, 3], pairs=True, model_factory=factory)
    assert not env.db.exists()


def test_an_unknown_provider_is_refused() -> None:
    with pytest.raises(BenchRequestError, match="--provider"):
        check_request(allow_raw_egress=True, concurrency=1, provider="nope")


def test_a_pair_is_one_call_with_one_result_per_chapter(env: Env) -> None:
    seen: list[ModelMessage] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(messages)
        return ModelResponse(parts=[TextPart(pair_payload("Hàn Lập", "Trương Thiết"))])

    report = env.run(only=[1, 2], pairs=True, model_factory=lambda _: FunctionModel(respond))
    rows = env.llm_rows()
    assert len(rows) == 1
    assert rows[0]["payload_chapters"] == "1,2"
    assert rows[0]["prompt_version"].endswith("+pair")
    sent = str(seen[-1].parts[-1].content)
    assert sent.count("<chapter>") == 2 and sent.index("#1#") < sent.index("#2#")
    assert PAIR_RULES.strip() in str(seen[0].parts[0].content)
    assert report["reliability"]["chapters_ok"] == 2
    assert report["reliability"]["first_try_success"] == 1
    assert report["meta"]["pairs"] is True

    conn = connect(env.db)
    try:
        saved = Repository(conn).list_extractions(novel_id="pntt1", chapter_max=4)
    finally:
        conn.close()
    assert {r["chapter_id"]: json.loads(r["payload"])["events"][0]["name"] for r in saved} == {
        1: "Hàn Lập", 2: "Trương Thiết",
    }
    assert {r["chapter_id"]: json.loads(r["payload"])["chapter_id"] for r in saved} == {1: 1, 2: 2}


def test_a_quote_from_the_partner_chapter_is_dropped_as_misattributed() -> None:
    extraction = ChapterExtraction.model_validate_json(payload("Hàn Lập"))
    kept, dropped = filter_events(extraction, "zzzzzzzz " * 20, {"character"}, TEXT)
    assert kept == [] and dropped == {MISATTRIBUTED: 1}

    kept, dropped = filter_events(extraction, "zzzzzzzz " * 20, {"character"}, "qqqqqqqq " * 20)
    assert kept == [] and dropped == {LOW_EVIDENCE: 1}


def test_a_pair_marks_events_quoted_from_the_wrong_chapter(env: Env) -> None:
    only_second = "Mặc đại phu nhìn lò đan cháy đỏ trong gian phòng tối."
    (env.raw / "chapter_0002.md").write_text(f"# Chương 2: Tên\n\n{only_second}\n" * 30, encoding="utf-8")

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(pair_payload("Hàn Lập", "Lý", evidence=only_second))])

    report = env.run(only=[1, 2], pairs=True, model_factory=lambda _: FunctionModel(respond))
    assert report["dropped"]["by_reason"] == {MISATTRIBUTED: 1}
    assert report["dropped"]["events_kept"] == 1


def test_a_failed_pair_fails_both_chapters_and_a_half_saved_pair_runs_again(env: Env) -> None:
    async def fails(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(400, "fake")

    report = env.run(only=[1, 2], pairs=True, model_factory=lambda _: FunctionModel(fails))
    assert sorted(report["reliability"]["chapters_failed"]) == [1, 2]

    calls = 0

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[TextPart(pair_payload("Hàn Lập", "Hàn Lập"))])

    env.run(only=[1, 2], pairs=True, model_factory=lambda _: FunctionModel(respond))
    conn = connect(env.db)
    try:
        with conn:
            conn.execute("DELETE FROM jobs WHERE chapter_id = 2")
    finally:
        conn.close()
    env.run(only=[1, 2], pairs=True, model_factory=lambda _: FunctionModel(respond))
    assert calls == 2


def test_gold_pairs_runs_the_same_chapters_in_both_modes(env: Env) -> None:
    single = env.run(gold_pairs=True, gold=gold_set(1, 4))
    assert single["meta"]["chapters"] == [1, 2, 3, 4]

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(pair_payload("Hàn Lập", "Hàn Lập"))])

    paired = env.run(
        gold_pairs=True, gold=gold_set(1, 4), pairs=True, model_factory=lambda _: FunctionModel(respond)
    )
    assert paired["meta"]["chapters"] == single["meta"]["chapters"]


@pytest.mark.parametrize(
    ("at", "peak"),
    [
        (datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc), True),
        (datetime(2026, 11, 2, 3, 59, tzinfo=timezone.utc), True),
        (datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc), False),
        (datetime(2026, 11, 2, 9, 59, tzinfo=timezone.utc), True),
        (datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc), False),
        (datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc), False),
    ],
)
def test_deepseek_peak_hours(at: datetime, peak: bool) -> None:
    assert is_deepseek_peak(at) is peak


def test_deepseek_cost_splits_cached_input_and_doubles_at_peak() -> None:
    usage = RequestUsage(
        input_tokens=1_000_000, output_tokens=1_000_000, details={"prompt_cache_hit_tokens": 400_000}
    )
    messages: list[ModelMessage] = [ModelResponse(parts=[TextPart("x")], usage=usage)]
    off_peak = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    peak = datetime(2026, 11, 2, 2, 0, tzinfo=timezone.utc)
    assert deepseek_cost(messages, off_peak) == (pytest.approx(0.6912), 400_000, 600_000)
    assert deepseek_cost(messages, peak)[0] == pytest.approx(1.3824)


def test_deepseek_run_uses_tool_output_turns_thinking_off_and_counts_the_cache(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings: list[Any] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        settings.append(info.model_settings)
        usage = RequestUsage(input_tokens=100, output_tokens=10, details={"prompt_cache_hit_tokens": 75})
        name = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(name, payload("Hàn Lập"))], usage=usage)

    report = env.run(
        only=[1], provider_name="deepseek", model_factory=lambda _: FunctionModel(respond)
    )
    assert report["meta"]["provider"] == "deepseek"
    assert report["meta"]["model"] == "deepseek-flash"
    assert settings[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "openrouter_usage" not in settings[0]
    assert report["usage"]["cache_hit_rate"] == 0.75
    assert report["usage"]["cost_usd"] > 0

    rerun = env.run(only=[1], provider_name="deepseek")
    assert rerun["usage"]["cache_hit_tokens"] == 75


def test_deepseek_model_needs_its_key_and_uses_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cloud_bench, "CloudSettings", lambda: CloudSettings(_env_file=None, deepseek_api_key="")
    )
    with pytest.raises(UpstreamError, match="DEEPSEEK_API_KEY"):
        cloud_bench.deepseek_model("deepseek-flash")

    monkeypatch.setattr(
        cloud_bench, "CloudSettings",
        lambda: CloudSettings(_env_file=None, deepseek_api_key="fake", deepseek_base_url="https://x.test/v1"),
    )
    model = cloud_bench.deepseek_model("deepseek-flash")
    assert model.model_name == "deepseek-flash"
    assert str(model.client.base_url).startswith("https://x.test/v1")
    assert model.client.max_retries == 0


def test_spend_cap_of_zero_does_not_stop_the_run(env: Env) -> None:
    config = {**CONFIG, "daily_spend_cap_usd": 0.0}
    assert env.run(only=[1], config=config)["reliability"]["chapters_ok"] == 1
