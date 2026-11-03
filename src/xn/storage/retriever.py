"""Hybrid retriever: BGE-M3 dense + BM25 sparse, RRF in Qdrant, then the cross-encoder reranker."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Protocol

from qdrant_client import models

from xn.embed import OllamaEmbedder
from xn.rerank import CrossEncoderReranker
from xn.schemas.state import ChunkEvidence
from xn.storage import bm25
from xn.storage.qdrant import QdrantStore

logger = logging.getLogger(__name__)


class Encoder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Reranker(Protocol):
    def score(self, query: str, passages: list[str]) -> list[float]: ...


class HybridRetriever:
    """Top chunks for a question, every one within the novel and chapter bound."""

    def __init__(
        self,
        *,
        store: QdrantStore,
        encoder: Encoder,
        reranker: Reranker,
        prefetch_k: int,
        fused_k: int,
        top_k: int,
    ) -> None:
        self._store = store
        self._encoder = encoder
        self._reranker = reranker
        self._prefetch_k = prefetch_k
        self._fused_k = fused_k
        self._top_k = top_k
        # Seconds spent per step on the last call, for the live check and logs.
        self.last_timings: dict[str, float] = {}

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> HybridRetriever:
        retrieval = config["retrieval"]
        return cls(
            store=QdrantStore.from_config(config),
            encoder=OllamaEmbedder.from_config(config, query=True),
            reranker=CrossEncoderReranker.from_config(config),
            prefetch_k=retrieval["prefetch_k"],
            fused_k=retrieval["fused_k"],
            top_k=retrieval["top_k"],
        )

    def retrieve(
        self,
        *,
        novel_id: str,
        chapter_max: int,
        query: str,
        entity_id: Optional[int] = None,
        top_k: Optional[int] = None,
    ) -> list[ChunkEvidence]:
        """Fuse dense and sparse hits, rerank the fused list and keep the best top_k.

        With entity_id, only chunks tagged with that entity are searched; top_k overrides the
        configured count.
        """
        self.last_timings = {}
        indices, values = bm25.query_vector(query)
        if not indices:
            return []

        start = time.perf_counter()
        dense = self._encoder.embed([query])[0]
        self.last_timings["encode"] = time.perf_counter() - start

        start = time.perf_counter()
        hits = self._store.hybrid_chunks(
            novel_id=novel_id,
            chapter_max=chapter_max,
            dense=dense,
            sparse=models.SparseVector(indices=indices, values=values),
            prefetch_limit=self._prefetch_k,
            limit=self._fused_k,
            entity_id=entity_id,
        )
        self.last_timings["fuse"] = time.perf_counter() - start

        # Qdrant already filtered; this check keeps a store bug from becoming a spoiler.
        kept = [
            h for h in hits
            if h.payload["novel_id"] == novel_id
            and h.payload["chapter_id"] <= chapter_max
            and (entity_id is None or entity_id in h.payload.get("entity_ids", []))
        ]
        if len(kept) != len(hits):
            logger.error(
                "Dropped %d chunks outside %s <= %d (entity %s)",
                len(hits) - len(kept), novel_id, chapter_max, entity_id,
            )
        if not kept:
            return []

        start = time.perf_counter()
        scores = self._reranker.score(query, [h.payload["text"] for h in kept])
        self.last_timings["rerank"] = time.perf_counter() - start

        ranked = sorted(zip(kept, scores), key=lambda pair: pair[1], reverse=True)
        return [
            ChunkEvidence(
                chapter_id=hit.payload["chapter_id"],
                kind=hit.payload["kind"],
                chunk_no=hit.payload["chunk_no"],
                text=hit.payload["text"],
                score=score,
                entity_id=entity_id,
            )
            for hit, score in ranked[: self._top_k if top_k is None else top_k]
        ]
