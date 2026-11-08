r"""Unit tests for the optional S2 matching rules, config block s2_rules (G-31.1)."""

from __future__ import annotations

import copy
import sqlite3
from typing import Any

import pytest
from pydantic_ai.models.function import FunctionModel

from xn.gold import GoldAliasPair, resolver_merges
from xn.pipeline.s2_resolve import (
    Candidate,
    Mention,
    Resolver,
    S2Rules,
    S2StaleError,
    run_s2,
    s2_rules_from_config,
)
from tests.unit.test_s2_run import (  # noqa: F401  (fixtures)
    PROMPTS_DIR,
    FakeAdjudicator,
    db_conn,
    make_canned_extraction,
    make_config,
    repo,
    save_chapter_and_extraction,
)

RULES = S2Rules(
    generic_terms=frozenset({"phù lục", "chưởng môn"}),
    titles=frozenset({"sư đệ", "sư huynh", "trưởng lão"}),
)


class Recorder:
    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.asked: list[tuple[str, list[str]]] = []

    def __call__(self, mention: Mention, candidate: Candidate) -> bool:
        self.asked.append((mention.name, list(candidate.aliases)))
        return self.answer


def resolve(resolver: Resolver, name: str, category: str = "character", chapter_id: int = 1) -> int | None:
    return resolver.resolve(name=name, category=category, chapter_id=chapter_id, context=f"{name} ctx")


def test_rules_from_config_none_when_absent_or_empty() -> None:
    assert s2_rules_from_config({}) is None
    assert s2_rules_from_config({"s2_rules": {}}) is None
    assert s2_rules_from_config({"s2_rules": {"generic_terms": [], "titles": []}}) is None
    rules = s2_rules_from_config({"s2_rules": {"generic_terms": ["Phù Lục "], "titles": ["sư  huynh"]}})
    assert rules == S2Rules(generic_terms=frozenset({"phù lục"}), titles=frozenset({"sư huynh"}))


def test_title_surname() -> None:
    assert RULES.title_surname("hàn sư đệ") == "hàn"
    assert RULES.title_surname("hàn lập") is None
    assert RULES.title_surname("sư đệ") is None


def test_generic_mention_is_never_adjudicated() -> None:
    rec = Recorder(answer=True)
    resolver = Resolver(adjudicate=rec, rules=RULES)
    named = resolve(resolver, "phù lục hình kiếm", "weapon_treasure")
    generic = resolve(resolver, "phù lục", "weapon_treasure")
    assert rec.asked == []
    assert generic != named


def test_without_rules_the_generic_mention_is_adjudicated() -> None:
    rec = Recorder(answer=True)
    resolver = Resolver(adjudicate=rec)
    named = resolve(resolver, "phù lục hình kiếm", "weapon_treasure")
    generic = resolve(resolver, "phù lục", "weapon_treasure")
    assert rec.asked == [("phù lục", ["phù lục hình kiếm"])]
    assert generic == named


def test_candidate_with_only_generic_aliases_is_dropped() -> None:
    rec = Recorder(answer=True)
    resolver = Resolver(adjudicate=rec, rules=RULES)
    generic = resolve(resolver, "chưởng môn")
    named = resolve(resolver, "Chung chưởng môn")
    assert rec.asked == []
    assert named != generic


def test_title_form_is_sent_with_the_named_person_of_that_surname() -> None:
    rec = Recorder(answer=True)
    resolver = Resolver(adjudicate=rec, rules=RULES)
    han = resolve(resolver, "Hàn Lập")
    resolve(resolver, "Lệ Phi Vũ")
    title = resolve(resolver, "Hàn sư đệ", chapter_id=2)
    assert rec.asked == [("Hàn sư đệ", ["hàn lập"])]
    assert title == han


def test_title_form_is_new_when_the_adjudicator_says_no() -> None:
    rec = Recorder(answer=False)
    resolver = Resolver(adjudicate=rec, rules=RULES)
    han = resolve(resolver, "Hàn Lập")
    title = resolve(resolver, "Hàn sư đệ", chapter_id=2)
    assert rec.asked == [("Hàn sư đệ", ["hàn lập"])]
    assert title != han


def test_title_form_without_rules_is_not_sent() -> None:
    rec = Recorder(answer=True)
    resolver = Resolver(adjudicate=rec)
    han = resolve(resolver, "Hàn Lập")
    title = resolve(resolver, "Hàn sư đệ", chapter_id=2)
    assert rec.asked == []
    assert title != han


def test_title_candidates_skip_other_title_forms_and_categories_and_cap_at_three() -> None:
    rec = Recorder(answer=False)
    resolver = Resolver(adjudicate=rec, rules=RULES)
    for ch, name in enumerate(["Vương Một", "Vương Hai", "Vương Ba", "Vương Bốn"], start=1):
        resolve(resolver, name, chapter_id=ch)
    resolve(resolver, "Vương trưởng lão", chapter_id=5)  # a title form is not a named candidate
    resolve(resolver, "Vương Kiếm", "weapon_treasure", chapter_id=6)  # other category
    rec.asked.clear()
    resolve(resolver, "Vương sư huynh", chapter_id=7)
    assert rec.asked == [
        ("Vương sư huynh", ["vương bốn"]),
        ("Vương sư huynh", ["vương ba"]),
        ("Vương sư huynh", ["vương hai"]),
    ]


def test_title_forms_only_for_characters() -> None:
    rec = Recorder(answer=True)
    resolver = Resolver(adjudicate=rec, rules=RULES)
    resolve(resolver, "Hàn Kiếm", "weapon_treasure")
    resolve(resolver, "Hàn sư đệ", "weapon_treasure", chapter_id=2)
    assert rec.asked == []


def rules_config(**overrides: Any) -> dict[str, Any]:
    cfg = make_config(**overrides)
    cfg["s2_rules"] = {"generic_terms": ["phù lục"], "titles": ["sư huynh"]}
    return cfg


def test_run_s2_hash_changes_only_when_rules_are_set(repo: Any, db_conn: sqlite3.Connection) -> None:
    for cid in (1, 2):
        save_chapter_and_extraction(repo, chapter_id=cid, extraction=make_canned_extraction(chapter_id=cid))
    plain = make_config(total_chapters=2)
    run_s2(repo, novel_id="pntt1", config=plain, model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)
    with pytest.raises(S2StaleError):
        run_s2(repo, novel_id="pntt1", config=rules_config(total_chapters=2),
               model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)
    res = run_s2(repo, novel_id="pntt1", config=rules_config(total_chapters=2), rebuild=True,
                 model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)
    assert res.written == 2
    changed = copy.deepcopy(rules_config(total_chapters=2))
    changed["s2_rules"]["titles"].append("sư đệ")
    with pytest.raises(S2StaleError):
        run_s2(repo, novel_id="pntt1", config=changed, model=FunctionModel(FakeAdjudicator()), prompts_dir=PROMPTS_DIR)


def test_gold_resolver_merges_applies_rules() -> None:
    pair = GoldAliasPair(a="Hàn Lập", b="Hàn sư đệ", category="character", same=True)
    texts = {1: "Hàn Lập đi. Hàn sư đệ về."}
    rec = Recorder(answer=True)
    assert resolver_merges(pair, texts, rec, RULES) is True
    assert resolver_merges(pair, texts, Recorder(answer=True)) is False
