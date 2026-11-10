r"""Unit tests for free-quota model pools with failover (G-32)."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from xn import model_pool
from xn.llm import run_logged
from xn.model_pool import (
    PoolExhaustedError,
    PoolMember,
    PoolModel,
    PoolState,
    answered_by,
    build_pool,
    stage_model,
    stage_pool_name,
)
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

QUOTA_BODY = {"code": "AllocationQuota.FreeTierOnly", "message": "The free tier of the model has been exhausted."}


@pytest.fixture
def repo(tmp_path: Path):
    conn = connect(tmp_path / "pool.db")
    init_schema(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


class Fake:
    """A FunctionModel body: answers, or raises the given error; records calls and settings."""

    def __init__(self, answer: str = "ok", error: Exception | None = None) -> None:
        self.answer, self.error = answer, error
        self.calls = 0
        self.settings: list[Any] = []

    def __call__(self, messages: list[Any], info: Any) -> ModelResponse:
        self.calls += 1
        self.settings.append(info.model_settings)
        if self.error is not None:
            raise self.error
        return ModelResponse(parts=[TextPart(self.answer)])


def quota_error(name: str) -> ModelHTTPError:
    return ModelHTTPError(status_code=403, model_name=name, body=QUOTA_BODY)


def make_pool(fakes: dict[str, Fake], repo: Repository | None = None, extra_body: dict[str, Any] | None = None) -> PoolModel:
    state = PoolState(name="qwen_free", provider="dashscope", patterns=model_pool.DEFAULT_EXHAUSTED_PATTERNS,
                      repo=repo, novel_id="pntt1" if repo is not None else None)
    if repo is not None:
        state.exhausted |= repo.exhausted_models(novel_id="pntt1", provider="dashscope")
    members = [PoolMember(FunctionModel(f, model_name=n), n, state, extra_body) for n, f in fakes.items()]
    return PoolModel(members, state)


def test_first_member_answers_when_it_has_quota() -> None:
    a, b = Fake("from a"), Fake("from b")
    pool = make_pool({"a": a, "b": b})
    assert Agent(pool).run_sync("hi").output == "from a"
    assert (a.calls, b.calls, pool.last_used) == (1, 0, "a")


def test_a_quota_error_marks_the_member_and_the_same_call_moves_on(repo: Repository) -> None:
    a, b = Fake(error=quota_error("a")), Fake("from b")
    pool = make_pool({"a": a, "b": b}, repo)
    agent = Agent(pool)
    assert agent.run_sync("hi").output == "from b"
    assert agent.run_sync("again").output == "from b"
    assert a.calls == 1  # never asked again once exhausted
    assert repo.exhausted_models(novel_id="other", provider="dashscope") == {"a"}  # account-wide
    row = repo._conn.execute("SELECT novel_id, error FROM model_quota").fetchone()  # type: ignore[attr-defined]
    assert row["novel_id"] == "pntt1" and "FreeTierOnly" in row["error"]


def test_models_exhausted_in_an_earlier_run_are_skipped(repo: Repository) -> None:
    repo.mark_model_exhausted(novel_id="pntt1", provider="dashscope", model="a", error="used up")
    a, b = Fake("from a"), Fake("from b")
    assert Agent(make_pool({"a": a, "b": b}, repo)).run_sync("hi").output == "from b"
    assert a.calls == 0


def test_other_errors_do_not_fail_over() -> None:
    a, b = Fake(error=ModelHTTPError(status_code=500, model_name="a", body={"code": "InternalError"})), Fake()
    pool = make_pool({"a": a, "b": b})
    with pytest.raises(ModelHTTPError):
        Agent(pool).run_sync("hi")
    assert b.calls == 0 and pool.state.exhausted == set()


def test_rate_limits_are_not_taken_for_used_up_quota() -> None:
    a = Fake(error=ModelHTTPError(status_code=429, model_name="a", body={"code": "Throttling.RateQuota"}))
    pool = make_pool({"a": a, "b": Fake()})
    with pytest.raises(ModelHTTPError):
        Agent(pool).run_sync("hi")
    assert pool.state.exhausted == set()


def test_every_member_used_up_raises_pool_exhausted(repo: Repository) -> None:
    pool = make_pool({"a": Fake(error=quota_error("a")), "b": Fake(error=quota_error("b"))}, repo)
    with pytest.raises(PoolExhaustedError, match="qwen_free.*a, b"):
        Agent(pool).run_sync("hi")
    assert repo.exhausted_models(novel_id="pntt1", provider="dashscope") == {"a", "b"}


def test_members_drop_local_and_openrouter_settings_and_add_extra_body() -> None:
    a = Fake()
    pool = make_pool({"a": a}, extra_body={"enable_thinking": False})
    settings = {"temperature": 0.2, "openai_reasoning_effort": "none", "openrouter_usage": {"include": True},
                "max_tokens": 900}
    Agent(pool).run_sync("hi", model_settings=settings)
    assert a.settings[-1] == {"temperature": 0.2, "max_tokens": 900, "extra_body": {"enable_thinking": False}}


def test_run_logged_records_the_member_that_answered(repo: Repository) -> None:
    repo.add_novel(novel_id="pntt1", title="N", genre_profile="xianxia", total_chapters=10)
    pool = make_pool({"a": Fake(error=quota_error("a")), "b": Fake("from b")}, repo)
    run_logged(Agent(pool), "hi", repo=repo, novel_id="pntt1", stage="s7", chapter_id=3,
               model="qwen38-udiq4-hui-10k", prompt_version="v1")
    rows = [r["model"] for r in repo._conn.execute("SELECT model FROM llm_runs")]  # type: ignore[attr-defined]
    assert rows == ["b"]


def test_answered_by_falls_back_to_the_configured_name() -> None:
    assert answered_by(object(), "local") == "local"


POOL_CFG = {
    "models": {"local_extractor": {"serving_name": "local-m"}},
    "model_pools": {"qwen_free": {"provider": "dashscope", "base_url": "https://example.test/v1",
                                  "api_key_setting": "dashscope_api_key", "models": ["m1", "m2"],
                                  "extra_body": {"enable_thinking": False}}},
    "stage_pools": {"s7": "qwen_free"},
}
SETTING_ENV = "DASHSCOPE" + "_API_KEY"


def test_stage_model_uses_the_pool_only_for_named_stages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(SETTING_ENV, "test-key")
    assert stage_pool_name(POOL_CFG, "s7") == "qwen_free" and stage_pool_name(POOL_CFG, "s5") is None
    pool = stage_model(POOL_CFG, "s7")
    assert isinstance(pool, PoolModel)
    assert [m.member_name for m in pool.models[:-1]] == ["m1", "m2"]  # type: ignore[attr-defined]
    assert pool.models[0].wrapped.client.api_key == "test-key"  # type: ignore[attr-defined]
    monkeypatch.setattr(model_pool, "local_model", lambda config: "LOCAL")
    assert stage_model(POOL_CFG, "s5") == "LOCAL"
    assert stage_model({"models": {}}, "s7") == "LOCAL"


def test_build_pool_refuses_a_missing_pool_or_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(SETTING_ENV, raising=False)
    with pytest.raises(ValueError, match="model_pools.nope is missing"):
        build_pool(POOL_CFG, "nope")
    with pytest.raises(ValueError, match="dashscope_api_key is not set"):
        build_pool(POOL_CFG, "qwen_free")


def test_build_pool_skips_models_already_marked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Repository) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(SETTING_ENV, "test-key")
    repo.mark_model_exhausted(novel_id="pntt1", provider="dashscope", model="m1", error="used up")
    pool = build_pool(POOL_CFG, "qwen_free", repo=repo, novel_id="pntt1")
    assert pool.state.exhausted == {"m1"}


def test_a_pool_entry_can_carry_its_own_extra_body(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(SETTING_ENV, "test-key")
    cfg = {**POOL_CFG, "model_pools": {"qwen_free": {**POOL_CFG["model_pools"]["qwen_free"],
           "models": ["m1", {"name": "m2", "extra_body": {"enable_thinking": True}}]}}}
    pool = build_pool(cfg, "qwen_free")
    m1, m2 = pool.models[0], pool.models[1]
    assert (m1.member_name, m2.member_name) == ("m1", "m2")  # type: ignore[attr-defined]
    assert m1._member_settings({}) == {"extra_body": {"enable_thinking": False}}  # type: ignore[attr-defined]
    assert m2._member_settings({}) == {"extra_body": {"enable_thinking": True}}  # type: ignore[attr-defined]
