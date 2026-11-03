"""Query graph state and the models carried through it."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, field_validator

from xn.text import normalize_alias


class RouterDecision(BaseModel):
    """Intent and entity mentions read from a question by the router."""

    intent: Literal["timeline", "deep_dive", "factual_qa", "compare"]
    mentions: list[str] = Field(default_factory=list, max_length=4)
    chapter_hint: Optional[int] = None
    aspect: Optional[str] = None

    @field_validator("mentions", mode="before")
    @classmethod
    def _drop_repeats(cls, value: Any) -> Any:
        """Drop repeated names, which local models produce and would break the size limit."""
        if not isinstance(value, list):
            return value
        unique = {normalize_alias(m) if isinstance(m, str) else m: m for m in reversed(value)}
        return list(reversed(unique.values()))


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


class ChunkEvidence(BaseModel):
    """One retrieved chunk with chapter_id <= chapter_max and its reranker score in [0, 1]."""

    chapter_id: int
    kind: Literal["text", "summary"]
    chunk_no: int
    text: str
    score: float


class EvidencePack(BaseModel):
    """Evidence for one query, ranked so the most useful items come first."""

    items: list[EvidenceItem] = Field(default_factory=list)
    chunks: list[ChunkEvidence] = Field(default_factory=list)
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
