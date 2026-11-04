"""Unit tests for the S1-T7 extractor benchmark, using fake models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from pydantic_ai import NativeOutput
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.usage import RequestUsage

from xn.bench import (
    CANDIDATES,
    Candidate,
    ModelResult,
    choose_winner,
    pick_chapters,
    pick_gold_chapters,
    render_gold,
    run_parallel,
    run_worker,
    pooled_recall,
    render_report,
    run_benchmark,
    select_candidates,
)
from xn.gold import GoldChapter, GoldEntity, GoldSet
from xn.llm import extractor_output, extractor_settings
from xn.schemas.extraction import MicroSummary

SUMMARY = "Hàn Lập rời thôn nhỏ, lên đường đến Thất Huyền Môn và bắt đầu tu luyện. " * 4


def make_config() -> dict[str, Any]:
    """Build a minimal novel config."""
    return {
        "novel_id": "pntt1",
        "title": "Phàm Nhân Tu Tiên",
        "author": "Vong Ngữ",
        "genre": "xianxia",
        "total_chapters": 3,
        "models": {"local_extractor": {"name": "base", "serving_name": "base-12k", "temperature": 0.1}},
        "categories": ["nhân vật"],
        "event_types": {"MENTIONED": "Nhắc tới"},
        "extraction_hints": ["Trích xuất chính xác."],
        "realm_ladder": [],
        "cleaning_patterns": [],
        "prompts": {},
    }


def write_chapters(raw_dir: Path) -> None:
    """Write 3 chapters, each naming Hàn Lập and Trương Thiết."""
    raw_dir.mkdir()
    for n in range(1, 4):
        body = f"# Chương {n}: Thử\n\nHàn Lập gặp Trương Thiết tại chân núi lần thứ {n} trong đời.\n"
        (raw_dir / f"chapter_{n:04d}.md").write_text(body * (n + 1), encoding="utf-8")


def fake_model(names: list[str], *, fail: bool = False) -> FunctionModel:
    """Build a native-output fake that reports names, or always returns bad JSON."""

    def respond(messages: list[Any], info: Any) -> ModelResponse:
        if fail:
            content = "not json"
        else:
            content = json.dumps({
                "chapter_id": 1, "map_location": "Thôn", "location_change": False, "mc_realm": None,
                "realm_breakthrough": False, "major_conflict": False, "time_skip_years": 0.0,
                "micro_summary": SUMMARY[:300],
                "events": [
                    {"name": name, "category": "nhân vật", "event_type": "MENTIONED",
                     "description": "Xuất hiện.", "evidence": "Hàn Lập gặp Trương Thiết tại chân núi"}
                    for name in names
                ],
            })
        tokens = sum(len(part.content) for msg in messages for part in msg.parts if hasattr(part, "content"))
        return ModelResponse(parts=[TextPart(content)], usage=RequestUsage(input_tokens=tokens // 3, output_tokens=10))

    return FunctionModel(respond, profile=ModelProfile(supports_json_schema_output=True))


def result(name: str, *, schema: float = 1.0, recall: float = 0.8, speed: float = 20.0) -> ModelResult:
    """Build a ModelResult with only the fields the rule reads changed."""
    return ModelResult(
        serving_name=name, chapters=50, schema_success=schema, first_try_rate=schema, recall=recall,
        names_in_text=1, names_not_in_text=0, s_per_chapter=speed, p95_s_per_chapter=speed,
        baseline_vram_mib=2000, peak_vram_mib=12000, chars_per_token=3.0,
    )


def test_pick_chapters_is_even_and_includes_both_ends() -> None:
    picked = pick_chapters(1121, 50)
    assert len(set(picked)) == 50
    assert picked[0] == 1 and picked[-1] == 1121
    gaps = {b - a for a, b in zip(picked, picked[1:])}
    assert max(gaps) - min(gaps) <= 1


@pytest.mark.parametrize("count", [1, 12])
def test_pick_chapters_rejects_bad_count(count: int) -> None:
    with pytest.raises(ValueError):
        pick_chapters(11, count)


def test_pooled_recall_scores_against_the_union() -> None:
    recall = pooled_recall({
        "a": {1: {"x", "y"}, 2: {"z"}},
        "b": {1: {"x"}, 2: set()},
    })
    assert recall == {"a": 1.0, "b": pytest.approx(1 / 3)}


def test_pooled_recall_keeps_same_name_in_two_chapters_apart() -> None:
    recall = pooled_recall({"a": {1: {"x"}, 2: set()}, "b": {1: set(), 2: {"x"}}})
    assert recall == {"a": 0.5, "b": 0.5}


def test_pooled_recall_with_empty_pool_is_zero() -> None:
    assert pooled_recall({"a": {1: set()}}) == {"a": 0.0}


def test_winner_needs_the_schema_gate() -> None:
    winner, _ = choose_winner([
        result("a", schema=0.94, recall=0.95),
        result("b", schema=0.95, recall=0.70),
    ])
    assert winner == "b"


def test_no_winner_when_nobody_passes_the_gate() -> None:
    winner, reason = choose_winner([result("a", schema=0.5), result("b", schema=0.94)])
    assert winner is None
    assert "95%" in reason


def test_higher_recall_wins_outside_the_margin() -> None:
    winner, _ = choose_winner([
        result("slow", recall=0.85, speed=60.0),
        result("fast", recall=0.82, speed=10.0),
    ])
    assert winner == "slow"


def test_faster_model_wins_within_two_points() -> None:
    winner, reason = choose_winner([
        result("slow", recall=0.85, speed=60.0),
        result("fast", recall=0.83, speed=10.0),
    ])
    assert winner == "fast"
    assert "faster" in reason


def test_extractor_output_modes() -> None:
    assert extractor_output(MicroSummary, {}) is MicroSummary
    native = extractor_output(MicroSummary, {"models": {"local_extractor": {"output_mode": "native"}}})
    assert isinstance(native, NativeOutput)
    with pytest.raises(ValueError):
        extractor_output(MicroSummary, {"models": {"local_extractor": {"output_mode": "other"}}})


def test_extractor_settings_only_sends_think_when_false() -> None:
    assert extractor_settings({}) == {"temperature": 0.1}
    assert extractor_settings({"models": {"local_extractor": {"think": True}}}) == {"temperature": 0.1}
    off = extractor_settings({"models": {"local_extractor": {"think": False, "temperature": 0.2}}})
    assert off == {"temperature": 0.2, "openai_reasoning_effort": "none"}


def run_fake(
    tmp_path: Path, models: dict[str, Any], *, vram: list[int] | None = None, **extra: Any
) -> tuple[Any, ...]:
    raw_dir = tmp_path / "raw"
    if not raw_dir.exists():
        write_chapters(raw_dir)
    readings = iter(vram or [])
    unloaded: list[str] = []
    candidates = tuple(Candidate(name, name) for name in models)
    out = run_benchmark(
        config=make_config(), raw_dir=raw_dir, out_dir=tmp_path / "out", chapter_max=3, count=3,
        candidates=candidates, models=models, prompts_dir=Path("prompts"),
        read_vram=lambda: next(readings, 9000), unload=unloaded.append, **extra,
    )
    return (*out, unloaded)


def test_run_benchmark_compares_models_on_the_same_chapters(tmp_path: Path) -> None:
    results, winner, _, unloaded = run_fake(tmp_path, {
        "wide": fake_model(["Hàn Lập", "Trương Thiết"]),
        "narrow": fake_model(["Hàn Lập"]),
    }, vram=[2000])

    by_name = {r.serving_name: r for r in results}
    assert by_name["wide"].recall == 1.0
    assert by_name["narrow"].recall == 0.5
    assert by_name["wide"].schema_success == 1.0
    assert by_name["wide"].first_try_rate == 1.0
    assert by_name["wide"].names_in_text == 6
    assert by_name["wide"].baseline_vram_mib == 2000
    assert by_name["wide"].peak_vram_mib == 9000
    assert by_name["wide"].chars_per_token == pytest.approx(3.0, rel=0.05)
    assert winner == "wide"
    assert unloaded == ["wide", "narrow", "wide", "wide", "narrow", "narrow"]

    saved = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert saved["chapters"] == [1, 2, 3]
    assert saved["winner"] == "wide"
    assert (tmp_path / "out" / "wide.db").exists() and (tmp_path / "out" / "narrow.db").exists()


def test_invented_names_stay_out_of_the_pool(tmp_path: Path) -> None:
    results, _, _, _ = run_fake(tmp_path, {
        "honest": fake_model(["Hàn Lập"]),
        "inventive": fake_model(["Hàn Lập", "Mặc Đại Phu"]),
    })
    by_name = {r.serving_name: r for r in results}
    assert by_name["honest"].recall == 1.0
    assert by_name["inventive"].recall == 1.0
    assert by_name["inventive"].names_not_in_text == 3


def test_failed_chapters_lower_schema_success_and_block_the_win(tmp_path: Path) -> None:
    results, winner, _, _ = run_fake(tmp_path, {"bad": fake_model([], fail=True)})
    assert results[0].schema_success == 0.0
    assert results[0].first_try_rate == 0.0
    assert winner is None


def test_rerun_makes_no_new_calls(tmp_path: Path) -> None:
    calls = {"n": 0}
    inner = fake_model(["Hàn Lập"])

    def counting(messages: list[Any], info: Any) -> ModelResponse:
        calls["n"] += 1
        return inner.function(messages, info)  # type: ignore[misc]

    model = FunctionModel(counting, profile=ModelProfile(supports_json_schema_output=True))
    first, _, _, _ = run_fake(tmp_path, {"m": model})
    made = calls["n"]
    second, _, _, _ = run_fake(tmp_path, {"m": model})
    assert calls["n"] == made
    assert first[0].recall == second[0].recall
    assert first[0].schema_success == second[0].schema_success
    assert first[0].peak_vram_mib == 9000
    assert second[0].peak_vram_mib is None
    assert second[0].baseline_vram_mib is None


def test_select_candidates_by_serving_name() -> None:
    assert select_candidates(None) == CANDIDATES
    assert select_candidates([]) == CANDIDATES
    picked = select_candidates(["qwen38-ud-q3-10k", "deepseek-r1-14b-12k"])
    assert [c.base_name for c in picked] == ["qwen3.8:UD_Q3", "deepseek-r1:14b"]
    assert picked[0].think is False


def test_select_candidates_rejects_unknown_names() -> None:
    with pytest.raises(ValueError, match="qwen3.8:IQ3"):
        select_candidates(["qwen3.8:IQ3"])


def test_report_names_each_model_and_the_winner() -> None:
    text = render_report([result("a"), result("b")], [1, 2], "a", "Reason.")
    assert "| Metric | a | b |" in text
    assert "Winner: a. Reason." in text


def make_gold(chapter_ids: list[int]) -> GoldSet:
    """Gold that names Hàn Lập and Trương Thiết in each chapter, with no flags set."""
    chapters = [
        GoldChapter(
            chapter_id=n, location_change=False, realm_breakthrough=False, major_conflict=False,
            entities=[
                GoldEntity(name="Hàn Lập", category="nhân vật", event_types=["MENTIONED"]),
                GoldEntity(name="Trương Thiết", category="địa điểm", event_types=["MENTIONED"]),
            ],
        )
        for n in chapter_ids
    ]
    return GoldSet(meta={}, chapters=chapters, aliases=[], qa=[], spoilers=[])


def test_qwen25_candidate_runs_at_its_10k_serving_name() -> None:
    (picked,) = select_candidates(["qwen25-14b-10k"])
    assert picked.base_name == "qwen2.5:14b-instruct"
    assert picked.think is None and picked.output_mode == "native"
    modelfile = Path("ollama/Modelfile.qwen25-14b-10k").read_text(encoding="utf-8")
    assert "FROM qwen2.5:14b-instruct" in modelfile
    assert "PARAMETER num_ctx 10240" in modelfile


def test_pick_gold_chapters_matches_the_g13_set() -> None:
    gold_ids = {1, 6, 11, 17, 22, 27, 32, 37, 43, 48, 53, 58, 63, 69, 74, 79, 84, 89, 94, 100}
    picked = pick_gold_chapters(gold_ids, 100, 30)
    assert len(picked) == 50 and gold_ids <= set(picked)
    assert picked == sorted(picked)
    with pytest.raises(ValueError):
        pick_gold_chapters({1, 2}, 3, 2)


def test_run_benchmark_scores_the_gold_chapters(tmp_path: Path) -> None:
    results, _, _, _ = run_fake(
        tmp_path,
        {"wide": fake_model(["Hàn Lập", "Trương Thiết"]), "extra": fake_model(["Hàn Lập", "Mặc Đại Phu"])},
        chapters=[1, 3], gold=make_gold([1, 3]),
    )
    assert results[0].chapters == 2
    saved = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert saved["chapters"] == [1, 3]
    wide, extra = saved["gold"]["wide"], saved["gold"]["extra"]
    assert wide["all"]["recall"] == 1.0 and wide["all"]["precision"] == 1.0
    assert wide["all"]["category_accuracy"] == 0.5
    assert wide["dev"]["gold_entities"] == 2 and wide["held_out"]["gold_entities"] == 2
    assert extra["all"]["recall"] == 0.5
    assert extra["all"]["false_names"] == 2
    assert extra["all"]["flag_f1"] == {"location_change": 0.0, "realm_breakthrough": 0.0, "major_conflict": 0.0}
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "| Entity recall | 1.00 (4 of 4) | 0.50 (2 of 4) |" in report
    assert "| Entity precision | 1.00 (0 false names) | 0.50 (2 false names) |" in report


def test_run_benchmark_rejects_gold_chapters_outside_the_run(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\[2\]"):
        run_fake(tmp_path, {"m": fake_model(["Hàn Lập"])}, chapters=[1, 3], gold=make_gold([1, 2]))


def test_render_gold_marks_missing_splits() -> None:
    text = render_gold({"m": {"all": None, "dev": None, "held_out": None}})
    assert "| Entity recall | n/a |" in text
    assert "| Flag F1, `major_conflict` | n/a |" in text


def parallel_fake(
    tmp_path: Path, model: Any, *, workers: int = 2, chapters: list[int] | None = None
) -> tuple[Any, ...]:
    raw_dir = tmp_path / "raw"
    if not raw_dir.exists():
        write_chapters(raw_dir)
    jobs_seen: list[Any] = []

    def spawn(jobs: list[Any]) -> None:
        jobs_seen.extend(jobs)
        for job in jobs:
            run_worker(*job, model=model)

    result, scores = run_parallel(
        config=make_config(), raw_dir=raw_dir, out_dir=tmp_path / "out", chapter_max=3,
        candidate=Candidate("m", "m"), chapters=chapters or [1, 2, 3], workers=workers,
        gold=make_gold([1, 3]), prompts_dir=Path("prompts"),
        read_vram=lambda: 5000, unload=lambda name: None, spawn=spawn,
    )
    return result, scores, jobs_seen


def test_run_parallel_deals_chapters_round_robin_and_scores_all_workers(tmp_path: Path) -> None:
    result, scores, jobs = parallel_fake(tmp_path, fake_model(["Hàn Lập", "Trương Thiết"]))
    assert [job[2] for job in jobs] == [[1, 3], [2]]
    assert len({job[0] for job in jobs}) == 2
    assert result.workers == 2 and result.chapters == 3
    assert result.schema_success == 1.0
    assert result.wall_s_per_chapter == pytest.approx(result.wall_seconds / 3)
    assert result.peak_vram_mib == 5000
    assert scores is not None and scores["all"]["recall"] == 1.0 and scores["all"]["gold_entities"] == 4
    saved = json.loads((tmp_path / "out" / "parallel-m-w2.json").read_text(encoding="utf-8"))
    assert saved["shares"] == [[1, 3], [2]]
    assert "| Workers | 2 |" in (tmp_path / "out" / "parallel-m-w2.md").read_text(encoding="utf-8")


def test_run_parallel_counts_failed_chapters(tmp_path: Path) -> None:
    result, _, _ = parallel_fake(tmp_path, fake_model([], fail=True), chapters=[1, 3])
    assert result.schema_success == 0.0


def test_run_parallel_refuses_a_resumed_run(tmp_path: Path) -> None:
    parallel_fake(tmp_path, fake_model(["Hàn Lập"]))
    with pytest.raises(ValueError, match="fresh"):
        parallel_fake(tmp_path, fake_model(["Hàn Lập"]))


def test_run_parallel_needs_two_workers(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 2"):
        parallel_fake(tmp_path, fake_model(["Hàn Lập"]), workers=1)
