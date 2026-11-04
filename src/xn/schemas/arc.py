"""Arc schemas for stages S5 and S6."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BoundaryReview(BaseModel):
    """The local model's verdict on one proposed arc boundary."""

    start_chapter: int = Field(description="The chapter that opens the new arc")
    reason: str = Field(max_length=300)
