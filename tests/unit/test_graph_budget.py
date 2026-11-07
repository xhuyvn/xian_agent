"""G-28: timeline and deep-dive evidence under the 20k cap, and no text from after N."""

from __future__ import annotations

from pathlib import Path
import re
import sqlite3
from typing import Any, Optional

import pytest

from xn.graph.budget import Unit, fit, split_text
from xn.graph.deps import GraphDeps
from xn.graph.nodes.gate import gate
from xn.graph.nodes.retrieve import EVENTS_PER_ENTITY, MIN_KEPT_EVENTS, retrieve
from xn.graph.nodes.spoiler_scan import evidence_words
from xn.graph.nodes.synthesize import compact_items
from xn.graph.nodes.verify import _strip_invalid
from xn.llm import MAX_CHUNKS, MAX_EVIDENCE_TOKENS, MAX_ITEM_TOKENS, validate_compact
from xn.schemas.arc import StoryArc
from xn.schemas.state import ChunkEvidence, EvidencePack, RouterDecision, SummaryEvidence
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository
from xn.text import estimate_tokens

REPO_ROOT = Path(__file__).resolve().parents[2]
MARK = re.compile(r"#c(\d+)")
HAN_LAP, OTHER = 1, 2


# --- pure packer ---


def test_split_text_keeps_the_header_on_every_piece_within_the_limit() -> None:
    body = " ".join(f"Câu số {i} nói về việc tu luyện của nhân vật chính." for i in range(80))
    pieces = split_text("Mạch 3, chương 65–96: ", body, MAX_ITEM_TOKENS)
    assert len(pieces) > 1
    assert all(p.startswith("Mạch 3, chương 65–96: (phần ") for p in pieces)
    assert all(estimate_tokens(p) <= MAX_ITEM_TOKENS for p in pieces)
    assert all(f"Câu số {i} " in "".join(pieces) for i in range(80))


def test_split_text_keeps_a_short_text_whole_and_cuts_an_overlong_sentence() -> None:
    assert split_text("H: ", "Một câu.", MAX_ITEM_TOKENS) == ["H: Một câu."]
    pieces = split_text("H: ", "x" * 5000, MAX_ITEM_TOKENS)
    assert all(estimate_tokens(p) <= MAX_ITEM_TOKENS for p in pieces)
    assert sum(p.count("x") for p in pieces) == 5000


def unit(name: str, tokens: int, fallback: Optional[Unit] = None) -> Unit:
    return Unit((name,), tokens, fallback)


def test_fit_leaves_a_pack_under_budget_alone() -> None:
    tiers = [[unit("a", 5)], [unit("b", 5, fallback=unit("b-", 1))]]
    assert fit(tiers, 10) == [list(t) for t in tiers]


def test_fit_drops_earlier_tiers_worst_first_and_shrinks_before_dropping() -> None:
    old = unit("old", 10, fallback=unit("old-", 2))
    new = unit("new", 10, fallback=unit("new-", 2))
    tiers = [[unit("e1", 3), unit("e2", 3)], [unit("c1", 4)], [new, old]]
    assert fit(tiers, 27) == [[unit("e1", 3)], [unit("c1", 4)], [new, old]]
    assert fit(tiers, 20) == [[], [], [new, old]]
    assert fit(tiers, 12) == [[], [], [new, old.fallback]]
    assert fit(tiers, 4) == [[], [], [new.fallback, old.fallback]]
    assert fit(tiers, 3) == [[], [], [new.fallback]]


# --- a novel with arcs, prefix summaries, digests and events ---


def sentences(n: int, text: str) -> str:
    return " ".join(f"Câu {i}: {text}." for i in range(n))


