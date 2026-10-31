"""Unit tests for extraction and lifecycle schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from xn.schemas.extraction import ChapterExtraction, EntityEvent
from xn.schemas.lifecycle import LifecycleEventType


def test_lifecycle_event_type_enum_values() -> None:
    """Verify LifecycleEventType contains exactly the 9 required values."""
    expected_values = {
        "ACQUISITION",
        "UPGRADE",
        "USED_IN_BATTLE",
        "DAMAGED",
        "TRANSFERRED",
        "DESTROYED",
        "FALLEN_OR_SEALED",
        "REVEALED_IDENTITY",
        "MENTIONED",
    }
    actual_values = {e.value for e in LifecycleEventType}
    assert actual_values == expected_values
    assert len(LifecycleEventType) == 9

    # Verify string subclassing
    for item in LifecycleEventType:
        assert isinstance(item, str)
        assert item == item.value


def test_entity_event_same_as_optional() -> None:
    """Verify same_as is optional and defaults to None."""
    event_without_same_as = EntityEvent(
        name="Chưởng Thiên Bình",
        category="weapon_treasure",
        event_type=LifecycleEventType.ACQUISITION,
        description="Hàn Lập nhặt được tiểu bình màu lục.",
        evidence="Hắn cúi người nhặt một chiếc bình nhỏ màu xanh lục.",
    )
    assert event_without_same_as.same_as is None
    assert event_without_same_as.holder is None
    assert event_without_same_as.tier_or_realm is None

    event_with_same_as = EntityEvent(
        name="Hàn Lập",
        category="character",
        event_type=LifecycleEventType.REVEALED_IDENTITY,
        same_as="Lệ Phi Vũ",
        description="Hàn Lập xưng tên là Lệ Phi Vũ.",
        evidence="Tại hạ Lệ Phi Vũ.",
    )
    assert event_with_same_as.same_as == "Lệ Phi Vũ"


def test_entity_event_field_limits() -> None:
    """Verify description (<=400) and evidence (<=200) field limits."""
    # Description limit: 400 is valid, 401 raises ValidationError
    valid_event = EntityEvent(
        name="Thanh Trúc Phong Vân Kiếm",
        category="weapon_treasure",
        event_type=LifecycleEventType.UPGRADE,
        description="d" * 400,
        evidence="e" * 200,
    )
    assert len(valid_event.description) == 400
    assert len(valid_event.evidence) == 200

    with pytest.raises(ValidationError):
        EntityEvent(
            name="Thanh Trúc Phong Vân Kiếm",
            category="weapon_treasure",
            event_type=LifecycleEventType.UPGRADE,
            description="d" * 401,
            evidence="e" * 10,
        )

    # Evidence limit: 201 raises ValidationError
    with pytest.raises(ValidationError):
        EntityEvent(
            name="Thanh Trúc Phong Vân Kiếm",
            category="weapon_treasure",
            event_type=LifecycleEventType.UPGRADE,
            description="d" * 10,
            evidence="e" * 201,
        )


def test_entity_event_invalid_enum_raises() -> None:
    """Verify invalid event_type strings fail validation."""
    with pytest.raises(ValidationError):
        EntityEvent(
            name="Thực thể",
            category="character",
            event_type="INVALID_TYPE",  # type: ignore[arg-type]
            description="Mô tả",
            evidence="Bằng chứng",
        )


def test_chapter_extraction_micro_summary_limits() -> None:
    """Verify micro_summary enforces min_length 200 and max_length 1200."""
    base_kwargs = {
        "chapter_id": 1,
        "map_location": "Thất Huyền Môn",
        "location_change": False,
        "mc_realm": "Luyện Khí tầng 1",
        "realm_breakthrough": True,
        "major_conflict": False,
        "events": [],
    }

    # Too short: 199 chars
    with pytest.raises(ValidationError):
        ChapterExtraction(
            **base_kwargs,
            micro_summary="s" * 199,
        )

    # Minimum boundary: 200 chars
    valid_min = ChapterExtraction(
        **base_kwargs,
        micro_summary="s" * 200,
    )
    assert len(valid_min.micro_summary) == 200

    # Maximum boundary: 1200 chars
    valid_max = ChapterExtraction(
        **base_kwargs,
        micro_summary="s" * 1200,
    )
    assert len(valid_max.micro_summary) == 1200

    # Too long: 1201 chars
    with pytest.raises(ValidationError):
        ChapterExtraction(
            **base_kwargs,
            micro_summary="s" * 1201,
        )


def test_chapter_extraction_events_limit() -> None:
    """Verify events list enforces max_length 25."""
    event = EntityEvent(
        name="Thực thể",
        category="character",
        event_type=LifecycleEventType.MENTIONED,
        description="Mô tả ngắn",
        evidence="Bằng chứng ngắn",
    )

    base_kwargs = {
        "chapter_id": 1,
        "map_location": "Thất Huyền Môn",
        "location_change": False,
        "realm_breakthrough": False,
        "major_conflict": False,
        "micro_summary": "m" * 200,
    }

    # Exactly 25 events is valid
    valid_25 = ChapterExtraction(
        **base_kwargs,
        events=[event] * 25,
    )
    assert len(valid_25.events) == 25

    # 26 events raises ValidationError
    with pytest.raises(ValidationError):
        ChapterExtraction(
            **base_kwargs,
            events=[event] * 26,
        )


def test_chapter_extraction_defaults() -> None:
    """Verify default values for time_skip_years and mc_realm optionality."""
    extraction = ChapterExtraction(
        chapter_id=10,
        map_location="Thái Nhạc Sơn Mạch",
        location_change=True,
        realm_breakthrough=False,
        major_conflict=True,
        micro_summary="t" * 250,
        events=[],
    )
    assert extraction.time_skip_years == 0.0
    assert extraction.mc_realm is None
