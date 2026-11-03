"""Unit tests for the OpenRouter wrapper: egress rule, retries, fallback and spend cap."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Any, Callable
import pytest

from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelHTTPError,
    ModelTimeoutError,
)
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from xn.config import CloudSettings, load_config
from xn.llm import (
    CompactItem,
    EgressError,
    MAX_CHUNKS,
    SpendCapError,
    UpstreamError,
    cloud_model,
    render_compact_prompt,
    run_compact,
    validate_compact,
)
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository
from xn.text import CHARS_PER_TOKEN

CONFIG: dict[str, Any] = {"daily_spend_cap_usd": 1.0, "fallback_model": None}
ITEMS = [
    CompactItem(kind="event", chapter_id=3, text="Hàn Lập nhận được bình nhỏ"),
    CompactItem(kind="event", chapter_id=7, text="Bình nhỏ <thăng cấp>"),
]


@pytest.fixture
def db_conn(tmp_path: Path):
    """Provide an initialized SQLite connection with one novel."""
    conn = connect(tmp_path / "test_cloud.db")
    init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def repo(db_conn: sqlite3.Connection) -> Repository:
    """Provide a Repository with one novel."""
    repository = Repository(db_conn)
    repository.add_novel(novel_id="nov", title="N", genre_profile="xianxia", total_chapters=100)
    return repository


class FakeCloud:
    """Model factory whose calls follow a script of costs or exceptions."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.names: list[str] = []
        self.prompts: list[str] = []
        self.settings: list[Any] = []

    def __call__(self, name: str) -> FunctionModel:
        self.names.append(name)

        def respond(messages: list[Any], info: AgentInfo) -> ModelResponse:
            self.prompts.append(str(messages[-1].parts[-1].content))
            self.settings.append(info.model_settings)
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            details = {} if step is None else {"cost": step}
            return ModelResponse(
                parts=[TextPart("xong")],
                usage=RequestUsage(input_tokens=10, output_tokens=5),
                provider_details=details,
            )

        return FunctionModel(respond)


def call(repo: Repository, fake: FakeCloud, config: dict[str, Any] = CONFIG, **kwargs: Any) -> Any:
    """Call run_compact with test defaults and no real sleeping."""
    sleeps: list[float] = []
    kwargs.setdefault("sleep", sleeps.append)
    result = run_compact(
        Agent(),
        "Hàn Lập có gì?",
        kwargs.pop("items", ITEMS),
        repo=repo,
        novel_id="nov",
        stage="synthesize",
        chapter_max=10,
        model_name="main/model",
        prompt_version="abcd1234",
        config=config,
        temperature=0.3,
        model_factory=fake,
        **kwargs,
    )
    return result


def runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return llm_runs rows in write order."""
    return conn.execute("SELECT * FROM llm_runs ORDER BY rowid").fetchall()


def http_error(status: int) -> ModelHTTPError:
    return ModelHTTPError(status_code=status, model_name="main/model")


def test_success_logs_compact_egress_chapters_and_cost(repo: Repository, db_conn: Any) -> None:
    fake = FakeCloud(0.002)
    result = call(repo, fake)

    assert result.output == "xong"
    row = runs(db_conn)[0]
    assert len(runs(db_conn)) == 1
    assert row["egress_class"] == "compact"
    assert row["payload_chapters"] == "3,7"
    assert row["cost_usd"] == pytest.approx(0.002)
    assert row["model"] == "main/model"
    assert row["ok"] == 1 and row["retries"] == 0
    assert row["input_tokens"] == 10 and row["output_tokens"] == 5
    assert fake.settings[0]["max_tokens"] == 2500
    assert fake.settings[0]["temperature"] == 0.3
    assert fake.settings[0]["openrouter_usage"] == {"include": True}


def test_prompt_holds_only_question_and_items_as_escaped_data(repo: Repository) -> None:
    fake = FakeCloud(0.001)
    call(repo, fake)
    prompt = fake.prompts[0]
    assert '<item chapter="7">Bình nhỏ &lt;thăng cấp&gt;</item>' in prompt
    assert "<question>Hàn Lập có gì?</question>" in prompt
    assert render_compact_prompt("q", []) == "<question>q</question>\n<evidence>\n\n</evidence>"


def test_chunk_items_are_marked_and_events_are_not() -> None:
    prompt = render_compact_prompt(
        "q", [CompactItem("event", 3, "sự kiện"), CompactItem("chunk", 4, "đoạn <trích>")]
    )
    assert '<item chapter="3">sự kiện</item>' in prompt
    assert '<item chapter="4" kind="chunk">đoạn &lt;trích&gt;</item>' in prompt


@pytest.mark.parametrize(
    "items, match",
    [
        ([CompactItem(kind="raw_chapter", chapter_id=1, text="x")], "not allowed"),  # type: ignore[arg-type]
        ([CompactItem(kind="event", chapter_id=11, text="x")], "outside"),
        ([CompactItem(kind="event", chapter_id=0, text="x")], "outside"),
        ([CompactItem(kind="chunk", chapter_id=1, text="x" * 2000)], "450"),
        ([CompactItem(kind="chunk", chapter_id=1, text="x")] * (MAX_CHUNKS + 1), "chunks"),
        ([CompactItem(kind="event", chapter_id=1, text="x" * 1400)] * 50, "budget"),
    ],
)
def test_validate_compact_rejects_payloads(items: list[CompactItem], match: str) -> None:
    with pytest.raises(EgressError, match=match):
        validate_compact(items, 10)


def test_chunk_limit_boundaries_are_allowed() -> None:
    text = "x" * int(450 * CHARS_PER_TOKEN)
    validate_compact([CompactItem(kind="chunk", chapter_id=10, text=text)] * MAX_CHUNKS, 10)


def test_bad_payload_and_long_context_never_reach_the_model(repo: Repository, db_conn: Any) -> None:
    fake = FakeCloud()
    with pytest.raises(EgressError):
        call(repo, fake, items=[CompactItem(kind="event", chapter_id=99, text="x")])
    with pytest.raises(EgressError, match="long_context"):
        call(repo, fake, egress_class="long_context")
    assert fake.names == [] and runs(db_conn) == []


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retries_with_exponential_backoff_then_succeeds(
    repo: Repository, db_conn: Any, status: int
) -> None:
    fake = FakeCloud(http_error(status), http_error(status), http_error(status), 0.001)
    sleeps: list[float] = []
    result = call(repo, fake, sleep=sleeps.append)

    assert result.output == "xong"
    assert sleeps == [1.0, 2.0, 4.0]
    rows = runs(db_conn)
    assert [r["ok"] for r in rows] == [0, 0, 0, 1]
    assert [r["retries"] for r in rows] == [0, 1, 2, 3]
    assert all(r["egress_class"] == "compact" and r["payload_chapters"] == "3,7" for r in rows)


def test_timeout_is_retried_and_reported(repo: Repository) -> None:
    timeout = ModelTimeoutError("main/model", "timed out")
    fake = FakeCloud(timeout, timeout, timeout, timeout)
    with pytest.raises(UpstreamError) as err:
        call(repo, fake)
    assert err.value.code == "UPSTREAM_TIMEOUT"
    assert len(fake.names) == 4


def test_dropped_connection_is_retried_after_a_short_pause(repo: Repository) -> None:
    drop = ModelConnectionError("main/model", "Connection error.")
    fake = FakeCloud(drop, http_error(503), drop, 0.001)
    sleeps: list[float] = []
    call(repo, fake, sleep=sleeps.append)
    # The 503 keeps its backoff step; each drop before it gets the short pause.
    assert sleeps == [0.25, 2.0, 0.25]


def test_slow_connection_failure_and_timeout_keep_the_backoff(
    repo: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeout = ModelTimeoutError("main/model", "timed out")
    sleeps: list[float] = []
    call(repo, FakeCloud(timeout, timeout, 0.001), sleep=sleeps.append)
    assert sleeps == [1.0, 2.0]

    # Patch the globals run_compact reads, since another test reloads xn.llm.
    monkeypatch.setitem(run_compact.__globals__, "CLOUD_FAST_FAIL_S", 0.0)
    drop = ModelConnectionError("main/model", "Connection error.")
    sleeps = []
    call(repo, FakeCloud(drop, 0.001), sleep=sleeps.append)
    assert sleeps == [1.0]


def test_cloud_provider_routing_is_sent_only_when_configured(repo: Repository) -> None:
    fake = FakeCloud(0.001, 0.001)
    call(repo, fake)
    call(repo, fake, config={**CONFIG, "cloud_provider": {"sort": "latency"}})
    assert "openrouter_provider" not in fake.settings[0]
    assert fake.settings[1]["openrouter_provider"] == {"sort": "latency"}


def test_real_config_routes_by_latency() -> None:
    config = load_config("pntt1", configs_dir=Path(__file__).resolve().parents[2] / "configs")
    assert config["cloud_provider"] == {"sort": "latency"}


def test_rate_limit_outlasting_retries_stops_without_fallback(
    repo: Repository, db_conn: Any
) -> None:
    fake = FakeCloud(*[http_error(429) for _ in range(4)])
    with pytest.raises(UpstreamError) as err:
        call(repo, fake)
    assert err.value.code == "UPSTREAM_RATE_LIMIT"
    assert fake.names == ["main/model"] * 4
    assert len(runs(db_conn)) == 4


def test_fallback_runs_once_after_retries(repo: Repository, db_conn: Any) -> None:
    fake = FakeCloud(*[http_error(500) for _ in range(4)], 0.001)
    config = {**CONFIG, "fallback_model": "other/model"}
    result = call(repo, fake, config=config)

    assert result.output == "xong"
    assert fake.names == ["main/model"] * 4 + ["other/model"]
    assert runs(db_conn)[-1]["model"] == "other/model"
    assert runs(db_conn)[-1]["ok"] == 1


def test_other_client_errors_are_not_retried(repo: Repository, db_conn: Any) -> None:
    fake = FakeCloud(http_error(400))
    config = {**CONFIG, "fallback_model": "other/model"}
    with pytest.raises(ModelAPIError):
        call(repo, fake, config=config)
    assert fake.names == ["main/model"]
    assert [r["ok"] for r in runs(db_conn)] == [0]


def test_unset_cap_refuses_before_any_call(repo: Repository, db_conn: Any) -> None:
    fake = FakeCloud()
    with pytest.raises(SpendCapError, match="not set"):
        call(repo, fake, config={"daily_spend_cap_usd": None})
    assert fake.names == [] and runs(db_conn) == []


def add_spend(repo: Repository, cost: float | None, *, egress: str = "compact") -> None:
    """Record one finished call with the given cost."""
    repo.add_llm_run(
        novel_id="nov",
        run_id=f"seed-{len(str(cost))}-{egress}",
        stage="synthesize",
        chapter_id=None,
        model="m",
        prompt_version="v",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
        retries=0,
        ok=1,
        cost_usd=cost,
        egress_class=egress,
        payload_chapters="1",
    )


def test_cap_counts_cloud_spend_only(repo: Repository) -> None:
    add_spend(repo, 0.999)
    add_spend(repo, 5.0, egress="local")
    assert call(repo, FakeCloud(0.001)).output == "xong"


def test_reached_cap_refuses(repo: Repository) -> None:
    add_spend(repo, 1.0)
    fake = FakeCloud()
    with pytest.raises(SpendCapError, match="reached"):
        call(repo, fake)
    assert fake.names == []


def test_cap_is_checked_before_each_retry(repo: Repository) -> None:
    add_spend(repo, 0.5)

    def spend_then_fail(_: float) -> None:
        add_spend(repo, 0.6, egress="long_context")

    fake = FakeCloud(http_error(500))
    with pytest.raises(SpendCapError):
        call(repo, fake, sleep=spend_then_fail)
    assert len(fake.names) == 1


def test_yesterdays_spend_does_not_count(repo: Repository, db_conn: Any) -> None:
    add_spend(repo, 5.0)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    db_conn.execute("UPDATE llm_runs SET created_at = ?", (yesterday,))
    db_conn.commit()
    assert call(repo, FakeCloud(0.001)).output == "xong"


def test_missing_reported_cost_is_recorded_and_blocks_the_next_call(
    repo: Repository, db_conn: Any
) -> None:
    assert call(repo, FakeCloud(None)).output == "xong"
    assert runs(db_conn)[0]["cost_usd"] is None
    with pytest.raises(SpendCapError, match="no reported cost"):
        call(repo, FakeCloud())


def test_spend_spans_all_novels(repo: Repository) -> None:
    repo.add_novel(novel_id="other", title="O", genre_profile="xianxia", total_chapters=10)
    add_spend(repo, 0.25)
    repo.add_llm_run(
        novel_id="other",
        run_id="other-run",
        stage="synthesize",
        chapter_id=None,
        model="m",
        prompt_version="v",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
        retries=0,
        ok=1,
        cost_usd=0.5,
        egress_class="compact",
        payload_chapters="1",
    )
    assert repo.cloud_spend_since(since="2000-01-01") == (0.75, 0)


def test_cost_of_a_failed_output_validation_is_still_counted(
    repo: Repository, db_conn: Any
) -> None:
    def reject(text: str) -> str:
        from pydantic_ai import ModelRetry

        raise ModelRetry("sai")

    agent: Agent[None, str] = Agent(retries=0)
    agent.output_validator(reject)
    fake = FakeCloud(0.01, 0.01)
    with pytest.raises(Exception):
        run_compact(
            agent,
            "q",
            ITEMS,
            repo=repo,
            novel_id="nov",
            stage="synthesize",
            chapter_max=10,
            model_name="main/model",
            prompt_version="v",
            config=CONFIG,
            temperature=0.3,
            model_factory=fake,
        )
    assert runs(db_conn)[0]["ok"] == 0
    assert runs(db_conn)[0]["cost_usd"] == pytest.approx(0.01)


def test_cloud_model_needs_a_key_and_disables_client_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(UpstreamError, match="OPENROUTER_API_KEY"):
        cloud_model("main/model")

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://localhost:9/v1")
    model = cloud_model("main/model")
    client = model.client
    assert client.max_retries == 0
    assert client.timeout == 90.0
    assert str(client.base_url).startswith("http://localhost:9/v1")
    assert "test-key" not in repr(CloudSettings())
