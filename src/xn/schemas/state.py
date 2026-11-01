"""Query graph state and the models carried through it."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, Optional, TypedDict

from pydantic import BaseModel, Field


class RouterDecision(BaseModel):
    """Intent and entity mentions read from a question by the router."""

    intent: Literal["timeline", "deep_dive", "factual_qa", "compare"]
    mentions: list[str] = Field(default_factory=list, max_length=4)
    chapter_hint: Optional[int] = None
    aspect: Optional[str] = None


class EvidenceItem(BaseModel):
    """One ledger event retrieved for a query, with chapter_id <= chapter_max."""

    event_id: int
    chapter_id: int
    entity_id: int
    entity_name: str
    event_type: str
    description: str
    tier_or_realm: Optional[str] = None
    evidence: Optional[str] = None

    def cloud_text(self) -> str:
        """Return the text sent to the cloud model; the chapter quote stays local."""
        text = f"[{self.event_type}] {self.entity_name}: {self.description}"
        if self.tier_or_realm:
            text += f" (cảnh giới/phẩm giai: {self.tier_or_realm})"
        return text


class EvidencePack(BaseModel):
    """Evidence for one query, ranked so the most useful items come first."""

    items: list[EvidenceItem] = Field(default_factory=list)
    tokens: int = 0


class Answer(BaseModel):
    """Synthesized answer: Markdown with chapter citations."""

    markdown: str
    citations: list[int] = Field(default_factory=list)


class QueryState(TypedDict, total=False):
    """State of one query; novel_id comes from the caller and is never inferred by the LLM."""

    novel_id: str
    chapter_max: int
    until: Optional[int]
    mode: Literal["compact", "long_context"]
    query: str
    thread_id: str
    decision: RouterDecision
    entity_ids: list[int]
    unresolved_mentions: list[str]
    suggestions: dict[str, list[str]]
    evidence: Optional[EvidencePack]
    answer: Optional[Answer]
    grounding_ratio: Optional[float]
    refusal: Optional[str]
    errors: Annotated[list[str], operator.add]
