"""Lifecycle event types for entity tracking across chapters."""

from __future__ import annotations

from enum import Enum


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
