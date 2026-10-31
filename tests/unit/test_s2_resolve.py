r"""Unit tests for stage S2 entity resolver and adjudicator."""

from __future__ import annotations

import logging
from pathlib import Path
import sqlite3
from typing import Any, Callable
import pytest

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from xn.pipeline.s2_resolve import (
    ADJUDICATE_MIN_SCORE,
    AUTO_MATCH_SCORE,
    CONTEXTS_PER_ENTITY,
    MAX_ADJUDICATIONS,
    STAGE,
    Candidate,
    ChapterWrites,
    Mention,
    Resolver,
    make_adjudicator,
    resolve_chapter,
)
from xn.schemas.extraction import ChapterExtraction, EntityEvent
from xn.schemas.lifecycle import LifecycleEventType
from xn.schemas.resolution import AdjudicationDecision
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository


@pytest.fixture
def db_conn(tmp_path: Path):
    r"""Provide an initialized SQLite database connection."""
    db_file = tmp_path / "test_s2.db"
    conn = connect(db_file)
    init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def repo(db_conn: sqlite3.Connection):
    r"""Provide a Repository instance backed by db_conn."""
    return Repository(db_conn)


class RecordingAdjudicator:
    r"""Test adjudicator recording calls and returning configured responses."""

    def __init__(
        self,
        default_response: bool = False,
        responses: dict[int, bool] | None = None,
    ) -> None:
        self.calls: list[tuple[Mention, Candidate]] = []
        self.default_response = default_response
        self.responses = responses or {}

    def __call__(self, mention: Mention, candidate: Candidate) -> bool:
        self.calls.append((mention, candidate))
        return self.responses.get(candidate.entity_id, self.default_response)


def make_adjudication_agent(
    model_func_or_bool: bool | Callable[..., Any] = True,
    reason: str = "Test reason",
) -> tuple[Agent, list[str]]:
    r"""Helper to construct a Pydantic-AI Agent backed by FunctionModel."""
    captured_prompts: list[str] = []

    if callable(model_func_or_bool):
        func = model_func_or_bool
    else:
        resp_bool = model_func_or_bool

        def func(messages: list[Any], info: Any) -> ModelResponse:
            prompt_str = ""
            for m in messages:
                for p in getattr(m, "parts", []):
                    c = getattr(p, "content", None)
                    if isinstance(c, str):
                        prompt_str = c
            captured_prompts.append(prompt_str)
            tool_name = info.output_tools[0].name
            payload = {"same_entity": resp_bool, "reason": reason}
            part = ToolCallPart(tool_name, payload)
            return ModelResponse(parts=[part], usage=RequestUsage(input_tokens=50, output_tokens=30))

    model = FunctionModel(func)
    agent = Agent(model, output_type=AdjudicationDecision, system_prompt="Adjudicator prompt")
    return agent, captured_prompts


def test_step_order_exact_hit_no_adjudicator_no_alias_write() -> None:
    r"""Verify exact hit makes no adjudicator call and no alias write, and second mention is exact hit."""
    adj = RecordingAdjudicator(default_response=False)
    resolver = Resolver(first_entity_id=1, adjudicate=adj)

    eid1 = resolver.resolve(name="Hàn Lập", category="character", chapter_id=1, context="Hàn Lập xuất hiện")
    assert eid1 == 1
    assert len(adj.calls) == 0

    eid2 = resolver.resolve(name="Hàn Lập", category="character", chapter_id=1, context="Hàn Lập lại xuất hiện")
    assert eid2 == 1
    assert len(adj.calls) == 0

    writes = resolver.take_writes()
    assert len(writes.entities) == 1
    assert writes.entities[0]["entity_id"] == 1
    assert writes.entities[0]["canonical_name"] == "Hàn Lập"
    assert writes.entities[0]["mention_count"] == 2
    assert writes.entities[0]["first_chapter"] == 1
    assert writes.entities[0]["last_chapter"] == 1

    assert len(writes.aliases) == 1
    assert writes.aliases[0]["alias_norm"] == "hàn lập"
    assert writes.aliases[0]["source"] == "exact"
    assert writes.aliases[0]["first_chapter"] == 1
    assert len(writes.entity_updates) == 0


