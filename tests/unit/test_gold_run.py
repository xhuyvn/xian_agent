"""Unit tests for gold tuning runs, using a fake model and made-up chapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic_ai.models.test import TestModel
import pytest

from xn.gold import GoldChapter, GoldEntity, GoldSet, gold_split
from xn.gold_run import recorded_drops, run_gold_chapters, summarize
from xn.pipeline.s1_extract import logger as s1_logger
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

REPO_ROOT = Path(__file__).resolve().parents[2]
TEXT = "Hàn Lập đi vào sơn cốc và gặp Trương Thiết ở đó vào lúc sáng sớm."
CONFIG: dict[str, Any] = {
    "novel_id": "pntt1",
    "total_chapters": 4,
    "categories": ["character"],
    "event_types": {"MENTIONED": "x"},
    "models": {"local_extractor": {"serving_name": "fake-local", "output_mode": "tool"}},
}


def gold_chapter(chapter_id: int, name: str = "Hàn Lập") -> GoldChapter:
    return GoldChapter(
        chapter_id=chapter_id, location_change=False, realm_breakthrough=False, major_conflict=False,
        entities=[GoldEntity(name=name, category="character", event_types=["MENTIONED"])],
    )


def gold_set(*ids: int) -> GoldSet:
    return GoldSet(meta={"chapter_max": 4}, chapters=[gold_chapter(n) for n in ids], aliases=[], qa=[], spoilers=[])


def event(name: str, *, category: str = "character", evidence: str = TEXT[:20]) -> dict[str, Any]:
    return {
        "name": name, "category": category, "event_type": "MENTIONED",
        "description": "d", "evidence": evidence,
    }


def fake_model(*events: dict[str, Any]) -> TestModel:
    return TestModel(custom_output_args={
        "chapter_id": 1, "map_location": "x", "location_change": False, "realm_breakthrough": False,
        "major_conflict": False, "micro_summary": "tóm tắt " * 40, "events": list(events),
    })


@pytest.fixture
def repo(tmp_path: Path) -> Any:
    conn = connect(tmp_path / "xn.db")
    init_schema(conn)
    repository = Repository(conn)
    repository.add_novel(novel_id="pntt1", title="t", genre_profile="xianxia", total_chapters=4)
    for n in range(1, 5):
        repository.save_chapter(
            novel_id="pntt1", chapter_id=n, title=None, clean_text=TEXT, sha256=f"s{n}",
            token_count=10, stage="s0", input_hash="h",
        )
    yield repository
    conn.close()


def test_split_takes_turns_in_chapter_order() -> None:
    dev, held = gold_split([gold_chapter(n) for n in (6, 1, 17, 11)])
    assert [c.chapter_id for c in dev] == [1, 11]
    assert [c.chapter_id for c in held] == [6, 17]


def test_recorded_drops_reads_both_reasons_and_detaches() -> None:
    with recorded_drops() as drops:
        s1_logger.warning("Chapter %d: dropping event '%s' with invalid category '%s'", 1, "A", "x")
        s1_logger.warning(
            "Chapter %d: dropping event '%s' with low evidence score %.1f < %d: %s", 2, "B", 50.0, 85, "q"
        )
        s1_logger.warning("Something else happened")
    s1_logger.warning("Chapter %d: dropping event '%s' with invalid category '%s'", 3, "C", "x")
    assert drops == [
        {"chapter_id": 1, "name": "A", "reason": "invalid_category"},
        {"chapter_id": 2, "name": "B", "reason": "low_evidence"},
    ]


def test_summary_hides_held_out_names_and_scores() -> None:
    gold = gold_set(1, 2)
    drops = [
        {"chapter_id": 1, "name": "dev-name", "reason": "low_evidence"},
        {"chapter_id": 2, "name": "held-name", "reason": "low_evidence"},
    ]
    payloads = {1: {"events": [event("Hàn Lập")]}, 2: {"events": []}}
    report = summarize(gold, drops, payloads)
    assert report["dev"] == {"kept": 1, "dropped": {"low_evidence": 1}}
    assert report["held_out"] == {"kept": 0, "dropped": {"low_evidence": 1}}
    assert "held-name" not in json.dumps(report)
    assert report["dev_score"]["gold_entities"] == 1


def test_run_counts_real_drops_from_s1(repo: Repository) -> None:
    model = fake_model(
        event("Hàn Lập"),
        event("Sai", evidence="một câu hoàn toàn không có trong chương truyện này"),
        event("Lạ", category="beast"),
    )
    report = run_gold_chapters(
        repo, gold=gold_set(1, 2), config=CONFIG, prompt="v1", model=model,
        prompts_dir=REPO_ROOT / "prompts",
    )
    assert report["written"] == 2
    assert report["dev"] == {"kept": 1, "dropped": {"low_evidence": 1, "invalid_category": 1}}
    assert report["held_out"]["kept"] == 1
    assert report["dev_score"]["recall"] == 1.0


def test_only_dev_leaves_held_out_chapters_unrun(repo: Repository) -> None:
    report = run_gold_chapters(
        repo, gold=gold_set(1, 2, 3), config=CONFIG, prompt="v1", only_dev=True,
        model=fake_model(event("Hàn Lập")), prompts_dir=REPO_ROOT / "prompts",
    )
    assert report["written"] == 2
    assert repo.get_job(novel_id="pntt1", chapter_id=2, stage="s1") is None


def test_chapters_outside_gold_are_not_run(repo: Repository) -> None:
    run_gold_chapters(
        repo, gold=gold_set(2), config=CONFIG, prompt="v1",
        model=fake_model(event("Hàn Lập")), prompts_dir=REPO_ROOT / "prompts",
    )
    assert repo.get_job(novel_id="pntt1", chapter_id=1, stage="s1") is None
