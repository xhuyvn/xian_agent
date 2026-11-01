"""Unit tests for gold asset loading, validation and scoring, using made-up data."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from pydantic_ai.models.test import TestModel
import pytest

from xn.gold import (
    GoldAliasPair,
    GoldChapter,
    GoldEntity,
    GoldQuestion,
    GoldSet,
    GoldSpoiler,
    evidence_state,
    gold_report,
    load_gold,
    name_key,
    resolver_merges,
    score_aliases,
    score_extraction,
    score_qa,
    score_spoilers,
    validate_gold,
)
from xn.graph.deps import GraphDeps
from xn.schemas.state import EvidenceItem, EvidencePack, RouterDecision
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "prompts"
CATEGORIES = ["character", "weapon_treasure"]
EVENT_TYPES = ["ACQUISITION", "MENTIONED"]
CONFIG: dict[str, Any] = {
    "novel_id": "pntt1",
    "categories": CATEGORIES,
    "event_types": {t: t for t in EVENT_TYPES},
    "models": {"local_extractor": {"serving_name": "fake-local", "output_mode": "tool"}},
}


def event(name: str, category: str = "character", event_type: str = "MENTIONED", **fields: Any) -> dict:
    return {"name": name, "category": category, "event_type": event_type, **fields}


def entity(name: str, *aliases: str, category: str = "character", types: tuple = ("MENTIONED",)) -> GoldEntity:
    return GoldEntity(name=name, category=category, aliases=list(aliases), event_types=list(types))


def chapter(*entities: GoldEntity, chapter_id: int = 1, optional: tuple = (), **flags: bool) -> GoldChapter:
    values = {"location_change": False, "realm_breakthrough": False, "major_conflict": False, **flags}
    return GoldChapter(chapter_id=chapter_id, entities=list(entities), optional=list(optional), **values)


def payload(*events: dict, **flags: bool) -> dict:
    return {"events": list(events), **flags}


def evidence(chapter_id: int, description: str = "việc", quote: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        event_id=chapter_id, chapter_id=chapter_id, entity_id=1, entity_name="Hàn Lập",
        event_type="MENTIONED", description=description, evidence=quote,
    )


def qa(answer_chapters: list[int], item_id: str = "q1") -> GoldQuestion:
    return GoldQuestion(
        id=item_id, question="?", chapter_max=50, answer="a", answer_chapters=answer_chapters
    )


def probe(forbidden: list[str], chapter_max: int = 10) -> GoldSpoiler:
    return GoldSpoiler(
        id="s1", question="?", chapter_max=chapter_max, kind="hidden_identity",
        forbidden=forbidden, source_chapter=chapter_max + 1,
    )


def full_gold(**overrides: Any) -> GoldSet:
    """Return a gold set of the Sprint 1 size that passes validation on TEXTS."""
    values: dict[str, Any] = {
        "meta": {"annotator": "claude", "chapter_max": 3},
        "chapters": [chapter(entity("Hàn Lập"), chapter_id=1)] * 20,
        "aliases": [GoldAliasPair(a="Hàn Lập", b="Trương Thiết", category="character", same=False)] * 80,
        "qa": [qa([1])] * 15,
        "spoilers": [probe(["Bình Ngọc"], chapter_max=1)] * 10,
    }
    return GoldSet(**{**values, **overrides})


TEXTS = {1: "Hàn Lập gặp Trương Thiết.", 2: "Bình Ngọc xuất hiện."}


def validate(gold: GoldSet, texts: dict[int, str] = TEXTS) -> list[str]:
    return validate_gold(gold, texts, categories=CATEGORIES, event_types=EVENT_TYPES)


def test_load_gold_reads_every_asset(tmp_path: Path) -> None:
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "meta.json").write_text('{"annotator": "claude", "chapter_max": 100}', encoding="utf-8")
    rows = {
        "chapters": chapter(entity("Hàn Lập")),
        "aliases": GoldAliasPair(a="a", b="b", category="character", same=True),
        "qa": qa([1]),
        "spoilers": probe(["x"]),
    }
    for name, row in rows.items():
        (gold_dir / f"{name}.jsonl").write_text(row.model_dump_json() + "\n\n", encoding="utf-8")

    gold = load_gold(tmp_path)

    assert gold.meta["annotator"] == "claude"
    assert gold.chapters == [rows["chapters"]]
    assert gold.aliases == [rows["aliases"]]
    assert gold.qa == [rows["qa"]]
    assert gold.spoilers == [rows["spoilers"]]


def test_validate_accepts_a_good_set() -> None:
    assert validate(full_gold()) == []


def test_validate_reports_short_assets() -> None:
    problems = validate(full_gold(qa=[qa([1])]))
    assert problems == ["qa: 1 items, need 15"]


def test_validate_rejects_a_name_missing_from_its_chapter() -> None:
    problems = validate(full_gold(chapters=[chapter(entity("Lệ Phi Vũ"))] * 20))
    assert "chapter 1, Lệ Phi Vũ: name not in text: le phi vu" in problems


def test_validate_rejects_unknown_labels() -> None:
    bad = entity("Hàn Lập", category="beast", types=("FLY",))
    problems = validate(full_gold(chapters=[chapter(bad)] * 20))
    assert "chapter 1, Hàn Lập: unknown category beast" in problems
    assert "chapter 1, Hàn Lập: unknown event type FLY" in problems


def test_validate_rejects_an_answer_above_the_bound() -> None:
    problems = validate(full_gold(qa=[qa([51])] * 15))
    assert "q1: answer chapter 51 is above the bound 50" in problems


def test_validate_rejects_a_forbidden_term_visible_by_the_bound() -> None:
    problems = validate(full_gold(spoilers=[probe(["Hàn Lập"], chapter_max=1)] * 10))
    assert "s1: Hàn Lập not in chapter 2" in problems
    assert "s1: Hàn Lập already appears by chapter 1" in problems


def test_validate_rejects_a_source_before_the_bound() -> None:
    bad = probe(["Bình Ngọc"], chapter_max=1).model_copy(update={"source_chapter": 1})
    assert "s1: source chapter is not after the bound" in validate(full_gold(spoilers=[bad] * 10))


def test_name_key_ignores_case_and_diacritics() -> None:
    assert name_key(" HÀN LẬP, ") == name_key("Han Lap") == "han lap"


def test_recall_counts_aliases_holders_and_same_as() -> None:
    gold = [chapter(entity("Hàn Lập", "Tiểu Lập"), entity("Mặc đại phu"), entity("Lệ Phi Vũ"), entity("Ngô"))]
    seen = {1: payload(
        event("Tiểu Lập"),
        event("Bình", "weapon_treasure", holder="Mặc Đại Phu", same_as="Lệ Phi Vũ"),
    )}
    score = score_extraction(gold, seen)
    assert score["recall"] == pytest.approx(3 / 4)
    assert score["missed"] == ["1: Ngô"]


def test_precision_ignores_optional_names_and_counts_the_rest() -> None:
    gold = [chapter(entity("Hàn Lập"), optional=("Tiểu Hoàn",))]
    seen = {1: payload(event("Hàn Lập"), event("Tiểu Hoàn"), event("Người lạ"))}
    score = score_extraction(gold, seen)
    assert score["precision"] == pytest.approx(1 / 2)
    assert score["extra"] == ["1: nguoi la"]


def test_category_and_event_type_use_events_under_the_entity_name() -> None:
    gold = [chapter(
        entity("A", types=("ACQUISITION",)),
        entity("B", types=("ACQUISITION",)),
        entity("C"),
    )]
    seen = {1: payload(
        event("A", event_type="ACQUISITION"),
        event("B", "weapon_treasure"),
        event("Bình", "weapon_treasure", holder="C"),
    )}
    score = score_extraction(gold, seen)
    assert score["category_accuracy"] == pytest.approx(1 / 2)
    assert score["event_type_accuracy"] == pytest.approx(1 / 2)


def test_flag_f1_per_flag() -> None:
    gold = [
        chapter(chapter_id=1, major_conflict=True),
        chapter(chapter_id=2, major_conflict=True),
        chapter(chapter_id=3),
        chapter(chapter_id=4),
    ]
    seen = {
        1: payload(major_conflict=True),
        2: payload(major_conflict=False),
        3: payload(major_conflict=True),
        4: payload(major_conflict=False),
    }
    flags = score_extraction(gold, seen)["flag_f1"]
    assert flags["major_conflict"] == pytest.approx(1 / 2)
    assert flags["location_change"] == 0.0


def test_score_extraction_needs_every_gold_chapter() -> None:
    with pytest.raises(ValueError, match=r"\[1\]"):
        score_extraction([chapter()], {})


def test_resolver_merges_through_the_real_cascade() -> None:
    def pair(a: str, b: str) -> GoldAliasPair:
        return GoldAliasPair(a=a, b=b, category="character", same=True)

    texts = {1: "Hàn Lập và han lap gặp Hàn Bảo Lập."}
    assert resolver_merges(pair("Hàn Lập", "han lap"), texts, lambda m, c: False)
    assert not resolver_merges(pair("Hàn Lập", "Trương Thiết"), texts, lambda m, c: True)


def test_resolver_gives_the_adjudicator_text_around_the_name() -> None:
    seen = []

    def adjudicate(mention: Any, candidate: Any) -> bool:
        seen.append((mention.chapter_id, mention.context, candidate.contexts))
        return True

    pair = GoldAliasPair(a="Hàn Lập", b="Hàn Lão Lập", category="character", same=True)
    texts = {1: "Sáng sớm Hàn Lập ra đi.", 2: "Chiều tối Hàn Lão Lập trở về."}
    assert resolver_merges(pair, texts, adjudicate)
    assert seen == [(2, "Chiều tối Hàn Lão Lập trở về.", ["Sáng sớm Hàn Lập ra đi."])]


def test_score_aliases_treats_same_as_positive() -> None:
    def pair(a: str, same: bool) -> GoldAliasPair:
        return GoldAliasPair(a=a, b="x", category="character", same=same)

    pairs = [pair("tp", True), pair("fn", True), pair("fp", False), pair("tn", False)]
    score = score_aliases(pairs, [True, False, True, False])
    assert score["f1"] == pytest.approx(1 / 2)
    assert score["wrong"] == ["fn / x: gold same", "fp / x: gold different"]


def test_score_qa_uses_evidence_chapters_and_counts_refusals() -> None:
    items = [qa([1, 2], "q1"), qa([3], "q2"), qa([4], "q3")]
    states: list[Any] = [
        {"evidence": EvidencePack(items=[evidence(1), evidence(9)])},
        {"evidence": EvidencePack(items=[evidence(3)]), "refusal": "Không đủ dữ liệu đến chương 50"},
        {"evidence": None},
    ]
    score = score_qa(items, states)
    assert score["citation_hit_rate"] == pytest.approx(1 / 3)
    assert score["context_recall"] == pytest.approx(0.5 / 3)
    assert score["refused"] == 1
    assert score["misses"] == [
        "q2: Không đủ dữ liệu đến chương 50",
        "q3: answer chapters not in evidence",
    ]


def test_score_spoilers_finds_a_forbidden_term_in_each_channel() -> None:
    probes = [probe(["bí mật"])] * 3
    states: list[Any] = [
        {"evidence": EvidencePack(items=[evidence(1, "BI MAT lộ ra")])},
        {"evidence": EvidencePack(items=[evidence(1, quote="Đây là bí mật")])},
        {"suggestions": {"x": ["Bí Mật"]}, "refusal": "no"},
    ]
    assert score_spoilers(probes, states)["leaks"] == 3


def test_score_spoilers_ignores_evidence_a_refusal_never_sends() -> None:
    state: Any = {"evidence": EvidencePack(items=[evidence(1, "bí mật")]), "refusal": "no"}
    assert score_spoilers([probe(["bí mật"])], [state])["leaks"] == 0


def test_score_spoilers_reports_a_chapter_above_the_bound() -> None:
    state: Any = {"evidence": EvidencePack(items=[evidence(11)])}
    assert score_spoilers([probe(["x"], chapter_max=10)], [state])["details"] == [
        "s1: chapter 11 above bound 10"
    ]


@pytest.fixture
def repo(tmp_path: Path) -> Any:
    conn = connect(tmp_path / "xn.db")
    init_schema(conn)
    repository = Repository(conn)
    repository.add_novel(novel_id="pntt1", title="t", genre_profile="xianxia", total_chapters=100)
    yield repository
    conn.close()


def _save_chapter(repo: Repository, chapter_id: int, text: str, data: dict) -> None:
    repo.save_chapter(
        novel_id="pntt1", chapter_id=chapter_id, title=None, clean_text=text, sha256="s",
        token_count=1, stage="s0", input_hash="h",
    )
    repo.save_extraction(
        novel_id="pntt1", chapter_id=chapter_id, payload=json.dumps(data), prompt_version="v",
        model="m", stage="s1", input_hash="h",
    )


def test_evidence_state_stops_at_gate_without_a_cloud_call(repo: Repository) -> None:
    def no_cloud(name: str) -> Any:
        raise AssertionError(f"cloud call: {name}")

    deps = GraphDeps(
        repo=repo, config=CONFIG, prompts_dir=PROMPTS_DIR, cloud_factory=no_cloud,
        router_model=TestModel(custom_output_args={"intent": "factual_qa", "mentions": ["Ai"]}),
    )
    state = evidence_state(deps, novel_id="pntt1", question="Ai?", chapter_max=5)
    assert state["chapter_max"] == 5
    assert isinstance(state["decision"], RouterDecision)
    assert state["refusal"] == "Không đủ dữ liệu đến chương 5"


def test_gold_report_covers_every_asset(repo: Repository) -> None:
    _save_chapter(repo, 1, "Hàn Lập gặp Trương Thiết.", payload(event("Hàn Lập")))
    gold = GoldSet(
        meta={"annotator": "claude", "chapter_max": 1},
        chapters=[chapter(entity("Hàn Lập"))],
        aliases=[GoldAliasPair(a="Hàn Lập", b="Trương Thiết", category="character", same=False)],
        qa=[qa([1])],
        spoilers=[probe(["bí mật"])],
    )
    model = TestModel(custom_output_args={"intent": "factual_qa", "mentions": []})

    report = gold_report(repo, gold=gold, config=CONFIG, model=model, prompts_dir=PROMPTS_DIR)

    assert report["meta"]["annotator"] == "claude"
    assert report["extraction"]["recall"] == 1.0
    assert report["extraction_dev"]["recall"] == 1.0
    assert report["extraction_held_out"]["gold_entities"] == 0
    assert report["aliases"]["wrong"] == []
    assert report["qa"]["refused"] == 1
    assert report["spoilers"]["leaks"] == 0


def test_gold_tests_are_skipped_without_the_marker() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/eval", "-q", "--no-header"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout
    assert "skipped" in result.stdout and "failed" not in result.stdout