def test_diacritics_stripped_joins_with_fuzzy_source_and_handles_d() -> None:
    r"""Verify hòa/hoà and Hàn Lập/Hàn Lạp/Han Lap join by diacritics with source fuzzy, and đ is handled."""
    adj = RecordingAdjudicator(default_response=False)
    resolver = Resolver(first_entity_id=1, adjudicate=adj)

    e_hoa1 = resolver.resolve(name="hòa", category="character", chapter_id=1, context="ctx hòa")
    assert e_hoa1 == 1
    e_hoa2 = resolver.resolve(name="hoà", category="character", chapter_id=1, context="ctx hoà")
    assert e_hoa2 == 1
    assert len(adj.calls) == 0

    e_han1 = resolver.resolve(name="Hàn Lập", category="character", chapter_id=1, context="ctx 1")
    assert e_han1 == 2
    e_han2 = resolver.resolve(name="Hàn Lạp", category="character", chapter_id=1, context="ctx 2")
    assert e_han2 == 2
    e_han3 = resolver.resolve(name="Han Lap", category="character", chapter_id=1, context="ctx 3")
    assert e_han3 == 2
    assert len(adj.calls) == 0

    e_doan1 = resolver.resolve(name="Đoàn Dự", category="character", chapter_id=1, context="ctx 4")
    assert e_doan1 == 3
    e_doan2 = resolver.resolve(name="Doan Du", category="character", chapter_id=1, context="ctx 5")
    assert e_doan2 == 3
    assert len(adj.calls) == 0

    writes = resolver.take_writes()
    fuzzy_aliases = [a for a in writes.aliases if a["source"] == "fuzzy"]
    fuzzy_norms = {a["alias_norm"] for a in fuzzy_aliases}
    assert "hoà" in fuzzy_norms
    assert "hàn lạp" in fuzzy_norms
    assert "han lap" in fuzzy_norms
    assert "doan du" in fuzzy_norms


def test_diacritics_stripped_tie_goes_to_adjudication_not_automerged() -> None:
    r"""Verify two entities tied at stripped diacritics step go to adjudication and are not auto-merged."""
    adj = RecordingAdjudicator(default_response=False)
    resolver = Resolver(first_entity_id=1, adjudicate=adj)

    resolver.load_alias(alias_norm="hàn lập", entity_id=1, category="character")
    resolver.load_alias(alias_norm="hàn lạp", entity_id=2, category="character")

    eid = resolver.resolve(name="Han Lap", category="character", chapter_id=1, context="ctx")

    assert eid == 3
    assert len(adj.calls) == 2
    cand_ids = [c[1].entity_id for c in adj.calls]
    assert set(cand_ids) == {1, 2}

    adj2 = RecordingAdjudicator(responses={2: True})
    resolver2 = Resolver(first_entity_id=1, adjudicate=adj2)
    resolver2.load_alias(alias_norm="hàn lập", entity_id=1, category="character")
    resolver2.load_alias(alias_norm="hàn lạp", entity_id=2, category="character")

    eid2 = resolver2.resolve(name="Han Lap", category="character", chapter_id=1, context="ctx")
    assert eid2 == 2
    writes2 = resolver2.take_writes()
    llm_alias = next(a for a in writes2.aliases if a["alias_norm"] == "han lap")
    assert llm_alias["source"] == "llm"
    assert llm_alias["entity_id"] == 2


