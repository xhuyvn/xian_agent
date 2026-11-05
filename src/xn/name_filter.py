"""Name filter study (G-26): offline rules that drop false entity names from S1 extractions.

This module scores filters on stored extractions. Production S1 does not use it.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

import typer

from xn.gold import GoldChapter, gold_split, load_gold, name_key, score_extraction
from xn.text import normalize_text

QUOTES = "\"'“”‘’«»"
SENTENCE_ENDS = ".!?:\n…-–—"
# Categories whose names this translation writes with a capital letter. Items and techniques
# are often lower case ("phi kiếm", "trừu tủy hoàn"), so the capital rule leaves them alone.
PROPER_CATEGORIES = frozenset({"character", "dungeon"})

# Kinship and address terms, and people named by a description: never one entity.
ADDRESS_TERMS = frozenset(
    name_key(term)
    for term in (
        "cha", "mẹ", "cha mẹ", "phụ thân", "mẫu thân", "phụ mẫu", "nương", "gia gia", "nãi nãi",
        "ca ca", "đại ca", "nhị ca", "tam ca", "tứ ca", "tỷ tỷ", "đại tỷ", "nhị tỷ",
        "muội muội", "tiểu muội", "tiểu muội muội", "đệ đệ", "tiểu đệ", "huynh đệ",
        "thúc thúc", "đại thúc", "bá bá", "cữu cữu", "lão gia", "thiếu gia", "công tử", "tiểu thư",
        "sư phụ", "sư huynh", "sư đệ", "sư tỷ", "sư muội", "sư thúc", "sư bá", "đại sư huynh",
        "tiền bối", "vãn bối", "đạo hữu", "thiếu nữ", "thiếu niên", "lão giả", "lão nhân",
        "nữ tử", "nam tử", "tên mập", "người kia", "hắn", "nàng",
    )
)
# First words that make a counted group or a described thing ("bảy cái đầu quỷ", "cái bình kia").
COUNT_WORDS = frozenset(
    name_key(word)
    for word in (
        "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín", "mười",
        "mấy", "vài", "các", "những", "mọi", "cái", "chiếc", "con", "tên", "gã",
    )
)
# Last words that point at something already in view ("cái bình kia").
DEMONSTRATIVES = frozenset(name_key(word) for word in ("kia", "này", "đó", "ấy", "nọ"))


@dataclass(frozen=True)
class FilterRule:
    """One filter under study.

    caps: keep a name only if the chapter writes it with a capital first letter somewhere
        other than at a sentence start. caps_categories limits that test to these categories;
        None tests every category.
    words: drop kinship and address terms, counted groups and demonstrative descriptions.
    """

    name: str
    caps: bool = False
    caps_categories: frozenset[str] | None = None
    words: bool = False


RULES = (
    FilterRule("none"),
    FilterRule("caps", caps=True),
    FilterRule("caps-proper", caps=True, caps_categories=PROPER_CATEGORIES),
    FilterRule("caps-character", caps=True, caps_categories=frozenset({"character"})),
    FilterRule("words", words=True),
    FilterRule("caps+words", caps=True, words=True),
    FilterRule("caps-proper+words", caps=True, caps_categories=PROPER_CATEGORIES, words=True),
    FilterRule("caps-character+words", caps=True, caps_categories=frozenset({"character"}), words=True),
)


def _fold(text: str) -> str:
    """Lower-case text without diacritics, one character for each character of NFC text."""
    folded = "".join(name_key(c) or c for c in text)
    return folded if len(folded) == len(text) else ""


def _at_sentence_start(text: str, at: int) -> bool:
    before = text[:at].rstrip(" \t" + QUOTES)
    return not before or before[-1] in SENTENCE_ENDS


def capitalized_mid_sentence(name: str, text: str) -> bool:
    """Return True if text writes name with a capital first letter other than at a sentence start.

    Matching ignores case and diacritics and must cover whole words.
    """
    words = name_key(name).split()
    if not words:
        return False
    text = normalize_text(text)
    folded = _fold(text)
    if not folded:
        return False
    pattern = re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, words)) + r"(?!\w)")
    return any(
        text[m.start()].isupper() and not _at_sentence_start(text, m.start())
        for m in pattern.finditer(folded)
    )


def is_address_or_description(name: str) -> bool:
    """Return True for kinship and address terms, counted groups and demonstrative descriptions."""
    key = name_key(name)
    words = key.split()
    if not words:
        return False
    return key in ADDRESS_TERMS or words[0] in COUNT_WORDS or (len(words) > 1 and words[-1] in DEMONSTRATIVES)


def keep_name(name: str, category: str, text: str, rule: FilterRule) -> bool:
    """Return whether rule keeps this name, extracted from a chapter with this text."""
    if rule.words and is_address_or_description(name):
        return False
    if rule.caps and (rule.caps_categories is None or category in rule.caps_categories):
        return capitalized_mid_sentence(name, text)
    return True


def filter_payload(payload: dict[str, Any], text: str, rule: FilterRule) -> dict[str, Any]:
    """Return a copy of an extraction without the events and holder or same_as names rule drops.

    A holder is tested as a character; a same_as name takes its event's category.
    """
    out = copy.deepcopy(payload)
    events = []
    for event in out["events"]:
        if not keep_name(event["name"], event["category"], text, rule):
            continue
        if event.get("holder") and not keep_name(event["holder"], "character", text, rule):
            event["holder"] = None
        if event.get("same_as") and not keep_name(event["same_as"], event["category"], text, rule):
            event["same_as"] = None
        events.append(event)
    out["events"] = events
    return out


def score_rules(
    chapters: list[GoldChapter],
    payloads: dict[int, dict[str, Any]],
    texts: dict[int, str],
    rules: tuple[FilterRule, ...] = RULES,
) -> dict[str, dict[str, Any]]:
    """Score each rule's filtered extractions of these gold chapters with score_extraction."""
    return {
        rule.name: score_extraction(
            chapters,
            {c.chapter_id: filter_payload(payloads[c.chapter_id], texts[c.chapter_id], rule) for c in chapters},
        )
        for rule in rules
    }


