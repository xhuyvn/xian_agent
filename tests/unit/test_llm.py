"""Unit tests for xn.llm module, factory caching, and run_logged SQLite tracking."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
from typing import Any
import unittest.mock
import pytest

from pydantic_ai import Agent, ModelRetry
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from xn.llm import _count_retries, _extract_tokens, local_model, run_logged
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository


@pytest.fixture
def db_conn(tmp_path: Path):
    """Provide an initialized SQLite database connection."""
    db_file = tmp_path / "test_llm.db"
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


def test_import_xn_llm_makes_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify importing xn.llm does not instantiate HTTP or OpenAI clients."""
    import httpx

    mock_client = unittest.mock.MagicMock()
    mock_async_client = unittest.mock.MagicMock()
    monkeypatch.setattr(httpx, "Client", mock_client)
    monkeypatch.setattr(httpx, "AsyncClient", mock_async_client)

    sys.modules.pop("xn.llm", None)
    import xn.llm  # noqa: F401

    mock_client.assert_not_called()
    mock_async_client.assert_not_called()


def test_local_model_uses_serving_name_and_is_cached() -> None:
    """Verify local_model uses serving_name from config and caches instances."""
    cfg1 = {
        "models": {
            "local_extractor": {
                "name": "qwen2.5:14b",
                "serving_name": "qwen14b-12k",
                "base_url": "http://localhost:11434/v1",
                "temperature": 0.1,
            }
        }
    }

    m1 = local_model(cfg1)
    assert m1.model_name == "qwen14b-12k"

    # Repeated call with same config retrieves cached model
    m2 = local_model(cfg1)
    assert m1 is m2

    # Different serving_name or base_url creates and caches a new model instance
    cfg2 = {
        "models": {
            "local_extractor": {
                "serving_name": "custom-qwen-14b",
                "base_url": "http://localhost:11434/v1",
            }
        }
    }
    m3 = local_model(cfg2)
    assert m3.model_name == "custom-qwen-14b"
    assert m3 is not m1


def test_extract_tokens_helper() -> None:
    """Verify token extraction handles various usage formats and None."""
    class MockResult:
        def __init__(self, usage_obj: Any) -> None:
            self.usage = usage_obj

    # Case 1: request_tokens / response_tokens
    usage1 = unittest.mock.MagicMock()
    usage1.request_tokens = 120
    usage1.response_tokens = 45
    assert _extract_tokens(MockResult(usage1)) == (120, 45)

    # Case 2: callable usage returning input_tokens / output_tokens
    usage2 = {"input_tokens": 80, "output_tokens": 30}
    assert _extract_tokens(MockResult(lambda: usage2)) == (80, 30)

    # Case 3: None usage
    assert _extract_tokens(MockResult(None)) == (None, None)

    # Case 4: No usage attribute
    assert _extract_tokens(object()) == (None, None)


def test_count_retries_helper() -> None:
    """Verify count_retries correctly parses messages with parts."""
    class MockPart:
        def __init__(self, part_kind: str) -> None:
            self.part_kind = part_kind

    class MockMessage:
        def __init__(self, parts: list[Any]) -> None:
            self.parts = parts

    class MockResult:
        def __init__(self, messages: list[Any]) -> None:
            self._messages = messages

        def all_messages(self) -> list[Any]:
            return self._messages

    # No retries
    res0 = MockResult([MockMessage([MockPart("user-prompt")]), MockMessage([MockPart("text")])])
    assert _count_retries(res0) == 0

    # 2 retries
    res2 = MockResult([
        MockMessage([MockPart("user-prompt")]),
        MockMessage([MockPart("retry-prompt")]),
        MockMessage([MockPart("retry-prompt")]),
        MockMessage([MockPart("text")]),
    ])
    assert _count_retries(res2) == 2