def test_fuzzy_score_thresholds_monkeypatched(monkeypatch: pytest.MonkeyPatch) -> None:
    r"""Verify monkeypatched _fuzzy_score behavior across all threshold boundaries."""
    import xn.pipeline.s2_resolve as s2_mod

    # 1. 92 with equal tokens auto-merges with source 'fuzzy'
    adj_never_called = RecordingAdjudicator(default_response=False)
    r1 = Resolver(first_entity_id=1, adjudicate=adj_never_called)
    r1.load_alias(alias_norm="nam cung uyển", entity_id=1, category="character")
    monkeypatch.setattr(s2_mod, "_fuzzy_score", lambda a, b: 92.0)

    eid1 = r1.resolve(name="Uyển Cung Nam", category="character", chapter_id=1, context="ctx")
    assert eid1 == 1
    assert len(adj_never_called.calls) == 0
    w1 = r1.take_writes()
    assert any(a["alias_norm"] == "uyển cung nam" and a["source"] == "fuzzy" for a in w1.aliases)

    # 2. 92 with different tokens is adjudicated
    adj_called = RecordingAdjudicator(default_response=True)
    r2 = Resolver(first_entity_id=1, adjudicate=adj_called)
    r2.load_alias(alias_norm="trưởng lão", entity_id=1, category="character")
    monkeypatch.setattr(s2_mod, "_fuzzy_score", lambda a, b: 92.0)

    eid2 = r2.resolve(name="Mặc Trưởng Lão", category="character", chapter_id=1, context="ctx")
    assert eid2 == 1
    assert len(adj_called.calls) == 1
    w2 = r2.take_writes()
    assert any(a["alias_norm"] == "mặc trưởng lão" and a["source"] == "llm" for a in w2.aliases)

    # 3. 91.9 is adjudicated
    adj_919 = RecordingAdjudicator(default_response=True)
    r3 = Resolver(first_entity_id=1, adjudicate=adj_919)
    r3.load_alias(alias_norm="nam cung uyển", entity_id=1, category="character")
    monkeypatch.setattr(s2_mod, "_fuzzy_score", lambda a, b: 91.9)

    eid3 = r3.resolve(name="Uyển Cung Nam", category="character", chapter_id=1, context="ctx")
    assert eid3 == 1
    assert len(adj_919.calls) == 1

    # 4. 80.0 is adjudicated
    adj_80 = RecordingAdjudicator(default_response=True)
    r4 = Resolver(first_entity_id=1, adjudicate=adj_80)
    r4.load_alias(alias_norm="nam cung uyển", entity_id=1, category="character")
    monkeypatch.setattr(s2_mod, "_fuzzy_score", lambda a, b: 80.0)

    eid4 = r4.resolve(name="Uyển Nhi", category="character", chapter_id=1, context="ctx")
    assert eid4 == 1
    assert len(adj_80.calls) == 1

    # 5. 79.9 is a new entity with no call
    adj_799 = RecordingAdjudicator(default_response=True)
    r5 = Resolver(first_entity_id=1, adjudicate=adj_799)
    r5.load_alias(alias_norm="nam cung uyển", entity_id=1, category="character")
    monkeypatch.setattr(s2_mod, "_fuzzy_score", lambda a, b: 79.9)

    eid5 = r5.resolve(name="Uyển Nhi", category="character", chapter_id=1, context="ctx")
    assert eid5 == 2
    assert len(adj_799.calls) == 0
    w5 = r5.take_writes()
    assert any(a["alias_norm"] == "uyển nhi" and a["source"] == "exact" for a in w5.aliases)

    # 6. Two entities at 95 are both adjudicated
    adj_two95 = RecordingAdjudicator(default_response=False)
    r6 = Resolver(first_entity_id=1, adjudicate=adj_two95)
    r6.load_alias(alias_norm="thanh kiếm a", entity_id=1, category="weapon")
    r6.load_alias(alias_norm="thanh kiếm b", entity_id=2, category="weapon")
    monkeypatch.setattr(s2_mod, "_fuzzy_score", lambda a, b: 95.0)

    eid6 = r6.resolve(name="thanh kiếm c", category="weapon", chapter_id=1, context="ctx")
    assert eid6 == 3
    assert len(adj_two95.calls) == 2


def test_real_scorer_truong_lao_and_kiem_never_automerge() -> None:
    r"""Verify real scorer on Trưởng Lão / Mặc Trưởng Lão and Sword variants never auto-merges."""
    adj = RecordingAdjudicator(default_response=False)
    resolver = Resolver(first_entity_id=1, adjudicate=adj)

    # Trưởng Lão vs Mặc Trưởng Lão
    e1 = resolver.resolve(name="Trưởng Lão", category="character", chapter_id=1, context="ctx 1")
    assert e1 == 1
    e2 = resolver.resolve(name="Mặc Trưởng Lão", category="character", chapter_id=1, context="ctx 2")
    assert e2 == 2
    assert len(adj.calls) == 1

    adj.calls.clear()

    # Huyền Thiên Trảm Linh Kiếm vs Huyền Thiên Trảm Ma Kiếm
    k1 = resolver.resolve(
        name="Huyền Thiên Trảm Linh Kiếm", category="weapon", chapter_id=1, context="ctx sword 1"
    )
    assert k1 == 3
    k2 = resolver.resolve(
        name="Huyền Thiên Trảm Ma Kiếm", category="weapon", chapter_id=1, context="ctx sword 2"
    )
    assert k2 == 4
    assert len(adj.calls) == 1


