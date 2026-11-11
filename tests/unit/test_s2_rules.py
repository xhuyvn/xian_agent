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


def test_named_mention_may_still_merge_into_an_entity_known_by_a_generic_word() -> None:
    """The rule is on the mention only: "phù lục hình kiếm" can name the talisman first seen as "phù lục"."""
    rec = Recorder(answer=True)
    resolver = Resolver(adjudicate=rec, rules=RULES)
    generic = resolve(resolver, "phù lục", "weapon_treasure")
    named = resolve(resolver, "phù lục hình kiếm", "weapon_treasure")
    assert rec.asked == [("phù lục hình kiếm", ["phù lục"])]
    assert named == generic


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


class AnswerFor:
    """Say "same" only for candidates whose first alias is in `yes`."""

    def __init__(self, yes: set[str]) -> None:
        self.yes = yes
        self.asked: list[str] = []

    def __call__(self, mention: Mention, candidate: Candidate) -> bool:
        self.asked.append(candidate.aliases[0])
        return candidate.aliases[0] in self.yes


def test_title_form_merges_when_exactly_one_candidate_is_same() -> None:
    ans = AnswerFor({"ngô phong"})
    resolver = Resolver(adjudicate=ans, rules=RULES)
    ids = {n: resolve(resolver, n, chapter_id=ch) for ch, n in enumerate(["Ngô Minh", "Ngô Phong", "Ngô Kiếm"], 1)}
    title = resolve(resolver, "Ngô sư huynh", chapter_id=9)
    assert sorted(ans.asked) == ["ngô kiếm", "ngô minh", "ngô phong"]  # every candidate is asked
    assert title == ids["Ngô Phong"]


def test_title_form_is_new_when_two_candidates_are_same() -> None:
    ans = AnswerFor({"ngô phong", "ngô minh"})
    resolver = Resolver(adjudicate=ans, rules=RULES)
    known = {resolve(resolver, n, chapter_id=ch) for ch, n in enumerate(["Ngô Minh", "Ngô Phong"], 1)}
    title = resolve(resolver, "Ngô sư huynh", chapter_id=9)
    assert title not in known


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


GUARDS = S2Rules(
    generic_terms=frozenset({"phù lục"}),
    type_classes=(
        ("place", frozenset({"đài", "châu", "lĩnh", "sơn", "sơn mạch"})),
        ("event", frozenset({"đại hội"})),
        ("technique", frozenset({"thuật"})),
        ("pill", frozenset({"hoàn"})),
    ),
    marker_classes=(("talisman", frozenset({"phù", "phù lục"})),),
    title_ranks=(frozenset({"trưởng lão"}), frozenset({"đạo trưởng"}), frozenset({"môn chủ", "đại môn chủ"})),
    distinct_min_edit=4,
)


@pytest.mark.parametrize(
    ("name", "aliases", "category", "blocked"),
    [
        ("thiên vụ đài", ["thăng tiên đại hội"], "dungeon", True),  # place against event
        ("phù lục hỏa xà thuật", ["hỏa xà thuật"], "technique", True),  # talisman against technique
        ("thiên vụ đài", ["thiên vụ đài cũ", "thăng tiên đại hội"], "dungeon", False),  # one alias has no class
        ("băng trùy thuật", ["băng đống thuật"], "technique", True),  # distinct name word
        ("trát nhãn kiếp thuật", ["trát nhãn kiếm thuật"], "technique", False),  # one-letter typo
        ("khu vật thuật", ["khống vật thuật"], "technique", False),  # edit distance 3 < 4
        ("kim tủy hoàn", ["trừu tủy hoàn", "kim tủy hoàn x"], "weapon_treasure", False),  # not every alias has the shape
        ("lam châu mỗ sơn lĩnh", ["lam châu"], "dungeon", True),  # a place inside the candidate place
        ("thái nhạc sơn mạch", ["thái nhạc sơn"], "dungeon", False),  # only one extra word
        ("lý đạo trưởng", ["lý trưởng lão"], "character", True),  # same surname, other rank
        ("vương đại môn chủ", ["vương môn chủ"], "character", False),  # same rank
        ("lý đạo trưởng", ["lý thanh"], "character", False),  # candidate is not a title form
        ("thiên vụ đài", ["thăng tiên đại hội"], "character", False),  # class rules skip characters
    ],
)
def test_guards(name: str, aliases: list[str], category: str, blocked: bool) -> None:
    assert GUARDS.blocks(name, aliases, category) is blocked


def test_guards_off_without_their_settings() -> None:
    assert RULES.blocks("băng trùy thuật", ["băng đống thuật"], "technique") is False
    assert RULES.blocks("lý đạo trưởng", ["lý trưởng lão"], "character") is False


def test_resolver_never_asks_about_a_blocked_candidate() -> None:
    rec = Recorder(answer=True)
    resolver = Resolver(adjudicate=rec, rules=GUARDS)
    spell = resolve(resolver, "Băng đống thuật", "technique")
    other = resolve(resolver, "Băng trùy thuật", "technique", chapter_id=2)
    assert rec.asked == []
    assert other != spell


def test_guard_settings_build_from_config_and_enter_the_hash() -> None:
    cfg = {"s2_rules": {
        "type_classes": {"place": ["Đài"]}, "marker_classes": {"talisman": ["phù"]},
        "title_ranks": [["trưởng lão"], []], "distinct_min_edit": 4,
    }}
    rules = s2_rules_from_config(cfg)
    assert rules is not None
    assert rules.type_classes == (("place", frozenset({"đài"})),)
    assert rules.title_ranks == (frozenset({"trưởng lão"}),)
    assert rules.as_hash_meta()["distinct_min_edit"] == 4
    assert "type_classes" not in RULES.as_hash_meta()  # R-1 style blocks keep their R-1 hash
