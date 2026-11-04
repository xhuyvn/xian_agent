"""Arc schemas for stages S5 and S6."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BoundaryReview(BaseModel):
    """The local model's verdict on one proposed arc boundary."""

    start_chapter: int = Field(description="The chapter that opens the new arc")
    reason: str = Field(max_length=300)


class StoryArcSynthesis(BaseModel):
    """What the macro model writes about one arc, from that arc's micro-summaries only (S6)."""

    title: str = Field(max_length=120, description="Tên ngắn của mạch truyện")
    primary_locations: list[str] = Field(max_length=10, description="Các địa bàn chính")
    core_conflict: str = Field(max_length=800, description="Mâu thuẫn hoặc mục tiêu cốt lõi")
    climax_event: str = Field(max_length=800, description="Sự kiện cao trào của mạch truyện")
    resolution: str = Field(max_length=800, description="Kết cục ở cuối mạch truyện")
    power_progression: str = Field(max_length=600, description="Thay đổi cảnh giới của nhân vật chính")
    key_entities: list[str] = Field(max_length=20, description="Nhân vật, bảo vật, thế lực then chốt")
    summary: str = Field(max_length=4000, description="Tóm tắt toàn mạch truyện, 250-400 từ")


class StoryArc(StoryArcSynthesis):
    """The `arcs.payload` of one arc: the synthesis plus the span S5 chose."""

    arc_id: int
    start_chapter: int
    end_chapter: int
