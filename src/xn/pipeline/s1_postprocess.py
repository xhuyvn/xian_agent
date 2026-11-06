"""S1 clean-up step (G-27): optional fixes applied to an extraction before it is saved.

Every fix is off unless the config turns it on, under `s1.postprocess`:

    s1:
      postprocess:
        name_filter: caps-proper+words   # a rule name from xn.name_filter.RULES, or null
        category_fix: true               # set the category from the name's last word
        dedupe_events: true              # drop repeats of the same event with another quote

With the section absent, or every key off, S1 saves exactly what it saved before.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from xn.schemas.extraction import ChapterExtraction
from xn.text import normalize_alias

# Last word of a name (lower case, with diacritics) -> the category it implies.
# Drawn from the dev-chapter category errors of the G-26 qwen3.5 run.
CATEGORY_BY_LAST_WORD: dict[str, str] = {
    **{word: "weapon_treasure" for word in ("đan", "hoàn", "tán", "thủy", "phù")},
    **{word: "technique" for word in ("công", "quyết", "bộ", "thuật", "pháp", "chú")},
    **{word: "dungeon" for word in ("môn", "bang", "cốc", "phong", "thôn")},
}
# The category fix never moves a name out of these categories.
PROTECTED_CATEGORIES = frozenset({"character"})



def _rules_by_name() -> dict[str, Any]:
    # Imported here: xn.name_filter pulls in xn.gold and the query graph, which S1 does not
    # need unless the name filter is on.
    from xn.name_filter import RULES

    return {rule.name: rule for rule in RULES if rule.name != "none"}


@dataclass(frozen=True)
class PostprocessSettings:
    """Which clean-up fixes S1 applies. The default applies none."""

    name_filter: str | None = None
    category_fix: bool = False
    dedupe_events: bool = False

    @property
    def enabled(self) -> bool:
        """Return whether any fix is on."""
        return bool(self.name_filter or self.category_fix or self.dedupe_events)

    def tag(self) -> str:
        """Return a stable text form of the settings, for S1's job input hash."""
        return f"name_filter={self.name_filter};category_fix={self.category_fix};dedupe_events={self.dedupe_events}"


def settings_from_config(config: dict[str, Any]) -> PostprocessSettings:
    """Read `s1.postprocess` from config; missing keys are off.

    Raises:
        ValueError: name_filter names no rule in xn.name_filter.RULES, or a key is unknown.
    """
    section = (config.get("s1") or {}).get("postprocess") or {}
    unknown = set(section) - {"name_filter", "category_fix", "dedupe_events"}
    if unknown:
        raise ValueError(f"Unknown s1.postprocess keys: {sorted(unknown)}")
    name_filter = section.get("name_filter") or None
    if name_filter is not None and name_filter not in (rules := _rules_by_name()):
        raise ValueError(f"s1.postprocess.name_filter must be one of {sorted(rules)}, got {name_filter!r}")
    return PostprocessSettings(
        name_filter=name_filter,
        category_fix=bool(section.get("category_fix", False)),
        dedupe_events=bool(section.get("dedupe_events", False)),
    )


def job_input_hash(chapter_sha256: str, settings: PostprocessSettings) -> str:
    """Return S1's job input hash: the chapter hash alone when no fix is on.

    With a fix on, the settings are hashed in too, so changing them reruns the chapter.
    """
    if not settings.enabled:
        return chapter_sha256
    return hashlib.sha256(f"{chapter_sha256}\n{settings.tag()}".encode("utf-8")).hexdigest()


def category_for_name(name: str, category: str, allowed: set[str]) -> str:
    """Return the category implied by the name's last word, or category when none applies.

    A name in a protected category (character) keeps it, and an implied category outside
    allowed is ignored.
    """
    if category in PROTECTED_CATEGORIES:
        return category
    words = normalize_alias(name).split()
    implied = CATEGORY_BY_LAST_WORD.get(words[-1]) if words else None
    return implied if implied is not None and implied in allowed else category


def _event_key(event: dict[str, Any]) -> tuple[Any, ...]:
    """Return what makes two events the same, apart from their quote and description."""
    def norm(value: str | None) -> str:
        return normalize_alias(value) if value else ""

    return (
        norm(event["name"]), event["category"], event["event_type"],
        norm(event.get("holder")), norm(event.get("same_as")), norm(event.get("tier_or_realm")),
    )


def postprocess(
    extraction: ChapterExtraction, text: str, settings: PostprocessSettings, *, allowed_categories: set[str]
) -> ChapterExtraction:
    """Apply the fixes that settings turn on, in order: name filter, category fix, dedupe.

    text is the chapter's clean text, which the name filter's capital test reads.
    """
    if not settings.enabled:
        return extraction
    payload = extraction.model_dump()
    if settings.name_filter:
        from xn.name_filter import filter_payload

        payload = filter_payload(payload, text, _rules_by_name()[settings.name_filter])
    events = payload["events"]
    if settings.category_fix:
        for event in events:
            event["category"] = category_for_name(event["name"], event["category"], allowed_categories)
    if settings.dedupe_events:
        seen: set[tuple[Any, ...]] = set()
        kept = []
        for event in events:
            key = _event_key(event)
            if key not in seen:
                seen.add(key)
                kept.append(event)
        events = kept
    payload["events"] = events
    return ChapterExtraction.model_validate(payload)