def build_novel(
    conn: sqlite3.Connection,
    spans: list[tuple[int, int]],
    *,
    summary_sentences: int = 80,
    resolution_sentences: int = 1,
    digest_sentences: int = 6,
) -> None:
    """Seed arcs with payloads, prefix rows at each multiple of 10, micro-summaries and digests.

    Every text carries `#c<n>`, the last chapter it draws on, so a leak test can read it back.
    """
    repo = Repository(conn)
    last = spans[-1][1]
    repo.add_novel(novel_id="nov", title="N", genre_profile="xianxia", total_chapters=last)
    conn.execute(
        "INSERT INTO entities (novel_id, entity_id, canonical_name, category, first_chapter) VALUES "
        "('nov', 1, 'Hàn Lập', 'character', 1), ('nov', 2, 'Lệ Phi Vũ', 'character', 1)"
    )
    for arc_id, (start, end) in enumerate(spans, start=1):
        conn.executemany(
            "INSERT INTO chapters (novel_id, chapter_id, clean_text, sha256, arc_id) VALUES ('nov', ?, 't', 's', ?)",
            [(c, arc_id) for c in range(start, end + 1)],
        )
        conn.executemany(
            "INSERT INTO extractions (novel_id, chapter_id, payload, prompt_version, model) "
            "VALUES ('nov', ?, json_object('micro_summary', ?), 'v', 'm')",
            [(c, f"Tóm tắt chương {c} #c{c}") for c in range(start, end + 1)],
        )
        payload = StoryArc(
            arc_id=arc_id, start_chapter=start, end_chapter=end, title=f"Mạch thứ {arc_id}",
            primary_locations=[], core_conflict="x", climax_event="x", power_progression="x", key_entities=[],
            resolution=sentences(resolution_sentences, f"kết cục mạch {arc_id} #c{end}"),
            summary=sentences(summary_sentences, f"diễn biến mạch {arc_id} #c{end}"),
        )
        conn.execute(
            "INSERT INTO arcs (novel_id, arc_id, start_chapter, end_chapter, payload) VALUES ('nov', ?, ?, ?, ?)",
            (arc_id, start, end, payload.model_dump_json()),
        )
        checkpoints = [c for c in range(start + 1, end + 1) if c % 10 == 0]
        conn.executemany(
            "INSERT INTO arc_prefix_summaries (novel_id, arc_id, upto_chapter, summary) VALUES ('nov', ?, ?, ?)",
            [(arc_id, c, sentences(5, f"mạch {arc_id} đến #c{c}")) for c in checkpoints],
        )
        digests = [c for c in checkpoints if c < end] + [end]
        for entity_id in (HAN_LAP, OTHER):
            conn.executemany(
                "INSERT INTO entity_arc_digests (novel_id, entity_id, arc_id, upto_chapter, digest, event_count) "
                "VALUES ('nov', ?, ?, ?, ?, 3)",
                [(entity_id, arc_id, c, sentences(digest_sentences, f"thực thể {entity_id} #c{c}")) for c in digests],
            )
    conn.executemany(
        "INSERT INTO entity_events (novel_id, id, chapter_id, entity_id, event_type, ordinal, description) "
        "VALUES ('nov', ?, ?, ?, 'UPGRADE', 1, ?)",
        [(c * 10 + e, c, e, f"sự kiện #c{c}") for c in range(1, last + 1) for e in (HAN_LAP, OTHER)],
    )
    conn.commit()


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "budget.db")
    init_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


THREE_ARCS = [(1, 32), (33, 64), (65, 96)]


class Chunks:
    """Fake retriever: max_chunks chunks of the given size, all from chapter N."""

    last_timings: dict[str, float] = {}

    def __init__(self, tokens: int = 50) -> None:
        self.tokens = tokens

    def retrieve(self, *, chapter_max: int, entity_id: Optional[int] = None, top_k: Optional[int] = None, **_: Any) -> list[ChunkEvidence]:
        text = f"đoạn #c{chapter_max} " + "x" * int(self.tokens * 3.3 - 20)
        return [
            ChunkEvidence(chapter_id=chapter_max, kind="text", chunk_no=i, text=text, score=1 - i / 10, entity_id=entity_id)
            for i in range(top_k or MAX_CHUNKS)
        ]


def pack_at(
    conn: sqlite3.Connection, n: int, intent: str, entity_ids: list[int], chunk_tokens: int = 50
) -> EvidencePack:
    deps = GraphDeps(repo=Repository(conn), config={}, retriever_factory=lambda _c: Chunks(chunk_tokens))
    state = {
        "novel_id": "nov",
        "chapter_max": n,
        "query": "q",
        "entity_ids": entity_ids,
        "decision": RouterDecision(intent=intent),
    }
    return retrieve(state, deps)["evidence"]


def all_texts(pack: EvidencePack) -> list[str]:
    return [s.text for s in pack.summaries] + [i.cloud_text() for i in pack.items] + [c.text for c in pack.chunks]


def assert_bounded(pack: EvidencePack, n: int) -> None:
    """Nothing after N, every item fits the egress rule, and the stated tokens are right."""
    for text in all_texts(pack):
        assert all(int(m) <= n for m in MARK.findall(text)), (n, text[:80])
    validate_compact(compact_items(pack), n)
    assert pack.tokens == sum(estimate_tokens(t) for t in all_texts(pack)) <= MAX_EVIDENCE_TOKENS


