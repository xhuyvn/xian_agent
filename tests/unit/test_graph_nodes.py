"""Unit tests for the route, bound, resolve, retrieve and gate nodes, using fake models."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any
import pytest

from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from xn.graph.deps import GraphDeps, QueryError
from xn.graph.nodes.bound import bound
from xn.graph.nodes.gate import gate
from xn.graph.nodes.resolve import resolve_entities
from xn.graph.nodes.retrieve import EVENTS_PER_ENTITY, retrieve
from xn.graph.nodes.route import route
from xn.llm import MAX_EVIDENCE_TOKENS
from xn.schemas.state import EvidenceItem, EvidencePack, RouterDecision
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository
from xn.text import estimate_tokens

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG: dict[str, Any] = {
    "models": {
        "local_extractor": {"serving_name": "fake-local", "output_mode": "tool", "temperature": 0.1}
    }
}


@pytest.fixture
def db_conn(tmp_path: Path):
    """Provide an initialized SQLite connection."""
    conn = connect(tmp_path / "test_nodes.db")
    init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def repo(db_conn: sqlite3.Connection) -> Repository:
    """Provide a repository with a novel, entities, aliases and an identity link."""
    repository = Repository(db_conn)
    repository.add_novel(novel_id="nov", title="N", genre_profile="xianxia", total_chapters=100)
    db_conn.execute(
        "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) VALUES "
        "('nov', 1, 'Hàn Lập', 'character', 1), "
        "('nov', 2, 'Lệ Phi Vũ', 'character', 5), "
        "('nov', 3, 'Hàn Lão Ma', 'character', 60), "
        "('nov', 4, 'Hư Thiên Đỉnh', 'weapon_treasure', 70)"
    )
    db_conn.execute(
        "INSERT INTO entity_aliases (novel_id, alias_norm, entity_id, first_chapter, source) VALUES "
        "('nov', 'hàn lập', 1, 1, 'exact'), "
        "('nov', 'lệ phi vũ', 2, 5, 'exact'), "
        "('nov', 'hàn lão ma', 3, 60, 'exact'), "
        "('nov', 'hư thiên đỉnh', 4, 70, 'exact')"
    )
    db_conn.execute(
        "INSERT INTO entity_links (novel_id, entity_a, entity_b, revealed_chapter) "
        "VALUES ('nov', 1, 3, 65)"
    )
    db_conn.commit()
    return repository


def deps_for(repo: Repository, **kwargs: Any) -> GraphDeps:
    """Build dependencies using the real prompts directory."""
    return GraphDeps(repo=repo, config=CONFIG, prompts_dir=REPO_ROOT / "prompts", **kwargs)


def decision(**kwargs: Any) -> RouterDecision:
    """Build a RouterDecision with test defaults."""
    return RouterDecision(**{"intent": "deep_dive", "mentions": [], **kwargs})


def add_events(conn: sqlite3.Connection, entity_id: int, rows: list[tuple[int, str]]) -> None:
    """Insert events given as (chapter, event_type), one ordinal per row."""
    start = conn.execute("SELECT COALESCE(MAX(id), 0) FROM entity_events").fetchone()[0]
    for offset, (chapter, event_type) in enumerate(rows, start=1):
        conn.execute(
            "INSERT INTO entity_events (novel_id, id, chapter_id, entity_id, event_type, ordinal, "
            "description, evidence) VALUES ('nov', ?, ?, ?, ?, ?, ?, 'trích dẫn')",
            (start + offset, chapter, entity_id, event_type, offset, f"sự kiện {chapter}"),
        )
    conn.commit()


# --- route ---


def test_route_returns_decision_and_logs_one_local_run(repo: Repository, db_conn: Any) -> None:
    model = TestModel(
        custom_output_args={"intent": "timeline", "mentions": ["Hàn Lập"], "chapter_hint": 40}
    )
    out = route(
        {"novel_id": "nov", "query": "Đến chương 40 Hàn Lập ra sao?"},
        deps_for(repo, router_model=model),
    )

    assert out["decision"].intent == "timeline"
    assert out["decision"].mentions == ["Hàn Lập"]
    assert out["decision"].chapter_hint == 40
    rows = db_conn.execute("SELECT * FROM llm_runs").fetchall()
    assert len(rows) == 1
    assert rows[0]["stage"] == "route" and rows[0]["egress_class"] == "local"
    assert rows[0]["model"] == "fake-local" and rows[0]["chapter_id"] is None


def test_route_falls_back_to_factual_qa_on_model_failure(repo: Repository) -> None:
    def explode(messages: list[Any], info: Any) -> Any:
        raise RuntimeError("ollama down")

    out = route(
        {"novel_id": "nov", "query": "q"}, deps_for(repo, router_model=FunctionModel(explode))
    )
    assert out["decision"] == RouterDecision(intent="factual_qa", mentions=[])
    assert "ollama down" in out["errors"][0]


def test_route_clears_the_previous_turn(repo: Repository) -> None:
    out = route(
        {"novel_id": "nov", "query": "q", "refusal": "cũ", "entity_ids": [1]},
        deps_for(repo, router_model=TestModel(custom_output_args={"intent": "factual_qa"})),
    )
    assert out["refusal"] is None and out["answer"] is None and out["evidence"] is None
    assert out["entity_ids"] == []


def test_repeated_mentions_are_dropped_in_order_before_the_size_limit() -> None:
    mentions = ["Thanh Trúc", "Phong Vân", "thanh trúc", "Phong Vân", "Hàn Lập", "Hàn Lập", "X"]
    assert decision(mentions=mentions).mentions == ["Thanh Trúc", "Phong Vân", "Hàn Lập", "X"]


def test_more_than_four_mentions_is_rejected_as_router_failure(repo: Repository) -> None:
    model = TestModel(
        custom_output_args={"intent": "compare", "mentions": ["a", "b", "c", "d", "e"]}
    )
    out = route({"novel_id": "nov", "query": "q"}, deps_for(repo, router_model=model))
    assert out["decision"].intent == "factual_qa" and out["decision"].mentions == []


# --- bound ---


@pytest.mark.parametrize(
    "until, hint, progress, expected",
    [
        (30, 40, 50, 30),  # explicit beats everything
        (None, 40, 50, 40),  # number in the question beats progress
        (None, None, 50, 50),  # reader progress beats total
        (None, None, None, 100),  # total is the last resort
        (500, None, None, 100),  # never above total
        (None, 500, None, 100),
    ],
)
def test_bound_precedence(
    repo: Repository, until: int | None, hint: int | None, progress: int | None, expected: int
) -> None:
    if progress is not None:
        repo.set_reader_progress(novel_id="nov", chapter=progress)
    out = bound(
        {"novel_id": "nov", "until": until, "decision": decision(chapter_hint=hint)},
        deps_for(repo),
    )
    assert out == {"chapter_max": expected}


def test_bound_ignores_a_meaningless_hint(repo: Repository) -> None:
    out = bound({"novel_id": "nov", "decision": decision(chapter_hint=0)}, deps_for(repo))
    assert out == {"chapter_max": 100}


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Đến chương 30 Mặc Đại Phu đã làm gì?", 30),
        ("Tính tới hết chương 40 có gì?", 40),
        ("đến cuối chương 40", 40),
        ("tới tận chương 40", 40),
        ("không quá chương 40", 40),
        ("chương 40 trở về trước", 40),
        ("Chương 40 trở xuống có gì?", 40),
        ("chương 40 đổ lại", 40),
        ("den chuong 40", 40),
        ("ĐẾN CHƯƠNG 40", 40),
        ("đến chap 40", 40),
        ("đến chapter 40", 40),
        ("đến c40", 40),
        ("tới c.40", 40),
        ("tới ch 40", 40),
        ("40 chương đầu", 40),
        ("trong 40 chương đầu tiên", 40),
        ("từ chương 10 đến 40", 40),
        ("từ chương 10 đến chương 40", 40),
        ("chương 10-40", 40),
        ("đến chương 60, trước chương 41", 40),  # the smallest bound wins
        ("trước chương 41", 40),  # strictly before the chapter
        ("đến trước chương 41", 40),
        ("trước chương 1", 1),
    ],
)
def test_bound_reads_the_question_when_the_model_misses_the_hint(
    repo: Repository, query: str, expected: int
) -> None:
    out = bound({"novel_id": "nov", "query": query, "decision": decision()}, deps_for(repo))
    assert out == {"chapter_max": expected}


@pytest.mark.parametrize(
    "query",
    [
        "Chương 30 có gì?",
        "Ở chương 30 Hàn Lập làm gì?",
        "từ chương 30 trở đi",
        "sau chương 30",
        "đến chương 0",
        "trước chương 0",
        "Hàn Lập đến Hoàng Phong Cốc thế nào?",
        "đến cửa 30",
        "Hàn Lập mạnh hơn 30 người",
    ],
)
def test_bound_does_not_invent_a_limit(repo: Repository, query: str) -> None:
    out = bound({"novel_id": "nov", "query": query, "decision": decision()}, deps_for(repo))
    assert out == {"chapter_max": 100}


def test_bound_prefers_model_hint_and_explicit_over_question(repo: Repository) -> None:
    state = {"novel_id": "nov", "query": "đến chương 30", "decision": decision(chapter_hint=40)}
    assert bound(state, deps_for(repo)) == {"chapter_max": 40}
    assert bound({**state, "until": 50}, deps_for(repo)) == {"chapter_max": 50}


def test_follow_up_with_bound_only_in_text_is_refused(repo: Repository) -> None:
    out = bound(
        {"novel_id": "nov", "chapter_max": 80, "query": "đến chương 30", "decision": decision()},
        deps_for(repo),
    )
    assert "cuộc trò chuyện mới" in out["refusal"]


def test_bound_rejects_an_explicit_bound_below_one(repo: Repository) -> None:
    with pytest.raises(QueryError) as err:
        bound({"novel_id": "nov", "until": 0, "decision": decision()}, deps_for(repo))
    assert err.value.code == "VALIDATION_FAILED"


def test_bound_rejects_unknown_novel(repo: Repository) -> None:
    with pytest.raises(QueryError) as err:
        bound({"novel_id": "ghost", "decision": decision()}, deps_for(repo))
    assert err.value.code == "NOVEL_NOT_FOUND"


def test_follow_up_inherits_pinned_bound_over_new_progress(repo: Repository) -> None:
    repo.set_reader_progress(novel_id="nov", chapter=90)
    out = bound({"novel_id": "nov", "chapter_max": 20, "decision": decision()}, deps_for(repo))
    assert out == {"chapter_max": 20}


@pytest.mark.parametrize("until, hint", [(30, None), (None, 30), (None, 10), (10, None)])
def test_follow_up_changing_bound_is_refused(
    repo: Repository, until: int | None, hint: int | None
) -> None:
    out = bound(
        {
            "novel_id": "nov",
            "chapter_max": 20,
            "until": until,
            "decision": decision(chapter_hint=hint),
        },
        deps_for(repo),
    )
    assert "cuộc trò chuyện mới" in out["refusal"]
    assert "chapter_max" not in out


def test_follow_up_repeating_the_same_bound_passes(repo: Repository) -> None:
    out = bound(
        {"novel_id": "nov", "chapter_max": 100, "until": 900, "decision": decision()},
        deps_for(repo),
    )
    assert out == {"chapter_max": 100}


# --- resolve_entities ---


def resolve(repo: Repository, mentions: list[str], chapter_max: int) -> dict[str, Any]:
    return resolve_entities(
        {"novel_id": "nov", "chapter_max": chapter_max, "decision": decision(mentions=mentions)},
        deps_for(repo),
    )


def test_resolve_uses_shared_normalization(repo: Repository) -> None:
    out = resolve(repo, ["  HÀN LẬP, "], 50)
    assert out["entity_ids"] == [1]
    assert out["unresolved_mentions"] == []


def test_alias_after_bound_is_not_resolved_or_suggested(repo: Repository) -> None:
    out = resolve(repo, ["Hàn Lão Ma"], 50)
    assert out["entity_ids"] == []
    assert out["unresolved_mentions"] == ["Hàn Lão Ma"]
    assert "Hàn Lão Ma" not in out["suggestions"]["Hàn Lão Ma"]


def test_identity_link_applies_only_once_revealed(repo: Repository) -> None:
    assert resolve(repo, ["Hàn Lập"], 64)["entity_ids"] == [1]
    assert resolve(repo, ["Hàn Lập"], 65)["entity_ids"] == [1, 3]


def test_identity_links_follow_chains(repo: Repository, db_conn: Any) -> None:
    db_conn.execute(
        "INSERT INTO entity_links (novel_id, entity_a, entity_b, revealed_chapter) "
        "VALUES ('nov', 3, 4, 70)"
    )
    db_conn.commit()
    assert resolve(repo, ["Hàn Lập"], 70)["entity_ids"] == [1, 3, 4]


def test_unresolved_gets_top_three_fuzzy_suggestions(repo: Repository, db_conn: Any) -> None:
    db_conn.execute(
        "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) VALUES "
        "('nov', 5, 'Hàn Lập A', 'character', 1), ('nov', 6, 'Hàn Lập B', 'character', 1), "
        "('nov', 7, 'Hàn Lập C', 'character', 1)"
    )
    db_conn.execute(
        "INSERT INTO entity_aliases (novel_id, alias_norm, entity_id, first_chapter, source) VALUES "
        "('nov', 'hàn lập a', 5, 1, 'exact'), ('nov', 'hàn lập b', 6, 1, 'exact'), "
        "('nov', 'hàn lập c', 7, 1, 'exact')"
    )
    db_conn.commit()
    out = resolve(repo, ["Han Lap"], 50)
    assert out["unresolved_mentions"] == ["Han Lap"]
    assert len(out["suggestions"]["Han Lap"]) == 3
    assert all(name.startswith("Hàn Lập") for name in out["suggestions"]["Han Lap"])


def test_unrelated_name_gets_no_suggestions(repo: Repository) -> None:
    assert resolve(repo, ["xyzzy"], 50)["suggestions"] == {"xyzzy": []}


def test_mention_of_only_punctuation_is_unresolved(repo: Repository) -> None:
    out = resolve(repo, ["..."], 50)
    assert out["entity_ids"] == [] and out["unresolved_mentions"] == ["..."]


# --- retrieve ---


def run_retrieve(repo: Repository, entity_ids: list[int], chapter_max: int, intent: str = "deep_dive") -> Any:
    return retrieve(
        {
            "novel_id": "nov",
            "chapter_max": chapter_max,
            "entity_ids": entity_ids,
            "decision": decision(intent=intent),
        },
        deps_for(repo),
    )["evidence"]


def test_retrieve_never_returns_events_after_bound(repo: Repository, db_conn: Any) -> None:
    add_events(db_conn, 1, [(10, "UPGRADE"), (20, "UPGRADE"), (21, "UPGRADE")])
    pack = run_retrieve(repo, [1], 20)
    assert [i.chapter_id for i in pack.items] == [10, 20]
    assert pack.tokens == sum(estimate_tokens(i.cloud_text()) for i in pack.items)


def test_retrieve_ranks_priority_types_first_and_caps_events(
    repo: Repository, db_conn: Any
) -> None:
    add_events(db_conn, 1, [(c, "MENTIONED") for c in range(1, 31)] + [(50, "DESTROYED")])
    pack = run_retrieve(repo, [1], 100)
    assert len(pack.items) == EVENTS_PER_ENTITY
    assert pack.items[0].event_type == "DESTROYED"
    assert pack.items[1].chapter_id == 1


def test_evidence_items_send_no_chapter_quote(repo: Repository, db_conn: Any) -> None:
    add_events(db_conn, 1, [(10, "UPGRADE")])
    item = run_retrieve(repo, [1], 100).items[0]
    assert item.evidence == "trích dẫn"
    assert "trích dẫn" not in item.cloud_text()
    assert item.cloud_text() == "[UPGRADE] Hàn Lập: sự kiện 10"


def test_retrieve_skips_entity_not_yet_visible(repo: Repository, db_conn: Any) -> None:
    add_events(db_conn, 3, [(10, "UPGRADE")])
    assert run_retrieve(repo, [3], 50).items == []


def long_events(conn: sqlite3.Connection, entity_id: int, count: int) -> None:
    """Insert events of about 450 tokens each with a higher-to-lower rank by chapter."""
    add_events(conn, entity_id, [(c, "UPGRADE") for c in range(1, count + 1)])
    conn.execute(
        "UPDATE entity_events SET description = ? WHERE entity_id = ?", ("x" * 1450, entity_id)
    )
    conn.commit()


def test_budget_drops_lowest_ranked_first(repo: Repository, db_conn: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr("xn.graph.nodes.retrieve.EVENTS_PER_ENTITY", 100)
    long_events(db_conn, 1, 60)
    pack = run_retrieve(repo, [1], 100)
    chapters = [i.chapter_id for i in pack.items]
    assert chapters == list(range(1, len(chapters) + 1))
    assert len(chapters) < 60
    assert pack.tokens <= MAX_EVIDENCE_TOKENS


def test_compare_gives_each_entity_half_the_budget(
    repo: Repository, db_conn: Any, monkeypatch: Any
) -> None:
    monkeypatch.setattr("xn.graph.nodes.retrieve.EVENTS_PER_ENTITY", 100)
    long_events(db_conn, 1, 60)
    add_events(db_conn, 2, [(10, "UPGRADE")])
    pack = run_retrieve(repo, [1, 2], 100, intent="compare")
    first = sum(estimate_tokens(i.cloud_text()) for i in pack.items if i.entity_id == 1)
    assert first <= MAX_EVIDENCE_TOKENS // 2
    assert any(i.entity_id == 2 for i in pack.items)


def test_global_budget_trims_every_entity(repo: Repository, db_conn: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr("xn.graph.nodes.retrieve.EVENTS_PER_ENTITY", 100)
    long_events(db_conn, 1, 60)
    long_events(db_conn, 2, 60)
    pack = run_retrieve(repo, [1, 2], 100)
    assert pack.tokens <= MAX_EVIDENCE_TOKENS
    counts = [sum(i.entity_id == e for i in pack.items) for e in (1, 2)]
    assert abs(counts[0] - counts[1]) <= 1


def test_oversized_event_is_dropped(repo: Repository, db_conn: Any) -> None:
    add_events(db_conn, 1, [(1, "UPGRADE"), (2, "UPGRADE")])
    db_conn.execute("UPDATE entity_events SET description = ? WHERE chapter_id = 1", ("x" * 5000,))
    db_conn.commit()
    assert [i.chapter_id for i in run_retrieve(repo, [1], 100).items] == [2]


def test_tenant_isolation_in_retrieve(repo: Repository, db_conn: Any) -> None:
    repo.add_novel(novel_id="other", title="O", genre_profile="xianxia", total_chapters=10)
    db_conn.execute(
        "INSERT INTO entity_events (novel_id, id, chapter_id, entity_id, event_type, ordinal, description) "
        "VALUES ('other', 1, 1, 1, 'UPGRADE', 1, 'của truyện khác')"
    )
    db_conn.commit()
    assert run_retrieve(repo, [1], 100).items == []


# --- gate ---


def pack_of(count: int) -> EvidencePack:
    item = EvidenceItem(
        event_id=1, chapter_id=1, entity_id=1, entity_name="A", event_type="UPGRADE", description="d"
    )
    return EvidencePack(items=[item] * count)


def run_gate(repo: Repository, intent: str, entity_ids: list[int], events: int) -> dict[str, Any]:
    return gate(
        {
            "chapter_max": 42,
            "decision": decision(intent=intent),
            "entity_ids": entity_ids,
            "evidence": pack_of(events),
        },
        deps_for(repo),
    )


@pytest.mark.parametrize(
    "intent, events, passes",
    [
        ("deep_dive", 3, True),
        ("deep_dive", 2, False),
        ("compare", 3, True),
        ("compare", 2, False),
        ("timeline", 1, True),
        ("timeline", 0, False),
        ("factual_qa", 1, True),
        ("factual_qa", 0, False),
    ],
)
def test_gate_thresholds(repo: Repository, intent: str, events: int, passes: bool) -> None:
    out = run_gate(repo, intent, [1], events)
    assert out == ({} if passes else {"refusal": "Không đủ dữ liệu đến chương 42"})


def test_gate_refuses_without_a_resolved_entity(repo: Repository) -> None:
    assert run_gate(repo, "deep_dive", [], 9)["refusal"] == "Không đủ dữ liệu đến chương 42"
