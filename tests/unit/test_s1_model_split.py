r"""Unit tests for the S1-only model key, models.s1_extractor (G-27.1)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

from xn import bench
from xn.graph.deps import GraphDeps
from xn.graph.nodes import route as route_module
from xn.llm import extractor_output, extractor_settings, local_model, s1_model_key
from xn.pipeline import s1_extract
from xn.pipeline.s1_extract import run_s1
from xn.schemas.extraction import MicroSummary
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository
from tests.unit.test_s1_extract import make_canned_extraction, make_config, seed_chapter

REPO_ROOT = Path(__file__).resolve().parents[2]
S1_BLOCK = {
    "name": "qwen3.5:9b_UD_Q6",
    "serving_name": "s1-model",
    "base_url": "http://localhost:11434/v1",
    "temperature": 0.3,
    "output_mode": "tool",
    "think": False,
}


def with_s1(config: dict[str, Any]) -> dict[str, Any]:
    cfg = copy.deepcopy(config)
    cfg["models"]["s1_extractor"] = dict(S1_BLOCK)
    return cfg


@pytest.fixture
def repo(tmp_path: Path):
    conn = connect(tmp_path / "split.db")
    init_schema(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


# --- llm helpers -------------------------------------------------------------------------------


def test_s1_model_key_uses_s1_extractor_only_when_set() -> None:
    assert s1_model_key({}) == "local_extractor"
    assert s1_model_key(make_config()) == "local_extractor"
    assert s1_model_key({"models": {"local_extractor": {}, "s1_extractor": None}}) == "local_extractor"
    assert s1_model_key(with_s1(make_config())) == "s1_extractor"


def test_helpers_read_the_key_they_are_given_and_default_to_local() -> None:
    cfg = with_s1(make_config())
    assert local_model(cfg).model_name == "qwen14b-12k"
    assert local_model(cfg, "s1_extractor").model_name == "s1-model"
    cfg["models"]["s1_extractor"]["output_mode"] = "native"
    assert extractor_output(MicroSummary, cfg) is MicroSummary
    assert extractor_output(MicroSummary, cfg, "s1_extractor") is not MicroSummary  # native
    assert extractor_settings(cfg) == {"temperature": 0.1}
    assert extractor_settings(cfg, "s1_extractor") == {"temperature": 0.3, "openai_reasoning_effort": "none"}
    with pytest.raises(ValueError, match="models.s1_extractor.serving_name"):
        local_model({"models": {"s1_extractor": {}}}, "s1_extractor")


# --- S1 ----------------------------------------------------------------------------------------


def run_with_recorded_model(
    repo: Repository, config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], list[Any]]:
    """Run S1 with model=None, recording the key S1 asks local_model for and the settings sent."""
    keys: list[str] = []
    settings: list[Any] = []

    def model_func(messages: list[Any], info: Any) -> ModelResponse:
        settings.append(info.model_settings)
        part = ToolCallPart(info.output_tools[0].name, make_canned_extraction(chapter_id=1))
        return ModelResponse(parts=[part], usage=RequestUsage(input_tokens=10, output_tokens=10))

    def fake_local_model(cfg: dict[str, Any], key: str = "local_extractor") -> FunctionModel:
        keys.append(key)
        return FunctionModel(model_func)

    monkeypatch.setattr(s1_extract, "local_model", fake_local_model)
    seed_chapter(repo)
    run_s1(repo, novel_id="pntt1", config=config)
    return keys, settings


def recorded_models(repo: Repository) -> tuple[str, str, str]:
    conn = repo._conn  # type: ignore[attr-defined]
    run = conn.execute("SELECT model FROM llm_runs WHERE stage = 's1'").fetchone()["model"]
    ext = conn.execute("SELECT model FROM extractions WHERE chapter_id = 1").fetchone()["model"]
    job = repo.get_job(novel_id="pntt1", chapter_id=1, stage="s1")["model"]
    return run, ext, job


def test_run_s1_without_s1_extractor_uses_local_extractor(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_config(total_chapters=1)
    keys, settings = run_with_recorded_model(repo, cfg, monkeypatch)
    assert keys == ["local_extractor"]
    assert settings[0]["temperature"] == 0.1 and "openai_reasoning_effort" not in settings[0]
    assert recorded_models(repo) == ("qwen14b-12k",) * 3


def test_run_s1_with_s1_extractor_calls_and_records_it(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = with_s1(make_config(total_chapters=1))
    keys, settings = run_with_recorded_model(repo, cfg, monkeypatch)
    assert keys == ["s1_extractor"]
    assert settings[0]["temperature"] == 0.3 and settings[0]["openai_reasoning_effort"] == "none"
    assert recorded_models(repo) == ("s1-model",) * 3
    payload = json.loads(repo._conn.execute("SELECT payload FROM extractions").fetchone()["payload"])  # type: ignore[attr-defined]
    assert payload["chapter_id"] == 1


# --- other stages stay on local_extractor ----------------------------------------------------------


def test_router_uses_local_extractor_when_s1_extractor_is_set(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.add_novel(novel_id="nov", title="N", genre_profile="xianxia", total_chapters=10)
    cfg = with_s1(make_config(novel_id="nov"))
    asked: list[tuple[Any, ...]] = []

    def fake_local_model(*args: Any, **kwargs: Any) -> TestModel:
        asked.append(args[1:] + tuple(kwargs.values()))
        return TestModel(custom_output_args={"intent": "factual_qa", "mentions": []})

    monkeypatch.setattr(route_module, "local_model", fake_local_model)
    out = route_module.route(
        {"novel_id": "nov", "query": "Hàn Lập là ai?"},
        GraphDeps(repo=repo, config=cfg, prompts_dir=REPO_ROOT / "prompts"),
    )
    assert out["decision"].intent == "factual_qa"
    assert asked == [()]  # default key: local_extractor
    run = repo._conn.execute("SELECT model FROM llm_runs WHERE stage = 'route'").fetchone()  # type: ignore[attr-defined]
    assert run["model"] == "qwen14b-12k"


def test_only_s1_and_bench_read_the_s1_model_key() -> None:
    """S2, the router, S5, prefix summaries and the gold report must keep local_extractor."""
    readers = {
        path.relative_to(REPO_ROOT / "src").as_posix()
        for path in (REPO_ROOT / "src").rglob("*.py")
        if re.search(r"s1_extractor|s1_model_key|S1_KEY", path.read_text(encoding="utf-8"))
    }
    assert readers == {"xn/llm.py", "xn/pipeline/s1_extract.py", "xn/bench.py"}


# --- bench -------------------------------------------------------------------------------------


def test_bench_candidate_goes_on_both_keys_when_s1_extractor_is_set() -> None:
    candidate = bench.Candidate("cand-10k", "cand:base", think=False)
    plain = bench._candidate_config(make_config(), candidate)
    assert plain["models"]["local_extractor"]["serving_name"] == "cand-10k"
    assert "s1_extractor" not in plain["models"]
    split = bench._candidate_config(with_s1(make_config()), candidate)
    for key in ("local_extractor", "s1_extractor"):
        assert split["models"][key]["serving_name"] == "cand-10k"
        assert split["models"][key]["think"] is False