def test_adjudicator_outcomes_true_llm_false_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    r"""Verify adjudicator True gives source llm; False gives new entity with source exact."""
    import xn.pipeline.s2_resolve as s2_mod
    monkeypatch.setattr(s2_mod, "_fuzzy_score", lambda a, b: 85.0)

    # True case -> source "llm"
    adj_true = RecordingAdjudicator(default_response=True)
    r1 = Resolver(first_entity_id=1, adjudicate=adj_true)
    r1.load_alias(alias_norm="bạch y tu sĩ", entity_id=1, category="character")
    eid_true = r1.resolve(name="hắc y tu sĩ", category="character", chapter_id=1, context="ctx")
    assert eid_true == 1
    w1 = r1.take_writes()
    assert any(a["alias_norm"] == "hắc y tu sĩ" and a["source"] == "llm" for a in w1.aliases)

    # False case -> source "exact"
    adj_false = RecordingAdjudicator(default_response=False)
    r2 = Resolver(first_entity_id=1, adjudicate=adj_false)
    r2.load_alias(alias_norm="bạch y tu sĩ", entity_id=1, category="character")
    eid_false = r2.resolve(name="hắc y tu sĩ", category="character", chapter_id=1, context="ctx")
    assert eid_false == 2
    w2 = r2.take_writes()
    assert any(a["alias_norm"] == "hắc y tu sĩ" and a["source"] == "exact" for a in w2.aliases)


def test_candidate_limit_at_most_3_ordered_and_stops_at_first_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r"""Verify name scoring >= 80 against 5 entities makes at most 3 calls, highest score first, stops at first True."""
    import xn.pipeline.s2_resolve as s2_mod

    score_map = {
        "ent1": 81.0,
        "ent2": 85.0,
        "ent3": 90.0,
        "ent4": 83.0,
        "ent5": 88.0,
    }

    def fake_score(a: str, b: str) -> float:
        return score_map.get(b, 50.0)

    monkeypatch.setattr(s2_mod, "_fuzzy_score", fake_score)

    # Case A: False for all -> should make exactly 3 calls (ent3: 90, ent5: 88, ent2: 85)
    adj_all_false = RecordingAdjudicator(default_response=False)
    r_a = Resolver(first_entity_id=1, adjudicate=adj_all_false)
    for i in range(1, 6):
        r_a.load_alias(alias_norm=f"ent{i}", entity_id=i, category="character")

    eid_a = r_a.resolve(name="target_name", category="character", chapter_id=1, context="ctx")
    assert eid_a == 6
    assert len(adj_all_false.calls) == 3
    called_eids = [c[1].entity_id for c in adj_all_false.calls]
    assert called_eids == [3, 5, 2]

    # Case B: True on second candidate (ent 5) -> should make 2 calls and stop
    adj_true_on_5 = RecordingAdjudicator(responses={5: True})
    r_b = Resolver(first_entity_id=1, adjudicate=adj_true_on_5)
    for i in range(1, 6):
        r_b.load_alias(alias_norm=f"ent{i}", entity_id=i, category="character")

    eid_b = r_b.resolve(name="target_name", category="character", chapter_id=1, context="ctx")
    assert eid_b == 5
    assert len(adj_true_on_5.calls) == 2
    assert [c[1].entity_id for c in adj_true_on_5.calls] == [3, 5]


def test_categories_never_cross() -> None:
    r"""Verify categories never cross at any step: same name in two categories gives two entities."""
    adj = RecordingAdjudicator(default_response=False)
    resolver = Resolver(first_entity_id=1, adjudicate=adj)

    e_char = resolver.resolve(name="Kim Tủy Thảo", category="character", chapter_id=1, context="ctx")
    e_med = resolver.resolve(name="Kim Tủy Thảo", category="medicine", chapter_id=1, context="ctx")

    assert e_char == 1
    assert e_med == 2
    assert resolver.lookup(name="Kim Tủy Thảo", category="character") == 1
    assert resolver.lookup(name="Kim Tủy Thảo", category="medicine") == 2
    assert len(adj.calls) == 0


def test_reveal_event_links_without_sharing_aliases() -> None:
    r"""Verify reveal event creates one link with right chapter, and neither entity gains the alias of the other."""
    adj = RecordingAdjudicator(default_response=False)
    resolver = Resolver(first_entity_id=1, adjudicate=adj)

    ev = EntityEvent(
        name="Lệ Phi Vũ",
        category="character",
        event_type=LifecycleEventType.REVEALED_IDENTITY,
        same_as="Hàn Lập",
        description="Thân phận thực sự tiết lộ",
        evidence="Hắn chính là Hàn Lập",
    )
    extraction = ChapterExtraction(
        chapter_id=2,
        map_location="Thất Huyền Môn",
        location_change=False,
        realm_breakthrough=False,
        major_conflict=False,
        time_skip_years=0.0,
        micro_summary="A" * 200,
        events=[ev],
    )

    writes = resolve_chapter(resolver, extraction, categories=["character"], identity_links=[])

    assert len(writes.links) == 1
    link = writes.links[0]
    assert link["entity_a"] == 1
    assert link["entity_b"] == 2
    assert link["revealed_chapter"] == 2
    assert link["evidence"] == "Hắn chính là Hàn Lập"

    assert resolver.lookup(name="Lệ Phi Vũ", category="character") == 1
    assert resolver.lookup(name="Hàn Lập", category="character") == 2


