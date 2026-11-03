"""Unit tests for synthesize, verify, the compiled graph and xn ask, using fake models."""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any
import warnings
import pytest

from langgraph.checkpoint.serde import jsonplus
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage
from typer.testing import CliRunner

from xn.cli import main as cli
from xn.config import load_config
from xn.graph.deps import GraphDeps
from xn.graph.nodes.verify import verify
from xn.graph.workflow import ask, build_graph, open_checkpointer, thread_novel
from xn.llm import SpendCapError
from xn.schemas.state import Answer, ChunkEvidence, EvidenceItem, EvidencePack
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
CONFIG: dict[str, Any] = {
    "daily_spend_cap_usd": 1.0,
    "fallback_model": None,
    "models": {
        "local_extractor": {"serving_name": "fake-local", "output_mode": "tool"},
        "macro_synthesizer": {"name": "macro/model", "temperature": 0.3},
        "deep_dive_evaluator": {"name": "deep/model", "temperature": 0.4},
    },
}


class FakeCloud:
    """Model factory that records calls and answers with a fixed Answer."""

    def __init__(self, markdown: str = "Hàn Lập đột phá [chương 10].", citations: Any = (10,)) -> None:
        self.markdown = markdown
        self.citations = list(citations)
        self.names: list[str] = []
        self.prompts: list[str] = []

    def __call__(self, name: str) -> FunctionModel:
        self.names.append(name)

        def respond(messages: list[Any], info: AgentInfo) -> ModelResponse:
            self.prompts.append(str(messages[-1].parts[-1].content))
            args = {"markdown": self.markdown, "citations": self.citations}
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, args)],
                usage=RequestUsage(input_tokens=10, output_tokens=5),
                provider_details={"cost": 0.001},
            )

        return FunctionModel(respond)


def router(intent: str = "deep_dive", mentions: Any = ("Hàn Lập",), hint: int | None = None) -> TestModel:
    return TestModel(
        custom_output_args={"intent": intent, "mentions": list(mentions), "chapter_hint": hint}
    )


def seed(conn: sqlite3.Connection, novel_id: str = "nov") -> None:
    """Seed three entities with events on both sides of chapter 50."""
    conn.execute(
        "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) VALUES "
        f"('{novel_id}', 1, 'Hàn Lập', 'character', 1), "
        f"('{novel_id}', 2, 'Lệ Phi Vũ', 'character', 2), "
        f"('{novel_id}', 3, 'Bình Nhỏ', 'weapon_treasure', 3)"
    )
    conn.execute(
        "INSERT INTO entity_aliases (novel_id, alias_norm, entity_id, first_chapter, source) VALUES "
        f"('{novel_id}', 'hàn lập', 1, 1, 'exact'), "
        f"('{novel_id}', 'lệ phi vũ', 2, 2, 'exact'), "
        f"('{novel_id}', 'bình nhỏ', 3, 3, 'exact')"
    )
    event_id = 0
    for entity_id in (1, 2, 3):
        for chapter in (10, 20, 30, 40, 50, 60, 70):
            event_id += 1
            conn.execute(
                "INSERT INTO entity_events (novel_id, id, chapter_id, entity_id, event_type, ordinal, "
                "description, evidence) VALUES (?, ?, ?, ?, 'UPGRADE', 0, ?, 'trích dẫn kín')",
                (novel_id, event_id, chapter, entity_id, f"biến cố chương {chapter}"),
            )
    conn.commit()


