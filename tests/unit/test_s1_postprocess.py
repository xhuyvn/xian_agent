r"""Unit tests for the S1 clean-up step (G-27)."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from xn.pipeline.s1_extract import run_s1
from xn.pipeline.s1_postprocess import (
    PostprocessSettings,
    category_for_name,
    job_input_hash,
    postprocess,
    settings_from_config,
)
from xn.schemas.extraction import ChapterExtraction
from xn.storage.database import connect, init_schema
from xn.storage.repository import Repository

from tests.unit.test_s1_extract import make_canned_extraction, make_config, seed_chapter

ALLOWED = {"character", "weapon_treasure", "puppet_beast", "technique", "dungeon"}
ALL_ON = {"name_filter": "caps-proper+words", "category_fix": True, "dedupe_events": True}
TEXT = (
    "Hàn Lập nuốt một viên Dưỡng tinh đan rồi gọi Đại ca. "
    "Sau đó Hàn Lập trở về Thất Huyền môn, nơi Hàn Lập luyện Trường xuân công."
)


def event(name: str, category: str, event_type: str = "MENTIONED", **fields: Any) -> dict[str, Any]:
    return {
        "name": name, "category": category, "event_type": event_type, "holder": None,
        "tier_or_realm": None, "same_as": None, "description": "mô tả",
        "evidence": fields.pop("evidence", name), **fields,
    }


def extraction(events: list[dict[str, Any]]) -> ChapterExtraction:
    return ChapterExtraction.model_validate(make_canned_extraction(chapter_id=1, events=events))


@pytest.fixture
def repo(tmp_path: Path):
    conn = connect(tmp_path / "s1pp.db")
    init_schema(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


def fake_model(events: list[dict[str, Any]], calls: list[int]) -> FunctionModel:
    def model_func(messages: list[Any], info: Any) -> ModelResponse:
        calls.append(1)
        part = ToolCallPart(info.output_tools[0].name, make_canned_extraction(chapter_id=1, events=events))
        return ModelResponse(parts=[part], usage=RequestUsage(input_tokens=10, output_tokens=10))

    return FunctionModel(model_func)


def profile_config(**s1: Any) -> dict[str, Any]:
    cfg = make_config(total_chapters=1, categories=sorted(ALLOWED))
    if s1:
        cfg["s1"] = s1
    return cfg


# --- settings --------------------------------------------------------------------------------


def test_settings_are_off_without_the_section() -> None:
    assert settings_from_config({}) == PostprocessSettings()
    assert settings_from_config({"s1": {}}) == PostprocessSettings()
    assert settings_from_config({"s1": {"postprocess": None}}) == PostprocessSettings()
    assert not PostprocessSettings().enabled


def test_settings_read_every_key() -> None:
    settings = settings_from_config({"s1": {"postprocess": ALL_ON}})
    assert settings == PostprocessSettings("caps-proper+words", True, True)
    assert settings.enabled


def test_settings_refuse_unknown_rule_and_key() -> None:
    with pytest.raises(ValueError, match="name_filter"):
        settings_from_config({"s1": {"postprocess": {"name_filter": "caps-maybe"}}})
    with pytest.raises(ValueError, match="name_filter"):
        settings_from_config({"s1": {"postprocess": {"name_filter": "none"}}})
    with pytest.raises(ValueError, match="Unknown"):
        settings_from_config({"s1": {"postprocess": {"dedupe": True}}})


def test_job_input_hash_is_the_chapter_hash_when_off() -> None:
    sha = "a" * 64
    assert job_input_hash(sha, PostprocessSettings()) == sha
    on = job_input_hash(sha, PostprocessSettings(dedupe_events=True))
    assert on != sha
    assert on != job_input_hash(sha, PostprocessSettings(category_fix=True))


# --- the three fixes ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "category", "expected"),
    [
        ("Dưỡng tinh đan", "technique", "weapon_treasure"),
        ("Thi Trùng Hoàn", "technique", "weapon_treasure"),
        ("Thanh Linh Tán", "technique", "weapon_treasure"),
        ("Ngũ Độc Thủy", "technique", "weapon_treasure"),
        ("bình an phù", "technique", "weapon_treasure"),
        ("Trường xuân công", "weapon_treasure", "technique"),
        ("La Yên bộ", "dungeon", "technique"),
        ("Dã Lang bang", "puppet_beast", "dungeon"),
        ("Lạc Nhật Phong", "technique", "dungeon"),
        ("Thất Huyền môn", "character", "character"),  # character is never overridden
        ("Phi kiếm", "technique", "technique"),  # no listed last word
        ("Tiểu phủ", "technique", "technique"),  # diacritics matter: phủ is not phù
    ],
)
def test_category_for_name(name: str, category: str, expected: str) -> None:
    assert category_for_name(name, category, ALLOWED) == expected


def test_category_fix_ignores_categories_outside_the_profile() -> None:
    assert category_for_name("Dưỡng tinh đan", "technique", {"technique", "character"}) == "technique"


def test_dedupe_keeps_the_first_of_events_that_differ_only_by_quote() -> None:
    events = [
        event("Hàn Lập", "character", evidence="Hàn Lập nuốt một viên"),
        event("Hàn Lập", "character", evidence="Hàn Lập trở về"),
        event("hàn lập", "character", evidence="Hàn Lập luyện"),
        event("Hàn Lập", "character", "ACQUISITION", evidence="Hàn Lập nuốt"),
    ]
    out = postprocess(extraction(events), TEXT, PostprocessSettings(dedupe_events=True), allowed_categories=ALLOWED)
    assert [(e.name, e.event_type, e.evidence) for e in out.events] == [
        ("Hàn Lập", "MENTIONED", "Hàn Lập nuốt một viên"),
        ("Hàn Lập", "ACQUISITION", "Hàn Lập nuốt"),
    ]


def test_name_filter_drops_address_terms_and_clears_holders() -> None:
    events = [
        event("Đại ca", "character"),
        event("Dưỡng tinh đan", "weapon_treasure", "ACQUISITION", holder="Đại ca"),
        event("Hàn Lập", "character"),
    ]
    out = postprocess(
        extraction(events), TEXT, PostprocessSettings(name_filter="caps-proper+words"), allowed_categories=ALLOWED
    )
    assert [e.name for e in out.events] == ["Dưỡng tinh đan", "Hàn Lập"]
    assert out.events[0].holder is None


def test_all_fixes_together() -> None:
    events = [
        event("Đại ca", "character"),
        event("Dưỡng tinh đan", "technique"),
        event("Dưỡng tinh đan", "weapon_treasure", evidence="viên Dưỡng tinh đan"),
        event("Hàn Lập", "character"),
    ]
    out = postprocess(extraction(events), TEXT, PostprocessSettings("caps-proper+words", True, True), allowed_categories=ALLOWED)
    assert [(e.name, e.category) for e in out.events] == [("Dưỡng tinh đan", "weapon_treasure"), ("Hàn Lập", "character")]


def test_off_returns_the_extraction_unchanged() -> None:
    original = extraction([event("Đại ca", "character"), event("Đại ca", "character", evidence="x")])
    assert postprocess(original, TEXT, PostprocessSettings(), allowed_categories=ALLOWED) is original


# --- inside run_s1 ----------------------------------------------------------------------------


RAW_EVENTS = [
    event("Đại ca", "character", evidence="gọi Đại ca"),
    event("Dưỡng tinh đan", "technique", evidence="viên Dưỡng tinh đan"),
    event("Hàn Lập", "character", evidence="Hàn Lập nuốt một viên"),
    event("Hàn Lập", "character", evidence="Hàn Lập trở về Thất Huyền môn"),
]


def saved(repo: Repository) -> tuple[dict[str, Any], dict[str, Any]]:
    conn: sqlite3.Connection = repo._conn  # type: ignore[attr-defined]
    row = conn.execute("SELECT payload FROM extractions WHERE chapter_id = 1").fetchone()
    return json.loads(row["payload"]), repo.get_job(novel_id="pntt1", chapter_id=1, stage="s1")


def test_run_s1_with_cleanup_off_saves_the_model_output_and_chapter_hash(repo: Repository) -> None:
    sha = seed_chapter(repo, clean_text=TEXT)
    calls: list[int] = []
    run_s1(repo, novel_id="pntt1", config=profile_config(), model=fake_model(RAW_EVENTS, calls))
    payload, job = saved(repo)
    assert [(e["name"], e["category"]) for e in payload["events"]] == [(e["name"], e["category"]) for e in RAW_EVENTS]
    assert job["input_hash"] == sha


def test_run_s1_with_cleanup_on_applies_it_and_reruns_when_settings_change(repo: Repository) -> None:
    sha = seed_chapter(repo, clean_text=TEXT)
    calls: list[int] = []
    model = fake_model(RAW_EVENTS, calls)
    run_s1(repo, novel_id="pntt1", config=profile_config(postprocess=ALL_ON), model=model)
    payload, job = saved(repo)
    assert [(e["name"], e["category"]) for e in payload["events"]] == [
        ("Dưỡng tinh đan", "weapon_treasure"), ("Hàn Lập", "character"),
    ]
    assert job["input_hash"] != sha
    assert len(calls) == 1

    run_s1(repo, novel_id="pntt1", config=profile_config(postprocess=ALL_ON), model=model)
    assert len(calls) == 1  # same settings: skipped

    run_s1(repo, novel_id="pntt1", config=profile_config(), model=model)
    assert len(calls) == 2  # turned off: the chapter runs again and is saved unfiltered
    payload, job = saved(repo)
    assert len(payload["events"]) == len(RAW_EVENTS)
    assert job["input_hash"] == sha