def kinds(pack: EvidencePack, kind: str) -> list[SummaryEvidence]:
    return [s for s in pack.summaries if s.kind == kind]


# --- timeline ---


def test_timeline_uses_complete_arcs_and_the_snapped_prefix(conn: Any) -> None:
    build_novel(conn, THREE_ARCS)
    pack = pack_at(conn, 45, "timeline", [])
    arcs = kinds(pack, "arc_summary")
    assert {s.arc_id for s in arcs} == {1} and len(arcs) > 1
    assert all(s.chapter_id == 32 and s.text.startswith("Mạch 1 «Mạch thứ 1», chương 1–32") for s in arcs)
    [prefix] = kinds(pack, "arc_prefix")
    assert (prefix.arc_id, prefix.start_chapter, prefix.chapter_id) == (2, 33, 40)
    assert [s.chapter_id for s in kinds(pack, "micro_summary")] == [41, 42, 43, 44, 45]
    assert_bounded(pack, 45)


def test_timeline_at_an_arc_end_uses_its_full_summary_and_no_prefix(conn: Any) -> None:
    build_novel(conn, THREE_ARCS)
    pack = pack_at(conn, 64, "timeline", [])
    assert {s.arc_id for s in kinds(pack, "arc_summary")} == {1, 2}
    assert kinds(pack, "arc_prefix") == [] and kinds(pack, "micro_summary") == []


def test_timeline_before_the_first_checkpoint_uses_raw_micro_summaries(conn: Any) -> None:
    build_novel(conn, THREE_ARCS)
    pack = pack_at(conn, 35, "timeline", [])
    assert kinds(pack, "arc_prefix") == []
    assert [s.chapter_id for s in kinds(pack, "micro_summary")] == [33, 34, 35]


def test_timeline_without_an_entity_passes_the_gate(conn: Any) -> None:
    build_novel(conn, THREE_ARCS)
    deps = GraphDeps(repo=Repository(conn), config={})
    state = {"chapter_max": 45, "decision": RouterDecision(intent="timeline"), "entity_ids": []}
    assert gate({**state, "evidence": pack_at(conn, 45, "timeline", [])}, deps) == {}
    assert gate({**state, "evidence": EvidencePack()}, deps) != {}


@pytest.mark.parametrize("intent, entity_ids", [("timeline", []), ("timeline", [HAN_LAP]), ("deep_dive", [HAN_LAP]), ("compare", [HAN_LAP, OTHER])])
def test_no_pack_carries_anything_after_n(conn: Any, intent: str, entity_ids: list[int]) -> None:
    build_novel(conn, THREE_ARCS)
    for n in range(1, 97):
        assert_bounded(pack_at(conn, n, intent, entity_ids), n)


def test_timeline_trims_events_then_chunks_then_shrinks_the_oldest_arcs(conn: Any) -> None:
    build_novel(conn, [(c, c + 9) for c in range(1, 400, 10)])  # 40 arcs of 10 chapters
    pack = pack_at(conn, 395, "timeline", [HAN_LAP], chunk_tokens=400)
    assert_bounded(pack, 395)
    assert pack.items == [] and pack.chunks == []
    # Arc 40 is 391-400: no checkpoint before N yet, so the view is the raw micro-summaries.
    assert [s.chapter_id for s in kinds(pack, "micro_summary")] == list(range(391, 396))
    short = {s.arc_id for s in kinds(pack, "arc_summary") if "(tóm lược kết cục)" in s.text}
    full = {s.arc_id for s in kinds(pack, "arc_summary")} - short
    assert short and full and max(short) < min(full)
    assert short | full == set(range(1, 40))


def test_timeline_drops_the_oldest_short_forms_last(conn: Any) -> None:
    spans = [(c, c + 9) for c in range(1, 2500, 10)]  # 250 arcs, as at a large N
    build_novel(conn, spans, resolution_sentences=12)
    pack = pack_at(conn, 2495, "timeline", [HAN_LAP], chunk_tokens=400)
    assert_bounded(pack, 2495)
    assert pack.items == [] and pack.chunks == []
    assert [s.chapter_id for s in kinds(pack, "micro_summary")] == list(range(2491, 2496))
    kept = sorted({s.arc_id for s in kinds(pack, "arc_summary")})
    assert all("(tóm lược kết cục)" in s.text for s in kinds(pack, "arc_summary"))
    assert kept == list(range(kept[0], 250)) and kept[0] > 1


# --- deep_dive ---


