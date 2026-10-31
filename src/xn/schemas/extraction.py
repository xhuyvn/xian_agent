"""Extraction schemas for chapter extraction."""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field

from xn.schemas.lifecycle import LifecycleEventType


class EntityEvent(BaseModel):
    """Lifecycle event associated with an entity within a chapter."""

    name: str
    category: str
    event_type: LifecycleEventType
    holder: Optional[str] = None
    tier_or_realm: Optional[str] = None
    same_as: Optional[str] = None
    description: str = Field(max_length=400)
    evidence: str = Field(max_length=200, description="≤25-word verbatim quote")


class ChapterExtraction(BaseModel):
    """Structured extraction output for a single novel chapter."""

    chapter_id: int
    map_location: str
    location_change: bool
    mc_realm: Optional[str] = None
    realm_breakthrough: bool
    major_conflict: bool
    time_skip_years: float = 0.0
    micro_summary: str = Field(min_length=200, max_length=1200)
    events: list[EntityEvent] = Field(max_length=25)


class MicroSummary(BaseModel):
    """Concise chapter summary model."""

    text: str = Field(min_length=200, max_length=1200)
