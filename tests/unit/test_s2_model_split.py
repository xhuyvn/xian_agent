r"""Unit tests for the S2-only adjudicator model key, models.s2_adjudicator (G-31)."""

from __future__ import annotations

import copy
from pathlib import Path
import re
import sqlite3
from typing import Any

import pytest
from pydantic_ai.models.function import FunctionModel

from xn import gold as gold_module
from xn.llm import s2_model_key
from xn.pipeline import s2_resolve
from xn.pipeline.s2_resolve import run_s2
from tests.unit.test_s2_run import (  # noqa: F401  (fixtures)
    PROMPTS_DIR,
    FakeAdjudicator,
    db_conn,
    make_canned_extraction,
    make_config,
    repo,
    save_chapter_and_extraction,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
S2_BLOCK = {
    "name": "qwen3.5_12k:q8_mtp",
    "serving_name": "s2-model",
    "base_url": "http://localhost:11434/v1",
    "temperature": 0.1,
    "output_mode": "tool",
    "think": False,
}


def with_s2(config: dict[str, Any]) -> dict[str, Any]:
    cfg = copy.deepcopy(config)
    cfg["models"]["s2_adjudicator"] = dict(S2_BLOCK)
    return cfg


def seed_two_chapters(repo: Any) -> None:
    for cid in (1, 2):
        save_chapter_and_extraction(repo, chapter_id=cid, extraction=make_canned_extraction(chapter_id=cid))


def spy_keys(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Record the models key each S2 helper is asked for, and the serving name given to the adjudicator."""
    seen: dict[str, list[Any]] = {"local_model": [], "settings": [], "output": [], "serving_name": []}
    real_settings, real_output, real_make = (
        s2_resolve.extractor_settings, s2_resolve.extractor_output, s2_resolve.make_adjudicator
    )

    def fake_local_model(config: dict[str, Any], key: str = "local_extractor") -> FunctionModel:
        seen["local_model"].append(key)
        return FunctionModel(FakeAdjudicator())

    def settings(config: dict[str, Any], key: str = "local_extractor") -> dict[str, Any]:
        seen["settings"].append(key)
        return real_settings(config, key)

    def output(output_type: Any, config: dict[str, Any], key: str = "local_extractor") -> Any:
        seen["output"].append(key)
        return real_output(output_type, config, key)

    def make(agent: Any, **kwargs: Any) -> Any:
        seen["serving_name"].append(kwargs["serving_name"])
        return real_make(agent, **kwargs)

    monkeypatch.setattr(s2_resolve, "local_model", fake_local_model)
    monkeypatch.setattr(s2_resolve, "extractor_settings", settings)
    monkeypatch.setattr(s2_resolve, "extractor_output", output)
    monkeypatch.setattr(s2_resolve, "make_adjudicator", make)
    return seen


def test_s2_model_key_uses_s2_adjudicator_only_when_set() -> None:
    assert s2_model_key({}) == "local_extractor"
    assert s2_model_key(make_config()) == "local_extractor"
    assert s2_model_key({"models": {"local_extractor": {}, "s2_adjudicator": None}}) == "local_extractor"
    assert s2_model_key(with_s2(make_config())) == "s2_adjudicator"


def test_run_s2_without_s2_adjudicator_uses_local_extractor(
    repo: Any, db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_two_chapters(repo)
    seen = spy_keys(monkeypatch)
    res = run_s2(repo, novel_id="pntt1", config=make_config(total_chapters=2), prompts_dir=PROMPTS_DIR)
    assert res.written == 2
    assert seen == {
        "local_model": ["local_extractor"],
        "settings": ["local_extractor"],
        "output": ["local_extractor"],
        "serving_name": ["qwen14b-12k"],
    }
    models = {r["model"] for r in db_conn.execute("SELECT model FROM jobs WHERE stage = 's2'")}
    assert models == {"qwen14b-12k"}


def test_run_s2_with_s2_adjudicator_calls_and_records_it(
    repo: Any, db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_two_chapters(repo)
    seen = spy_keys(monkeypatch)
    res = run_s2(repo, novel_id="pntt1", config=with_s2(make_config(total_chapters=2)), prompts_dir=PROMPTS_DIR)
    assert res.written == 2
    assert seen == {
        "local_model": ["s2_adjudicator"],
        "settings": ["s2_adjudicator"],
        "output": ["s2_adjudicator"],
        "serving_name": ["s2-model"],
    }
    models = {r["model"] for r in db_conn.execute("SELECT model FROM jobs WHERE stage = 's2'")}
    assert models == {"s2-model"}


def test_s2_adjudicator_think_off_reaches_the_settings() -> None:
    cfg = with_s2(make_config())
    assert s2_resolve.extractor_settings(cfg, s2_model_key(cfg)) == {
        "temperature": 0.1, "openai_reasoning_effort": "none"
    }


def test_switching_only_the_s2_model_does_not_make_s2_stale(repo: Any, db_conn: sqlite3.Connection) -> None:
    """A run stopped mid-S2 can resume on another adjudicator: done chapters stay done, the rest record the new model."""
    for cid in (1, 2, 3):
        save_chapter_and_extraction(repo, chapter_id=cid, extraction=make_canned_extraction(chapter_id=cid))
    cfg = make_config(total_chapters=3)
    run_s2(repo, novel_id="pntt1", config=cfg, chapter_to=2, model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)
    res = run_s2(repo, novel_id="pntt1", config=with_s2(cfg), model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)
    assert (res.skipped, res.written) == (2, 1)
    rows = db_conn.execute("SELECT chapter_id, model FROM jobs WHERE stage = 's2' ORDER BY chapter_id").fetchall()
    assert [tuple(r) for r in rows] == [(1, "qwen14b-12k"), (2, "qwen14b-12k"), (3, "s2-model")]


def test_gold_report_gives_the_adjudicator_the_s2_model_and_the_router_the_local_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: dict[str, Any] = {}

    def fake_local_model(config: dict[str, Any], key: str = "local_extractor") -> str:
        return f"model:{key}"

    class Stop(Exception):
        pass

    def fake_build_adjudicator(repo: Any, **kwargs: Any) -> Any:
        asked["adjudicator"] = kwargs["model"]
        raise Stop

    class FakeRepo:
        def list_chapters(self, **kwargs: Any) -> list[Any]:
            return []

        def list_extractions(self, **kwargs: Any) -> list[Any]:
            return []

    class FakeGold:
        meta = {"chapter_max": 10}

    monkeypatch.setattr(gold_module, "local_model", fake_local_model)
    monkeypatch.setattr(gold_module, "build_adjudicator", fake_build_adjudicator)
    monkeypatch.setattr(gold_module, "load_adjudicate_prompt", lambda config, prompts_dir: None)
    with pytest.raises(Stop):
        gold_module.gold_report(FakeRepo(), gold=FakeGold(), config=with_s2(make_config()))  # type: ignore[arg-type]
    assert asked["adjudicator"] == "model:s2_adjudicator"
    with pytest.raises(Stop):
        gold_module.gold_report(FakeRepo(), gold=FakeGold(), config=make_config())  # type: ignore[arg-type]
    assert asked["adjudicator"] == "model:local_extractor"


def test_only_s2_gold_and_llm_read_the_s2_model_key() -> None:
    """The router, S5, prefix summaries and S1 must not pick up the S2 adjudicator."""
    readers = {
        path.relative_to(REPO_ROOT / "src").as_posix()
        for path in (REPO_ROOT / "src").rglob("*.py")
        if re.search(r"s2_adjudicator|s2_model_key|S2_KEY", path.read_text(encoding="utf-8"))
    }
    assert readers == {"xn/llm.py", "xn/pipeline/s2_resolve.py", "xn/gold.py"}