@pytest.mark.parametrize(
    "n, expected",
    [
        (32, {1: 32}),  # at an arc end: its full digest
        (33, {1: 32}),  # arc 2 has no checkpoint yet
        (40, {1: 32, 2: 40}),  # at a checkpoint
        (59, {1: 32, 2: 50}),  # between checkpoints: the largest one <= N
        (63, {1: 32, 2: 60}),  # just before the end: the last prefix digest
        (64, {1: 32, 2: 64}),  # the end: the full digest replaces the prefixes
    ],
)
def test_deep_dive_takes_the_full_digest_or_the_largest_prefix_below_n(
    conn: Any, n: int, expected: dict[int, int]
) -> None:
    build_novel(conn, THREE_ARCS)
    pack = pack_at(conn, n, "deep_dive", [HAN_LAP])
    assert {s.arc_id: s.chapter_id for s in kinds(pack, "digest")} == expected
    assert len(kinds(pack, "digest")) == len(expected)
    assert len(pack.items) == min(n, EVENTS_PER_ENTITY) and len(pack.chunks) == MAX_CHUNKS
    assert_bounded(pack, n)


@pytest.mark.parametrize("n", [10, 500, 1234, 2499, 2500])
def test_protagonist_deep_dive_fits_the_budget_at_any_n(conn: Any, n: int) -> None:
    build_novel(conn, [(c, c + 9) for c in range(1, 2500, 10)], digest_sentences=14)
    pack = pack_at(conn, n, "deep_dive", [HAN_LAP], chunk_tokens=440)
    assert_bounded(pack, n)
    assert len(pack.items) >= MIN_KEPT_EVENTS


def test_deep_dive_trims_chunks_then_events_then_the_oldest_digests(conn: Any) -> None:
    build_novel(conn, [(c, c + 9) for c in range(1, 2500, 10)], digest_sentences=14)
    pack = pack_at(conn, 2500, "deep_dive", [HAN_LAP], chunk_tokens=440)
    assert pack.chunks == [] and len(pack.items) == MIN_KEPT_EVENTS
    arcs = sorted(s.arc_id for s in kinds(pack, "digest"))
    assert arcs == list(range(arcs[0], 251)) and arcs[0] > 1


def test_deep_dive_keeps_everything_when_it_fits(conn: Any) -> None:
    build_novel(conn, [(c, c + 9) for c in range(1, 600, 10)])  # 60 arcs
    pack = pack_at(conn, 600, "deep_dive", [HAN_LAP], chunk_tokens=440)
    assert len(kinds(pack, "digest")) == 60 and len(pack.items) == EVENTS_PER_ENTITY
    assert len(pack.chunks) == MAX_CHUNKS


def test_compare_gives_each_entity_half_the_budget(conn: Any) -> None:
    build_novel(conn, [(c, c + 9) for c in range(1, 2500, 10)], digest_sentences=14)
    pack = pack_at(conn, 2500, "compare", [HAN_LAP, OTHER], chunk_tokens=440)
    assert_bounded(pack, 2500)
    for entity_id in (HAN_LAP, OTHER):
        own = [s.text for s in pack.summaries if s.entity_id == entity_id]
        own += [i.cloud_text() for i in pack.items if i.entity_id == entity_id]
        own += [c.text for c in pack.chunks if c.entity_id == entity_id]
        assert sum(estimate_tokens(t) for t in own) <= MAX_EVIDENCE_TOKENS // 2
        assert sum(i.entity_id == entity_id for i in pack.items) >= MIN_KEPT_EVENTS


# --- synthesize, verify and the spoiler scan see the summaries ---


def test_summaries_reach_the_model_first_in_story_order(conn: Any) -> None:
    build_novel(conn, THREE_ARCS)
    items = compact_items(pack_at(conn, 45, "timeline", []))
    summary_kinds = [i.kind for i in items if i.kind != "chunk"]
    assert summary_kinds[0] == "arc_summary" and summary_kinds[-1] == "micro_summary"
    chapters = [i.chapter_id for i in items if i.kind != "chunk"]
    assert chapters == sorted(chapters)


def test_summary_chapters_are_valid_citations_and_their_words_are_evidence(conn: Any) -> None:
    build_novel(conn, THREE_ARCS)
    pack = pack_at(conn, 45, "timeline", [])
    allowed = {s.chapter_id for s in pack.summaries}
    assert _strip_invalid("Mạch một [chương 32]. Sau đó [chương 50].", allowed) == "Mạch một [chương 32]. Sau đó."
    assert " dien bien mach 1 " in evidence_words(pack)
