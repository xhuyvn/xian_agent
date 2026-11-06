"""Unit tests for the G-26 name filter study."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from xn.gold import GoldChapter, GoldEntity
from xn.name_filter import (
    FilterRule,
    RULES,
    capitalized_mid_sentence,
    filter_payload,
    is_address_or_description,
    keep_name,
    read_db,
    render_study,
    score_cleanup,
    score_rules,
)

TEXT = (
    "Hàn Lập theo Đại ca đi lên núi. Đại ca nói rằng đường xa.\n"
    "“Phi kiếm của ta đâu?” hắn hỏi. Hắn thấy bảy cái đầu quỷ bay qua Thất Huyền Môn, "
    "cạnh cái bình kia. Mặc đại phu đưa thuốc cho Hàn Lập."
)


def event(name: str, category: str = "character", **extra: Any) -> dict[str, Any]:
    return {"name": name, "category": category, "event_type": "ACQUISITION", **extra}


def test_capitalized_mid_sentence_needs_a_capital_away_from_sentence_starts() -> None:
    assert capitalized_mid_sentence("Hàn Lập", TEXT)
    assert capitalized_mid_sentence("đại ca", TEXT)  # "theo Đại ca" is mid-sentence
    assert capitalized_mid_sentence("thất huyền môn", TEXT)
    assert not capitalized_mid_sentence("phi kiếm", TEXT)  # only after an opening quote
    assert not capitalized_mid_sentence("bảy cái đầu quỷ", TEXT)  # lower case
    assert not capitalized_mid_sentence("Lệ Phi Vũ", TEXT)  # not in the text


def test_capitalized_mid_sentence_ignores_diacritics_and_needs_whole_words() -> None:
    assert capitalized_mid_sentence("Han Lap", TEXT)
    assert not capitalized_mid_sentence("Lậ", TEXT)
    assert not capitalized_mid_sentence("", TEXT)


def test_sentence_starts_cover_newlines_ellipses_and_dashes() -> None:
    for text in ("Cái gì?\nĐại ca đến.", "Ừ… Đại ca đến.", "Ừ - Đại ca đến.", "Ừ: “Đại ca đến.”", "Đại ca đến."):
        assert not capitalized_mid_sentence("đại ca", text), text
    assert capitalized_mid_sentence("đại ca", "Ừ, Đại ca đến.")


def test_address_and_description_words() -> None:
    for name in ("Đại ca", "cha mẹ", "Nhị ca", "tên mập", "bảy cái đầu quỷ", "năm loại thảo dược", "cái bình kia"):
        assert is_address_or_description(name), name
    for name in ("Hàn Lập", "Thất Huyền Môn", "Trừu tủy hoàn", "Ngũ sát", "kia", ""):
        assert not is_address_or_description(name), name


def test_keep_name_applies_caps_only_to_its_categories() -> None:
    caps = FilterRule("caps", caps=True)
    proper = FilterRule("proper", caps=True, caps_categories=frozenset({"character"}))
    assert not keep_name("phi kiếm", "weapon_treasure", TEXT, caps)
    assert keep_name("phi kiếm", "weapon_treasure", TEXT, proper)
    assert not keep_name("phi kiếm", "character", TEXT, proper)
    words = FilterRule("words", caps=True, words=True)
    assert not keep_name("Đại ca", "character", TEXT, words)  # capitalized, but an address term
    assert keep_name("Hàn Lập", "character", TEXT, words)


def test_filter_payload_drops_events_and_clears_holders_without_touching_input() -> None:
    payload = {
        "events": [
            event("Hàn Lập"),
            event("bảy cái đầu quỷ", "puppet_beast"),
            event("Phi kiếm", "weapon_treasure", holder="Đại ca", same_as="Thất Huyền Môn"),
        ]
    }
    rule = FilterRule("both", caps=True, caps_categories=frozenset({"character", "puppet_beast"}), words=True)
    out = filter_payload(payload, TEXT, rule)
    assert [e["name"] for e in out["events"]] == ["Hàn Lập", "Phi kiếm"]
    assert out["events"][1]["holder"] is None
    assert out["events"][1]["same_as"] == "Thất Huyền Môn"
    assert len(payload["events"]) == 3 and payload["events"][2]["holder"] == "Đại ca"


def test_score_rules_scores_each_rule_on_gold() -> None:
    chapter = GoldChapter(
        chapter_id=5, location_change=False, realm_breakthrough=False, major_conflict=False,
        entities=[
            GoldEntity(name="Hàn Lập", category="character", event_types=["ACQUISITION"]),
            GoldEntity(name="phi kiếm", category="weapon_treasure", event_types=["ACQUISITION"]),
        ],
    )
    payloads = {5: {"events": [event("Hàn Lập"), event("phi kiếm", "weapon_treasure"), event("Đại ca")]}}
    scores = score_rules([chapter], payloads, {5: TEXT})
    assert set(scores) == {rule.name for rule in RULES}
    assert (scores["none"]["found"], scores["none"]["false_names"]) == (2, 1)
    assert (scores["caps"]["found"], scores["caps"]["false_names"]) == (1, 1)
    assert (scores["caps-proper+words"]["found"], scores["caps-proper+words"]["false_names"]) == (2, 0)
    table = render_study({"db": scores}, "dev")
    assert "| caps-proper+words | R 1.00 (2/2), P 1.00 (0 false), C 1.00 |" in table


def test_read_db_reads_only_the_chapters_asked_for(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE extractions (novel_id TEXT, chapter_id INTEGER, payload TEXT)")
    conn.execute("CREATE TABLE chapters (novel_id TEXT, chapter_id INTEGER, clean_text TEXT)")
    for n in (1, 2):
        conn.execute("INSERT INTO extractions VALUES ('pntt1', ?, ?)", (n, json.dumps({"events": []})))
        conn.execute("INSERT INTO chapters VALUES ('pntt1', ?, ?)", (n, f"text {n}"))
    conn.commit()
    conn.close()
    payloads, texts = read_db(path, novel_id="pntt1", chapter_ids={2})
    assert payloads == {2: {"events": []}} and texts == {2: "text 2"}


def test_score_cleanup_applies_every_fix_before_scoring() -> None:
    from tests.unit.test_s1_extract import make_canned_extraction

    chapter = GoldChapter(
        chapter_id=5, location_change=False, realm_breakthrough=False, major_conflict=False,
        entities=[
            GoldEntity(name="Hàn Lập", category="character", event_types=["MENTIONED"]),
            GoldEntity(name="Dưỡng tinh đan", category="weapon_treasure", event_types=["ACQUISITION"]),
        ],
    )

    def full(name: str, category: str, event_type: str, evidence: str) -> dict[str, Any]:
        return {
            "name": name, "category": category, "event_type": event_type, "holder": None,
            "tier_or_realm": None, "same_as": None, "description": "x", "evidence": evidence,
        }

    payload = make_canned_extraction(chapter_id=5, events=[
        full("Hàn Lập", "character", "MENTIONED", "Hàn Lập theo"),
        full("Hàn Lập", "character", "MENTIONED", "cho Hàn Lập"),
        full("Dưỡng tinh đan", "technique", "ACQUISITION", "thuốc"),
        full("Đại ca", "character", "MENTIONED", "theo Đại ca"),
    ])
    raw = score_rules([chapter], {5: payload}, {5: TEXT})["none"]
    assert (raw["false_names"], raw["category_accuracy"]) == (1, 0.5)
    cleaned = score_cleanup([chapter], {5: payload}, {5: TEXT}, {"character", "weapon_treasure", "technique"})
    assert (cleaned["found"], cleaned["false_names"], cleaned["category_accuracy"]) == (2, 0, 1.0)