@pytest.fixture
def db_conn(tmp_path: Path):
    """Provide a seeded SQLite connection."""
    conn = connect(tmp_path / "xn.db")
    init_schema(conn)
    Repository(conn).add_novel(novel_id="nov", title="N", genre_profile="xianxia", total_chapters=100)
    seed(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def run_query(db_conn: sqlite3.Connection, tmp_path: Path):
    """Run one turn through the real graph with a checkpoint reopened on every call."""

    def run(
        query: str = "q",
        *,
        cloud: FakeCloud | None = None,
        router_model: Any = None,
        thread_id: str = "t-1",
        until: int | None = None,
        retriever: Any = None,
    ) -> Any:
        extra = {"retriever_factory": lambda _config: retriever} if retriever else {}
        deps = GraphDeps(
            repo=Repository(db_conn),
            config=CONFIG,
            prompts_dir=REPO_ROOT / "prompts",
            router_model=router_model or router(),
            cloud_factory=cloud or FakeCloud(),
            sleep=lambda _: None,
            **extra,
        )
        saver = open_checkpointer(tmp_path / "data" / "graph_checkpoints.db")
        try:
            graph = build_graph(deps, saver)
            return ask(graph, thread_id=thread_id, novel_id="nov", query=query, until=until)
        finally:
            saver.conn.close()

    return run


# --- verify ---


def verify_answer(markdown: str, citations: list[int], chapters: list[int], chapter_max: int = 50) -> Any:
    items = [
        EvidenceItem(
            event_id=c, chapter_id=c, entity_id=1, entity_name="A", event_type="UPGRADE", description="d"
        )
        for c in chapters
    ]
    deps = GraphDeps(repo=None, config={})  # type: ignore[arg-type]
    return verify(
        {
            "chapter_max": chapter_max,
            "evidence": EvidencePack(items=items),
            "answer": Answer(markdown=markdown, citations=citations),
        },
        deps,
    )


def test_verify_keeps_valid_citations() -> None:
    out = verify_answer("A đột phá [chương 10]. B bị thương [chương 20].", [10, 20], [10, 20])
    assert out["answer"].markdown == "A đột phá [chương 10]. B bị thương [chương 20]."
    assert out["answer"].citations == [10, 20]
    assert out["grounding_ratio"] == 1.0


def test_verify_strips_citation_above_bound_and_not_in_evidence() -> None:
    out = verify_answer("A [chương 10]. B [chương 60]. C [chương 15].", [10, 60, 15], [10, 60])
    assert out["answer"].markdown == "A [chương 10]. B. C."
    assert out["answer"].citations == [10]
    assert out["grounding_ratio"] == pytest.approx(1 / 3)


def test_verify_rewrites_a_mixed_marker() -> None:
    out = verify_answer("A [chương 10, 99, 20].", [], [10, 20])
    assert out["answer"].markdown == "A [chương 10, 20]."
    assert out["answer"].citations == [10, 20]


def test_verify_handles_decomposed_unicode_and_case() -> None:
    import unicodedata

    text = unicodedata.normalize("NFD", "A [Chương 99].")
    assert verify_answer(text, [], [10])["answer"].markdown == "A."


def test_verify_answer_without_citations_scores_zero() -> None:
    out = verify_answer("Không có trích dẫn.", [], [10])
    assert out["answer"].citations == [] and out["grounding_ratio"] == 0.0


# --- graph ---


def test_deep_dive_on_three_entities_cites_only_chapters_up_to_n(
    run_query: Any, db_conn: Any
) -> None:
    cloud = FakeCloud("X [chương 40]. Y [chương 70]. Z [chương 990].", [40, 70, 990])
    for name, thread_id in [("Hàn Lập", "a"), ("Lệ Phi Vũ", "b"), ("Bình Nhỏ", "c")]:
        state = run_query(
            cloud=cloud, router_model=router(mentions=[name]), thread_id=thread_id, until=50
        )
        assert state["chapter_max"] == 50
        assert state["answer"].citations == [40]
        assert "70" not in state["answer"].markdown and "990" not in state["answer"].markdown
        assert {i.chapter_id for i in state["evidence"].items} == {10, 20, 30, 40, 50}

    assert len(cloud.names) == 3
    for prompt in cloud.prompts:
        assert 'chapter="50"' in prompt
        assert 'chapter="60"' not in prompt and "biến cố chương 70" not in prompt
        assert "trích dẫn kín" not in prompt


@pytest.mark.parametrize(
    "intent, model", [("deep_dive", "deep/model"), ("compare", "deep/model"),
                      ("timeline", "macro/model"), ("factual_qa", "macro/model")]
)
def test_intent_picks_the_configured_model(run_query: Any, intent: str, model: str) -> None:
    cloud = FakeCloud()
    run_query(
        cloud=cloud,
        router_model=router(intent=intent, mentions=("Hàn Lập", "Lệ Phi Vũ")),
        retriever=FakeRetriever(),
    )
    assert cloud.names == [model]


def test_factual_qa_refuses_below_the_rerank_threshold_without_a_cloud_call(run_query: Any) -> None:
    cloud = FakeCloud()
    state = run_query(
        until=50, cloud=cloud, router_model=router("factual_qa"), retriever=FakeRetriever(score=0.29)
    )
    assert state["refusal"] == "Không đủ dữ liệu đến chương 50"
    assert cloud.names == []


def test_compare_with_one_resolved_entity_refuses(run_query: Any) -> None:
    cloud = FakeCloud()
    state = run_query(
        until=50,
        cloud=cloud,
        router_model=router("compare", mentions=("Hàn Lập", "Người Lạ")),
        retriever=FakeRetriever(),
    )
    assert state["decision"].intent == "compare" and state["entity_ids"] == [1]
    assert state["refusal"] == "Không đủ dữ liệu đến chương 50"
    assert cloud.names == []


def test_llm_runs_show_compact_egress_with_bounded_chapters(run_query: Any, db_conn: Any) -> None:
    run_query(until=30)
    rows = db_conn.execute("SELECT * FROM llm_runs ORDER BY rowid").fetchall()
    assert [r["stage"] for r in rows] == ["route", "synthesize"]
    assert rows[1]["egress_class"] == "compact"
    assert rows[1]["payload_chapters"] == "10,20,30"
    assert rows[1]["model"] == "deep/model"
    assert rows[1]["prompt_version"] != "v1"


def test_gate_refusal_makes_no_cloud_call(run_query: Any, db_conn: Any) -> None:
    cloud = FakeCloud()
    state = run_query(cloud=cloud, until=20)  # 2 events is below the deep_dive minimum
    assert state["refusal"] == "Không đủ dữ liệu đến chương 20"
    assert state.get("answer") is None
    assert cloud.names == []
    assert db_conn.execute("SELECT count(*) FROM llm_runs WHERE egress_class = 'compact'").fetchone()[0] == 0


def test_router_failure_ends_in_refusal_not_memory(run_query: Any) -> None:
    def explode(messages: list[Any], info: Any) -> Any:
        raise RuntimeError("down")

    cloud = FakeCloud()
    state = run_query(cloud=cloud, router_model=FunctionModel(explode))
    assert state["decision"].intent == "factual_qa"
    assert state["refusal"].startswith("Không đủ dữ liệu")
    assert cloud.names == []


def test_unresolved_name_returns_suggestions(run_query: Any) -> None:
    state = run_query(router_model=router(mentions=["Han Lap"]))
    assert state["suggestions"] == {"Han Lap": ["Hàn Lập"]}
    assert state["refusal"].startswith("Không đủ dữ liệu")


def test_hint_in_question_bounds_the_answer(run_query: Any) -> None:
    state = run_query(router_model=router(hint=30))
    assert state["chapter_max"] == 30
    assert max(i.chapter_id for i in state["evidence"].items) == 30


def test_follow_up_inherits_novel_and_bound_after_reopening(run_query: Any, tmp_path: Path) -> None:
    first = run_query(until=50)
    second = run_query(query="Còn gì nữa?")
    assert first["chapter_max"] == 50 and second["chapter_max"] == 50
    assert max(i.chapter_id for i in second["evidence"].items) == 50

    saver = open_checkpointer(tmp_path / "data" / "graph_checkpoints.db")
    try:
        assert thread_novel(saver, "t-1") == "nov"
        assert thread_novel(saver, "unknown") is None
    finally:
        saver.conn.close()


def test_new_thread_does_not_inherit(run_query: Any) -> None:
    run_query(until=50)
    assert run_query(thread_id="t-2", until=70)["chapter_max"] == 70


def test_follow_up_changing_bound_is_refused_without_stale_answer(run_query: Any) -> None:
    run_query(until=50)
    cloud = FakeCloud()
    state = run_query(cloud=cloud, until=70)
    assert "cuộc trò chuyện mới" in state["refusal"]
    assert state["chapter_max"] == 50
    assert state.get("answer") is None
    assert cloud.names == []


def test_checkpoint_round_trip_gives_no_serializer_warning(
    run_query: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # langgraph logs each type once per process, so an earlier test would hide it here.
    monkeypatch.setattr(jsonplus, "_warned_unregistered_types", set())
    monkeypatch.setattr(jsonplus, "_warned_blocked_types", set())
    with warnings.catch_warnings(), caplog.at_level(logging.WARNING, logger=jsonplus.logger.name):
        warnings.simplefilter("error")
        run_query(until=50)
        state = run_query(until=50)
    assert [r.getMessage() for r in caplog.records if r.name == jsonplus.logger.name] == []
    assert isinstance(state["answer"], Answer)
    assert isinstance(state["evidence"], EvidencePack)
    assert isinstance(state["evidence"].items[0], EvidenceItem)


class FakeRetriever:
    last_timings: dict[str, float] = {}

    def __init__(self, score: float = 0.9) -> None:
        self.score = score

    def retrieve(
        self, *, novel_id: str, chapter_max: int, query: str, entity_id: int | None = None, top_k: int | None = None
    ) -> list[ChunkEvidence]:
        return [
            ChunkEvidence(
                chapter_id=chapter_max, kind="text", chunk_no=0, text="đoạn", score=self.score, entity_id=entity_id
            )
        ]


def test_checkpoint_round_trip_keeps_chunks_without_serializer_warning(
    run_query: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jsonplus, "_warned_unregistered_types", set())
    monkeypatch.setattr(jsonplus, "_warned_blocked_types", set())
    with warnings.catch_warnings(), caplog.at_level(logging.WARNING, logger=jsonplus.logger.name):
        warnings.simplefilter("error")
        run_query(until=50, router_model=router("factual_qa"), retriever=FakeRetriever())
        state = run_query(until=50, router_model=router("factual_qa"), retriever=FakeRetriever())
    assert [r.getMessage() for r in caplog.records if r.name == jsonplus.logger.name] == []
    assert state["evidence"].chunks == FakeRetriever().retrieve(novel_id="nov", chapter_max=50, query="q")


def test_tenant_isolation_through_the_graph(run_query: Any, db_conn: Any) -> None:
    Repository(db_conn).add_novel(
        novel_id="other", title="O", genre_profile="xianxia", total_chapters=100
    )
    seed(db_conn, "other")
    db_conn.execute(
        "UPDATE entity_events SET description = 'của truyện khác' WHERE novel_id = 'other'"
    )
    db_conn.commit()
    cloud = FakeCloud()
    run_query(cloud=cloud, until=50)
    assert "của truyện khác" not in cloud.prompts[0]


def test_spend_cap_stops_the_graph(run_query: Any, db_conn: Any) -> None:
    db_conn.execute(
        "INSERT INTO llm_runs (run_id, novel_id, cost_usd, egress_class, created_at) "
        "VALUES ('r', 'nov', 5.0, 'compact', '9999')"
    )
    db_conn.commit()
    with pytest.raises(SpendCapError):
        run_query(until=50)


# --- xn ask ---


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Point xn ask at a seeded database with fake models."""
    db_path = tmp_path / "cli.db"
    conn = connect(db_path)
    init_schema(conn)
    Repository(conn).add_novel(
        novel_id="pntt1", title="P", genre_profile="xianxia", total_chapters=2673
    )
    seed(conn, "pntt1")
    conn.close()

    env: dict[str, Any] = {
        "cloud": FakeCloud(),
        "router": router(),
        "args": [
            "--db", str(db_path),
            "--checkpoints", str(tmp_path / "ck" / "graph_checkpoints.db"),
            "--configs-dir", str(REPO_ROOT / "configs"),
            "--prompts-dir", str(REPO_ROOT / "prompts"),
        ],
    }

    def fake_deps(*, config: dict[str, Any], **kwargs: Any) -> GraphDeps:
        config = copy.deepcopy(config)
        config["models"]["local_extractor"]["output_mode"] = "tool"
        return GraphDeps(
            config=config, router_model=env["router"], cloud_factory=env["cloud"], **kwargs
        )

    monkeypatch.setattr(cli, "GraphDeps", fake_deps)
    return env


def invoke(env: dict[str, Any], *args: str) -> Any:
    return CliRunner().invoke(cli.app, ["ask", *args, *env["args"]])


def test_ask_prints_scope_answer_citations_and_thread(cli_env: dict[str, Any]) -> None:
    result = invoke(cli_env, "Hàn Lập ra sao?", "--novel", "pntt1", "--until", "50", "--thread", "t1")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == "Phạm vi: chương 1–50"
    assert "Hàn Lập đột phá [chương 10]." in result.output
    assert "Trích dẫn: 10" in result.output
    assert "Độ bám nguồn: 100%" in result.output
    assert "Thread: t1" in result.output


def test_ask_follow_up_inherits_novel_without_flag(cli_env: dict[str, Any]) -> None:
    invoke(cli_env, "q", "--novel", "pntt1", "--until", "50", "--thread", "t1")
    result = invoke(cli_env, "Còn gì nữa?", "--thread", "t1")
    assert result.exit_code == 0, result.output
    assert "Phạm vi: chương 1–50" in result.output


def test_ask_full_uses_total_chapters(cli_env: dict[str, Any]) -> None:
    result = invoke(cli_env, "q", "--novel", "pntt1", "--full")
    assert result.exit_code == 0, result.output
    assert "Phạm vi: chương 1–2673" in result.output


def test_ask_refusal_prints_scope_and_suggestions(cli_env: dict[str, Any]) -> None:
    cli_env["router"] = router(mentions=["Han Lap"])
    result = invoke(cli_env, "q", "--novel", "pntt1", "--until", "50")
    assert result.exit_code == 0, result.output
    assert "Phạm vi: chương 1–50" in result.output
    assert "Không đủ dữ liệu đến chương 50" in result.output
    assert 'Không tìm thấy "Han Lap". Có phải: Hàn Lập?' in result.output
    assert cli_env["cloud"].names == []


@pytest.mark.parametrize(
    "args, code",
    [
        (["q"], "VALIDATION_FAILED"),
        (["q", "--novel", "ghost"], "NOVEL_NOT_FOUND"),
        (["q", "--novel", "pntt1", "--until", "5", "--full"], "VALIDATION_FAILED"),
        (["q", "--novel", "pntt1", "--until", "0"], "VALIDATION_FAILED"),
    ],
)
def test_ask_rejects_bad_requests(cli_env: dict[str, Any], args: list[str], code: str) -> None:
    result = invoke(cli_env, *args)
    assert result.exit_code == 1
    assert f"Error [{code}]" in result.output


def test_ask_refuses_thread_of_another_novel(
    cli_env: dict[str, Any], tmp_path: Path
) -> None:
    invoke(cli_env, "q", "--novel", "pntt1", "--until", "50", "--thread", "t1")
    result = invoke(cli_env, "q", "--novel", "pntt2", "--thread", "t1")
    assert result.exit_code == 1
    assert "belongs to novel pntt1" in result.output


def test_ask_reports_upstream_failure_after_retries(
    cli_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def rate_limited(name: str) -> FunctionModel:
        def respond(messages: list[Any], info: AgentInfo) -> ModelResponse:
            raise ModelHTTPError(status_code=429, model_name=name)

        return FunctionModel(respond)

    cli_env["cloud"] = rate_limited
    monkeypatch.setattr("xn.llm.time.sleep", lambda _: None)
    result = invoke(cli_env, "q", "--novel", "pntt1", "--until", "50")
    assert result.exit_code == 1
    assert "Error [UPSTREAM_RATE_LIMIT]" in result.output


def test_ask_runs_as_module_entry_point() -> None:
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR), "PYTHONUTF8": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "xn", "ask", "--help"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0
    assert "--until" in proc.stdout
    assert "--thread" in proc.stdout


def test_real_configs_name_both_cloud_models() -> None:
    config = load_config("pntt1", configs_dir=REPO_ROOT / "configs")
    assert config["models"]["macro_synthesizer"]["name"]
    assert config["models"]["deep_dive_evaluator"]["name"]
    assert config["daily_spend_cap_usd"] == 1.0