def test_reveal_fuzzy_same_as_stays_two_entities_due_to_exclude(monkeypatch: pytest.MonkeyPatch) -> None:
    r"""Verify reveal whose same_as is fuzzily close to name stays two entities due to exclude."""
    import xn.pipeline.s2_resolve as s2_mod
    monkeypatch.setattr(s2_mod, "_fuzzy_score", lambda a, b: 90.0)

    adj = RecordingAdjudicator(default_response=True)
    resolver = Resolver(first_entity_id=1, adjudicate=adj)

    ev = EntityEvent(
        name="Hàn Lập",
        category="character",
        event_type=LifecycleEventType.REVEALED_IDENTITY,
        same_as="Hàn Lạp Phong",
        description="Tiết lộ danh tính bí mật",
        evidence="Thực chất là Hàn Lạp Phong",
    )
    extraction = ChapterExtraction(
        chapter_id=1,
        map_location="Thất Huyền Môn",
        location_change=False,
        realm_breakthrough=False,
        major_conflict=False,
        time_skip_years=0.0,
        micro_summary="B" * 200,
        events=[ev],
    )

    writes = resolve_chapter(resolver, extraction, categories=["character"], identity_links=[])

    assert len(adj.calls) == 0
    assert len(writes.entities) == 2
    assert len(writes.links) == 1
    assert writes.links[0]["entity_a"] == 1
    assert writes.links[0]["entity_b"] == 2


def test_same_as_exact_alias_of_own_entity_gives_no_link_and_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    r"""Verify same_as that is an exact alias of the own entity of name gives no link and a warning."""
    adj = RecordingAdjudicator(default_response=False)
    resolver = Resolver(first_entity_id=1, adjudicate=adj)

    resolver.load_alias(alias_norm="hàn lập", entity_id=1, category="character")
    resolver.load_alias(alias_norm="lập ca", entity_id=1, category="character")

    ev = EntityEvent(
        name="Hàn Lập",
        category="character",
        event_type=LifecycleEventType.REVEALED_IDENTITY,
        same_as="Lập ca",
        description="Lập ca chính là Hàn Lập",
        evidence="Hắn chính là Lập ca",
    )
    extraction = ChapterExtraction(
        chapter_id=1,
        map_location="Thất Huyền Môn",
        location_change=False,
        realm_breakthrough=False,
        major_conflict=False,
        time_skip_years=0.0,
        micro_summary="C" * 200,
        events=[ev],
    )

    with caplog.at_level(logging.WARNING):
        writes = resolve_chapter(resolver, extraction, categories=["character"], identity_links=[])

    assert len(writes.links) == 0
    assert "entity IDs are equal" in caplog.text


def test_same_as_on_non_reveal_event_gives_no_link_no_entity_and_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    r"""Verify same_as on non-reveal event gives no link, no entity and a warning."""
    adj = RecordingAdjudicator(default_response=False)
    resolver = Resolver(first_entity_id=1, adjudicate=adj)

    ev = EntityEvent(
        name="Bình nhỏ",
        category="weapon",
        event_type=LifecycleEventType.ACQUISITION,
        same_as="Chưởng Thiên Bình",
        description="Nhặt được bình nhỏ",
        evidence="Nhặt được bình nhỏ bí ẩn",
    )
    extraction = ChapterExtraction(
        chapter_id=1,
        map_location="Thất Huyền Môn",
        location_change=False,
        realm_breakthrough=False,
        major_conflict=False,
        time_skip_years=0.0,
        micro_summary="D" * 200,
        events=[ev],
    )

    with caplog.at_level(logging.WARNING):
        writes = resolve_chapter(resolver, extraction, categories=["weapon"], identity_links=[])

    assert len(writes.links) == 0
    assert len(writes.entities) == 1
    assert writes.entities[0]["canonical_name"] == "Bình nhỏ"
    assert resolver.lookup(name="Chưởng Thiên Bình", category="weapon") is None
    assert "non-reveal event type" in caplog.text


