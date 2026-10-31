"""Resolution schemas for entity resolution and adjudication."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AdjudicationDecision(BaseModel):
    """Adjudication decision model comparing mention and candidate."""

    same_entity: bool
    reason: str = Field(max_length=200)