def read_db(path: Path, *, novel_id: str, chapter_ids: set[int]) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    """Read extractions and clean texts of these chapters from a database, opened read-only."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        payloads = {
            row["chapter_id"]: json.loads(row["payload"])
            for row in conn.execute("SELECT chapter_id, payload FROM extractions WHERE novel_id = ?", (novel_id,))
            if row["chapter_id"] in chapter_ids
        }
        texts = {
            row["chapter_id"]: row["clean_text"]
            for row in conn.execute("SELECT chapter_id, clean_text FROM chapters WHERE novel_id = ?", (novel_id,))
            if row["chapter_id"] in chapter_ids
        }
    finally:
        conn.close()
    return payloads, texts


def render_study(scores: dict[str, dict[str, dict[str, Any]]], split: str) -> str:
    """Render recall and precision per rule and database as a Markdown table."""
    dbs = list(scores)
    rules = list(next(iter(scores.values())))
    lines = [
        f"Gold chapters: {split}.",
        "",
        "| Rule | " + " | ".join(dbs) + " |",
        "|---|" + "---|" * len(dbs),
    ]
    for rule in rules:
        cells = []
        for db in dbs:
            s = scores[db][rule]
            cells.append(
                f"R {s['recall']:.2f} ({s['found']}/{s['gold_entities']}), "
                f"P {s['precision']:.2f} ({s['false_names']} false)"
            )
        lines.append(f"| {rule} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(
    db: list[Path] = typer.Option(..., "--db", help="Database to score, read-only; repeat for more"),
    split: str = typer.Option("dev", "--split", help="Gold chapters: dev, held_out or all"),
    novel: str = typer.Option("pntt1", "--novel", help="Novel ID"),
    out: Path | None = typer.Option(None, "--out", help="Write the full scores as JSON here"),
) -> None:
    """Score every filter rule on the gold chapters of each database and print the table."""
    gold = load_gold(Path("eval") / novel)
    dev, held_out = gold_split(gold.chapters)
    chapters = {"dev": dev, "held_out": held_out, "all": gold.chapters}.get(split)
    if chapters is None:
        raise typer.BadParameter(f"--split must be dev, held_out or all, got {split}")
    ids = {c.chapter_id for c in chapters}
    scores = {}
    for path in db:
        payloads, texts = read_db(path, novel_id=novel, chapter_ids=ids)
        scores[path.stem] = score_rules(chapters, payloads, texts)
    if out is not None:
        out.write_text(json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(render_study(scores, split))


if __name__ == "__main__":
    typer.run(main)