def test_holder_resolves_as_character_and_ignored_when_character_not_in_categories() -> None:
    r"""Verify holder resolves as character, and is ignored when character is not a category."""
    adj = RecordingAdjudicator(default_response=False)

    ev1 = EntityEvent(
        name="Trúc Phong Kiếm",
        category="weapon",
        event_type=LifecycleEventType.ACQUISITION,
        holder="Hàn Lập",
        description="Hàn Lập đoạt kiếm",
        evidence="đoạt được Trúc Phong Kiếm",
    )
    ext1 = ChapterExtraction(
        chapter_id=1,
        map_location="Thất Huyền Môn",
        location_change=False,
        realm_breakthrough=False,
        major_conflict=False,
        time_skip_years=0.0,
        micro_summary="E" * 200,
        events=[ev1],
    )

    # 1. character in categories
    r1 = Resolver(first_entity_id=1, adjudicate=adj)
    w1 = resolve_chapter(r1, ext1, categories=["weapon", "character"], identity_links=[])
    assert len(w1.entities) == 2
    assert r1.lookup(name="Trúc Phong Kiếm", category="weapon") == 1
    assert r1.lookup(name="Hàn Lập", category="character") == 2

    # 2. character NOT in categories
    r2 = Resolver(first_entity_id=1, adjudicate=adj)
    w2 = resolve_chapter(r2, ext1, categories=["weapon"], identity_links=[])
    assert len(w2.entities) == 1
    assert r2.lookup(name="Trúc Phong Kiếm", category="weapon") == 1
    assert r2.lookup(name="Hàn Lập", category="character") is None


def test_seeds_behavior_and_binding_restoration() -> None:
    r"""Verify seeds behavior: unused produce no alias, first mention creates entity, later mention adds alias, load_alias restores binding."""
    seed_aliases = [
        {"category": "character", "names": ["Hàn Lập", "Hàn Sư Huynh", "Lập Ma"]}
    ]
    adj = RecordingAdjudicator(default_response=False)
    r1 = Resolver(first_entity_id=1, seed_aliases=seed_aliases, adjudicate=adj)

    # First mention by 'Hàn Lập' creates entity 1 with source seed
    eid1 = r1.resolve(name="Hàn Lập", category="character", chapter_id=1, context="ctx 1")
    assert eid1 == 1

    w1 = r1.take_writes()
    assert len(w1.entities) == 1
    assert w1.entities[0]["canonical_name"] == "Hàn Lập"
    assert len(w1.aliases) == 1
    assert w1.aliases[0]["alias_norm"] == "hàn lập"
    assert w1.aliases[0]["source"] == "seed"
    assert w1.aliases[0]["first_chapter"] == 1

    # Later mention by 'Hàn Sư Huynh' in chapter 2 joins Entity 1
    eid2 = r1.resolve(name="Hàn Sư Huynh", category="character", chapter_id=2, context="ctx 2")
    assert eid2 == 1
    w2 = r1.take_writes()
    assert len(w2.entities) == 0
    assert len(w2.aliases) == 1
    assert w2.aliases[0]["alias_norm"] == "hàn sư huynh"
    assert w2.aliases[0]["first_chapter"] == 2
    assert w2.aliases[0]["source"] == "seed"

    # New resolver restores binding via load_alias
    r2 = Resolver(first_entity_id=10, seed_aliases=seed_aliases, adjudicate=adj)
    r2.load_alias(alias_norm="hàn lập", entity_id=1, category="character")
    eid3 = r2.resolve(name="Lập Ma", category="character", chapter_id=3, context="ctx 3")
    assert eid3 == 1
    w3 = r2.take_writes()
    assert len(w3.aliases) == 1
    assert w3.aliases[0]["alias_norm"] == "lập ma"
    assert w3.aliases[0]["first_chapter"] == 3
    assert w3.aliases[0]["source"] == "seed"


