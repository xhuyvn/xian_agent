"""Lifecycle event types for entity tracking across chapters."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class LifecycleEventType(str, Enum):
    """Categorized lifecycle event types recorded for novel entities."""

    ACQUISITION = "ACQUISITION"
    UPGRADE = "UPGRADE"
    USED_IN_BATTLE = "USED_IN_BATTLE"
    DAMAGED = "DAMAGED"
    TRANSFERRED = "TRANSFERRED"
    DESTROYED = "DESTROYED"
    FALLEN_OR_SEALED = "FALLEN_OR_SEALED"
    REVEALED_IDENTITY = "REVEALED_IDENTITY"
    MENTIONED = "MENTIONED"


class EntityLifecycleRecord(BaseModel):
    """Lifecycle event record written to the ledger for an entity."""

    novel_id: str
    id: int
    chapter_id: int
    entity_id: int
    event_type: LifecycleEventType
    ordinal: int
    holder_entity_id: Optional[int] = None
    tier_or_realm: Optional[str] = None
    realm_rank: Optional[int] = None
    description: str
    evidence: Optional[str] = None
