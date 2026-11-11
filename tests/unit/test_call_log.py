r"""Unit tests for the optional full-content call log (XN_CALL_LOG)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from xn.call_log import CALL_LOG_ENV, log_call
from xn.llm import CompactItem, run_compact, run_logged
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository


@pytest.fixture
def repo(tmp_path: Path):
    conn = connect(tmp_path / "log.db")
    init_schema(conn)
    try:
        r = Repository(conn)
        r.add_novel(novel_id="n1", title="N", genre_profile="xianxia", total_chapters=10)
        yield r
    finally:
        conn.close()


def read_log(folder: Path, stage: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (folder / f"{stage}.jsonl").read_text(encoding="utf-8").splitlines()]


def answer(text: str) -> FunctionModel:
    return FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart(text)]), model_name="fake")


def test_nothing_is_written_when_the_switch_is_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Repository) -> None:
    monkeypatch.delenv(CALL_LOG_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    run_logged(Agent(answer("hi"), system_prompt="SYS"), "hello", repo=repo, novel_id="n1", stage="s7",
               chapter_id=3, model="m", prompt_version="v1")
    assert list(tmp_path.rglob("*.jsonl")) == []


def test_a_successful_call_logs_prompts_and_answer_under_its_llm_runs_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Repository
) -> None:
    folder = tmp_path / "calls"
    monkeypatch.setenv(CALL_LOG_ENV, str(folder))
    run_logged(Agent(answer("Hàn Lập"), system_prompt="SYS"), "chương 3", repo=repo, novel_id="n1", stage="s7",
               chapter_id=3, model="m", prompt_version="v1")
    [rec] = read_log(folder, "s7")
    assert (rec["stage"], rec["chapter_id"], rec["model"], rec["ok"], rec["error"]) == ("s7", 3, "m", True, None)
    text = json.dumps(rec["messages"], ensure_ascii=False)
    assert "SYS" in text and "chương 3" in text and "Hàn Lập" in text
    run_ids = [r["run_id"] for r in repo._conn.execute("SELECT run_id FROM llm_runs")]  # type: ignore[attr-defined]
    assert run_ids == [rec["run_id"]]


class Out(BaseModel):
    digest: str


def test_a_failed_call_logs_every_answer_and_retry_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Repository
) -> None:
    folder = tmp_path / "calls"
    monkeypatch.setenv(CALL_LOG_ENV, str(folder))
    agent = Agent(answer('{"digest": "ngắn"}'), output_type=Out, retries=1)

    @agent.output_validator
    def too_short(out: Out) -> Out:
        raise ModelRetry("quá ngắn")

    with pytest.raises(UnexpectedModelBehavior):
        run_logged(agent, "x", repo=repo, novel_id="n1", stage="s7", chapter_id=5, model="m", prompt_version="v1")
    [rec] = read_log(folder, "s7")
    assert rec["ok"] is False and "UnexpectedModelBehavior" in rec["error"]
    text = json.dumps(rec["messages"], ensure_ascii=False)
    assert text.count("ngắn") >= 2 and "quá ngắn" in text


def test_run_compact_logs_each_attempt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, repo: Repository) -> None:
    folder = tmp_path / "calls"
    monkeypatch.setenv(CALL_LOG_ENV, str(folder))
    items = [CompactItem(kind="event", chapter_id=2, text="Hàn Lập vào Hoàng Phong cốc")]
    config = {"daily_spend_cap_usd": 1.0}
    run_compact(Agent(output_type=str), "q?", items, repo=repo, novel_id="n1", stage="s6", chapter_max=5,
                model_name="m", prompt_version="v1", config=config, temperature=0.2,
                model_factory=lambda name: answer("ok"))
    [rec] = read_log(folder, "s6")
    assert rec["ok"] is True and "Hoàng Phong cốc" in json.dumps(rec["messages"], ensure_ascii=False)


def test_a_log_that_cannot_be_written_does_not_fail_the_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv(CALL_LOG_ENV, str(blocker / "sub"))  # a file in the way of the folder
    log_call(run_id="r", stage="s7", chapter_id=None, model="m", messages=[])  # no exception