def test_manual_identity_links(caplog: pytest.LogCaptureFixture) -> None:
    r"""Verify manual identity_links apply only in revealed_chapter, and missing name logs warning and gives no link."""
    identity_links = [
        {
            "name_a": "Hàn Lập",
            "name_b": "Lệ Phi Vũ",
            "category": "character",
            "revealed_chapter": 2,
            "evidence": "Chương 2 tiết lộ thủ đoạn",
        }
    ]
    adj = RecordingAdjudicator(default_response=False)
    resolver = Resolver(first_entity_id=1, adjudicate=adj)

    ev_hl = EntityEvent(
        name="Hàn Lập",
        category="character",
        event_type=LifecycleEventType.MENTIONED,
        description="Hàn Lập xuất hiện",
        evidence="Hàn Lập xuất hiện",
    )
    ext1 = ChapterExtraction(
        chapter_id=1,
        map_location="Thất Huyền Môn",
        location_change=False,
        realm_breakthrough=False,
        major_conflict=False,
        time_skip_years=0.0,
        micro_summary="F" * 200,
        events=[ev_hl],
    )
    w1 = resolve_chapter(resolver, ext1, categories=["character"], identity_links=identity_links)
    assert len(w1.links) == 0

    with caplog.at_level(logging.WARNING):
        w2 = resolve_chapter(
            resolver,
            ext1.model_copy(update={"chapter_id": 2}),
            categories=["character"],
            identity_links=identity_links,
        )
    assert len(w2.links) == 0
    assert "identity link lookup failed" in caplog.text

    ev_lpv = EntityEvent(
        name="Lệ Phi Vũ",
        category="character",
        event_type=LifecycleEventType.MENTIONED,
        description="Lệ Phi Vũ xuất hiện",
        evidence="Lệ Phi Vũ xuất hiện",
    )
    ext2 = ChapterExtraction(
        chapter_id=2,
        map_location="Thất Huyền Môn",
        location_change=False,
        realm_breakthrough=False,
        major_conflict=False,
        time_skip_years=0.0,
        micro_summary="G" * 200,
        events=[ev_hl, ev_lpv],
    )
    w3 = resolve_chapter(resolver, ext2, categories=["character"], identity_links=identity_links)
    assert len(w3.links) == 1
    assert w3.links[0]["entity_a"] == 1
    assert w3.links[0]["entity_b"] == 2
    assert w3.links[0]["revealed_chapter"] == 2