def test_run_logged_success_writes_row_and_returns_result(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify run_logged writes exactly one successful row in llm_runs with usage and fields."""
    agent = Agent(TestModel(), output_type=str)

    result = run_logged(
        agent,
        "Analyze this chapter content",
        repo=repo,
        novel_id="pntt1",
        stage="s1",
        chapter_id=1,
        model="qwen14b-12k",
        prompt_version="a1b2c3d4",
    )

    assert result is not None
    assert result.output is not None

    cur = db_conn.execute("SELECT * FROM llm_runs WHERE novel_id = 'pntt1' AND chapter_id = 1")
    rows = cur.fetchall()
    assert len(rows) == 1

    row = rows[0]
    assert row["novel_id"] == "pntt1"
    assert row["stage"] == "s1"
    assert row["chapter_id"] == 1
    assert row["model"] == "qwen14b-12k"
    assert row["prompt_version"] == "a1b2c3d4"
    assert row["ok"] == 1
    assert row["cost_usd"] == 0.0
    assert row["egress_class"] == "local"
    assert row["payload_chapters"] == "1"
    assert row["retries"] == 0
    assert len(row["run_id"]) == 32
    assert row["latency_ms"] >= 0
    assert row["created_at"] is not None


def test_run_logged_with_retries(repo: Repository, db_conn: sqlite3.Connection) -> None:
    """Verify run_logged correctly counts retry prompts in result messages."""
    attempts = 0

    def retry_model_func(messages: list[Any], info: Any) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ModelRetry("Please format output according to instructions")
        return "Extraction result after 1 retry"

    agent = Agent(FunctionModel(retry_model_func), output_type=str, retries=2)

    result = run_logged(
        agent,
        "Run with retry",
        repo=repo,
        novel_id="pntt1",
        stage="s1",
        chapter_id=2,
        model="qwen14b-12k",
        prompt_version="a1b2c3d4",
    )

    assert result.output == "Extraction result after 1 retry"

    cur = db_conn.execute("SELECT * FROM llm_runs WHERE novel_id = 'pntt1' AND chapter_id = 2")
    row = cur.fetchone()
    assert row is not None
    assert row["ok"] == 1
    assert row["retries"] == 1
    assert row["chapter_id"] == 2


def test_run_logged_failure_writes_row_and_reraises(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    """Verify run_logged writes an ok=0 row with null tokens and retries on exception, then re-raises."""
    def failing_model_func(messages: list[Any], info: Any) -> str:
        raise RuntimeError("Inference connection failed")

    failing_agent = Agent(FunctionModel(failing_model_func), output_type=str)

    with pytest.raises(RuntimeError, match="Inference connection failed"):
        run_logged(
            failing_agent,
            "Failing prompt",
            repo=repo,
            novel_id="pntt1",
            stage="s1",
            chapter_id=99,
            model="qwen14b-12k",
            prompt_version="a1b2c3d4",
        )

    cur = db_conn.execute("SELECT * FROM llm_runs WHERE novel_id = 'pntt1' AND chapter_id = 99")
    rows = cur.fetchall()
    assert len(rows) == 1

    row = rows[0]
    assert row["ok"] == 0
    assert row["input_tokens"] is None
    assert row["output_tokens"] is None
    assert row["retries"] is None
    assert row["novel_id"] == "pntt1"
    assert row["stage"] == "s1"
    assert row["chapter_id"] == 99
    assert row["model"] == "qwen14b-12k"
    assert row["prompt_version"] == "a1b2c3d4"
    assert row["cost_usd"] == 0.0
    assert row["egress_class"] == "local"
    assert row["payload_chapters"] == "99"
    assert row["latency_ms"] >= 0
    assert len(row["run_id"]) == 32


def test_run_logged_logging_failure_does_not_hide_original_exception() -> None:
    """Verify that if repository logging fails during exception handling, the original exception is re-raised."""
    def failing_model_func(messages: list[Any], info: Any) -> str:
        raise ValueError("Original model failure")

    failing_agent = Agent(FunctionModel(failing_model_func), output_type=str)

    broken_repo = unittest.mock.MagicMock()
    broken_repo.add_llm_run.side_effect = sqlite3.OperationalError("Disk full error")

    with pytest.raises(ValueError, match="Original model failure"):
        run_logged(
            failing_agent,
            "Prompt",
            repo=broken_repo,
            novel_id="pntt1",
            stage="s1",
            chapter_id=100,
            model="qwen14b-12k",
            prompt_version="v1",
        )