def test_two_chapters_writes_and_sqlite_save_resolution(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""Verify mention counts and ChapterWrites shapes across two chapters and Repository.save_resolution persistence."""
    repo.add_novel(
        novel_id="pntt1",
        title="Phàm Nhân Tu Tiên",
        genre_profile="xianxia",
        total_chapters=10,
    )

    adj = RecordingAdjudicator(default_response=False)
    resolver = Resolver(first_entity_id=1, adjudicate=adj)

    # Chapter 1: Hàn Lập mentioned 2 times
    resolver.resolve(name="Hàn Lập", category="character", chapter_id=1, context="Hàn Lập 1")
    resolver.resolve(name="Hàn Lập", category="character", chapter_id=1, context="Hàn Lập 2")

    writes1 = resolver.take_writes()
    assert len(writes1.entities) == 1
    assert writes1.entities[0]["entity_id"] == 1
    assert writes1.entities[0]["mention_count"] == 2
    assert writes1.entities[0]["first_chapter"] == 1
    assert writes1.entities[0]["last_chapter"] == 1
    assert len(writes1.entity_updates) == 0

    repo.save_resolution(
        novel_id="pntt1",
        chapter_id=1,
        entities=writes1.entities,
        entity_updates=writes1.entity_updates,
        aliases=writes1.aliases,
        links=writes1.links,
        stage="s2",
        input_hash="hash1",
        prompt_version="v1",
        model="qwen",
    )

    # Chapter 2: Hàn Lập mentioned 3 times, Lệ Phi Vũ mentioned 1 time
    resolver.resolve(name="Hàn Lập", category="character", chapter_id=2, context="Hàn Lập 3")
    resolver.resolve(name="Hàn Lập", category="character", chapter_id=2, context="Hàn Lập 4")
    resolver.resolve(name="Hàn Lập", category="character", chapter_id=2, context="Hàn Lập 5")
    resolver.resolve(name="Lệ Phi Vũ", category="character", chapter_id=2, context="Lệ Phi Vũ 1")

    writes2 = resolver.take_writes()
    assert len(writes2.entities) == 1
    assert writes2.entities[0]["entity_id"] == 2
    assert writes2.entities[0]["canonical_name"] == "Lệ Phi Vũ"
    assert writes2.entities[0]["mention_count"] == 1
    assert writes2.entities[0]["first_chapter"] == 2
    assert writes2.entities[0]["last_chapter"] == 2

    assert len(writes2.entity_updates) == 1
    assert writes2.entity_updates[0]["entity_id"] == 1
    assert writes2.entity_updates[0]["mention_delta"] == 3
    assert writes2.entity_updates[0]["last_chapter"] == 2

    repo.save_resolution(
        novel_id="pntt1",
        chapter_id=2,
        entities=writes2.entities,
        entity_updates=writes2.entity_updates,
        aliases=writes2.aliases,
        links=writes2.links,
        stage="s2",
        input_hash="hash2",
        prompt_version="v1",
        model="qwen",
    )

    cur = db_conn.execute("SELECT * FROM entities WHERE novel_id = 'pntt1' ORDER BY entity_id")
    rows = cur.fetchall()
    assert len(rows) == 2
    assert rows[0]["entity_id"] == 1
    assert rows[0]["mention_count"] == 5
    assert rows[0]["first_chapter"] == 1
    assert rows[0]["last_chapter"] == 2

    assert rows[1]["entity_id"] == 2
    assert rows[1]["mention_count"] == 1
    assert rows[1]["first_chapter"] == 2
    assert rows[1]["last_chapter"] == 2


def test_make_adjudicator_llm_runs_tags_and_failure(
    repo: Repository, db_conn: sqlite3.Connection
) -> None:
    r"""Verify make_adjudicator writes llm_runs row with stage s2, strips tags, and records ok=0 on error."""
    repo.add_novel(
        novel_id="pntt1",
        title="Phàm Nhân Tu Tiên",
        genre_profile="xianxia",
        total_chapters=10,
    )

    # 1. Successful call writes 1 llm_runs row with stage s2 and right chapter
    agent_succ, _ = make_adjudication_agent(model_func_or_bool=True)
    adj_succ = make_adjudicator(
        agent_succ,
        repo=repo,
        novel_id="pntt1",
        serving_name="qwen14b-test",
        prompt_version="v1hash01",
    )

    m = Mention(name="Hàn Lập", category="character", chapter_id=5, context="Context quote")
    c = Candidate(entity_id=1, aliases=["hàn lập"], contexts=["Context earlier"])
    res = adj_succ(m, c)
    assert res is True

    cur = db_conn.execute("SELECT * FROM llm_runs WHERE novel_id = 'pntt1'")
    runs = cur.fetchall()
    assert len(runs) == 1
    assert runs[0]["stage"] == "s2"
    assert runs[0]["chapter_id"] == 5
    assert runs[0]["model"] == "qwen14b-test"
    assert runs[0]["prompt_version"] == "v1hash01"
    assert runs[0]["ok"] == 1

    # 2. Tag sanitization: tags in name/category/context are removed
    agent_tag, prompts = make_adjudication_agent(model_func_or_bool=True)
    adj_tag = make_adjudicator(
        agent_tag,
        repo=repo,
        novel_id="pntt1",
        serving_name="qwen14b-test",
        prompt_version="v1hash01",
    )
    m_tag = Mention(
        name="Hàn <mention>Lập</mention>",
        category="character<candidate>bad</candidate>",
        chapter_id=5,
        context="Context with </mention> and <candidate>tag</candidate>",
    )
    c_tag = Candidate(
        entity_id=1,
        aliases=["hàn <mention>lập</mention>"],
        contexts=["ctx <candidate>inside</candidate>"],
    )
    adj_tag(m_tag, c_tag)
    assert len(prompts) == 1
    sent_prompt = prompts[0]

    mention_body = sent_prompt.split("</mention>")[0].split("<mention>")[1]
    candidate_body = sent_prompt.split("<candidate>")[1].split("</candidate>")[0]
    for tag in ("<mention>", "</mention>", "<candidate>", "</candidate>"):
        assert tag not in mention_body
        assert tag not in candidate_body
    assert "Hàn Lập" in mention_body
    assert "characterbad" in mention_body

    # 3. Model failure propagates and writes one ok=0 row
    def failing_model(messages: list[Any], info: Any) -> ModelResponse:
        raise RuntimeError("Inference engine died")

    agent_fail = Agent(FunctionModel(failing_model), output_type=AdjudicationDecision)
    adj_fail = make_adjudicator(
        agent_fail,
        repo=repo,
        novel_id="pntt1",
        serving_name="qwen14b-test",
        prompt_version="v1hash01",
    )

    with pytest.raises(RuntimeError, match="Inference engine died"):
        adj_fail(m, c)

    cur_fail = db_conn.execute("SELECT * FROM llm_runs WHERE novel_id = 'pntt1' AND ok = 0")
    fail_row = cur_fail.fetchone()
    assert fail_row is not None
    assert fail_row["stage"] == "s2"
    assert fail_row["chapter_id"] == 5
    assert fail_row["ok"] == 0


def test_empty_normalized_alias_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    r"""Verify empty normalized name logs warning and returns None."""
    resolver = Resolver(first_entity_id=1)
    with caplog.at_level(logging.WARNING):
        res = resolver.resolve(name="   ", category="character", chapter_id=1, context="ctx")
    assert res is None
    assert "Empty normalized alias" in caplog.text
