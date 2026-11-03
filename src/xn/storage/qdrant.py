"""Qdrant storage layer: shared collections, tenant isolation and the spoiler bound."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID, uuid5

from qdrant_client import QdrantClient, models

from xn.storage.repository import _validate_novel_id, story_read

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "sparse"
POINT_NAMESPACE = UUID("6f1d3a52-8c47-5e0b-9a26-3b7e4d10c2f8")

_TENANT_INDEX = models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True)
_RANGE_INDEX = models.IntegerIndexParams(
    type=models.IntegerIndexType.INTEGER, range=True, lookup=True
)
_CHUNK_INDEXES: dict[str, Any] = {
    "novel_id": _TENANT_INDEX,
    "chapter_id": _RANGE_INDEX,
    "arc_id": models.PayloadSchemaType.INTEGER,
    "kind": models.PayloadSchemaType.KEYWORD,
    "entity_ids": models.PayloadSchemaType.INTEGER,
}
_ARC_INDEXES: dict[str, Any] = {
    "novel_id": _TENANT_INDEX,
    "arc_id": models.PayloadSchemaType.INTEGER,
    "start_chapter": models.PayloadSchemaType.INTEGER,
    "end_chapter": models.PayloadSchemaType.INTEGER,
}


def point_id(novel_id: str, kind: str, chapter_id: int, chunk_no: int) -> str:
    """Return the deterministic point ID, so re-embedding upserts and never duplicates."""
    return str(uuid5(POINT_NAMESPACE, f"{novel_id}:{kind}:{chapter_id}:{chunk_no}"))


@dataclass(frozen=True)
class ChunkPoint:
    """One chunk with its dense and sparse vectors."""

    kind: str
    chapter_id: int
    chunk_no: int
    arc_id: int | None
    entity_ids: list[int]
    text: str
    dense: list[float]
    sparse_indices: list[int]
    sparse_values: list[float]


class QdrantStore:
    """Guarded access to the shared `xn_chunks` and `xn_arcs` collections."""

    def __init__(
        self,
        client: QdrantClient,
        *,
        chunks_collection: str,
        arcs_collection: str,
        dense_size: int,
    ) -> None:
        self._client = client
        self._chunks = chunks_collection
        self._arcs = arcs_collection
        self._dense_size = dense_size

    @classmethod
    def from_config(cls, config: dict[str, Any], client: QdrantClient | None = None) -> QdrantStore:
        qdrant = config["qdrant"]
        return cls(
            client if client is not None else QdrantClient(url=qdrant["url"]),
            chunks_collection=qdrant["collections"]["chunks"],
            arcs_collection=qdrant["collections"]["arcs"],
            dense_size=qdrant["dense_size"],
        )

    def ensure_collections(self) -> None:
        """Create missing collections and indexes. Never deletes or recreates anything."""
        self._ensure_collection(
            self._chunks,
            sparse={SPARSE_VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF)},
            indexes=_CHUNK_INDEXES,
        )
        self._ensure_collection(self._arcs, sparse=None, indexes=_ARC_INDEXES)

    def _ensure_collection(
        self,
        name: str,
        *,
        sparse: dict[str, models.SparseVectorParams] | None,
        indexes: dict[str, Any],
    ) -> None:
        if self._client.collection_exists(name):
            info = self._client.get_collection(name)
            actual = info.config.params.vectors[DENSE_VECTOR].size
            if actual != self._dense_size:
                raise ValueError(
                    f"collection {name} has dense size {actual}, config says {self._dense_size}"
                )
            existing = set(info.payload_schema)
        else:
            self._client.create_collection(
                collection_name=name,
                vectors_config={
                    DENSE_VECTOR: models.VectorParams(
                        size=self._dense_size, distance=models.Distance.COSINE
                    )
                },
                sparse_vectors_config=sparse,
            )
            existing = set()
        for field, schema in indexes.items():
            if field not in existing:
                self._client.create_payload_index(
                    collection_name=name, field_name=field, field_schema=schema
                )

    def upsert_chunks(self, *, novel_id: str, points: list[ChunkPoint]) -> None:
        _validate_novel_id(novel_id)
        if not points:
            return
        self._client.upsert(
            collection_name=self._chunks,
            points=[self._to_point(novel_id, point) for point in points],
            wait=True,
        )

    def delete_chapter(self, *, novel_id: str, chapter_id: int) -> None:
        """Remove one chapter's points, so a shorter re-chunk leaves none behind."""
        _validate_novel_id(novel_id)
        self._client.delete(
            collection_name=self._chunks,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(key="novel_id", match=models.MatchValue(value=novel_id)),
                        models.FieldCondition(key="chapter_id", match=models.MatchValue(value=chapter_id)),
                    ]
                )
            ),
            wait=True,
        )

    @staticmethod
    def _to_point(novel_id: str, point: ChunkPoint) -> models.PointStruct:
        return models.PointStruct(
            id=point_id(novel_id, point.kind, point.chapter_id, point.chunk_no),
            vector={
                DENSE_VECTOR: point.dense,
                SPARSE_VECTOR: models.SparseVector(
                    indices=point.sparse_indices, values=point.sparse_values
                ),
            },
            payload={
                "novel_id": novel_id,
                "chapter_id": point.chapter_id,
                "chunk_no": point.chunk_no,
                "arc_id": point.arc_id,
                "kind": point.kind,
                "entity_ids": point.entity_ids,
                "text": point.text,
            },
        )

    @story_read
    def query_chunks(
        self,
        *,
        novel_id: str,
        chapter_max: int,
        using: str,
        query: list[float] | models.SparseVector,
        limit: int,
    ) -> list[models.ScoredPoint]:
        if using not in (DENSE_VECTOR, SPARSE_VECTOR):
            raise ValueError(f"using must be {DENSE_VECTOR} or {SPARSE_VECTOR}, got {using}")
        return self._client.query_points(
            collection_name=self._chunks,
            query=query,
            using=using,
            query_filter=self._bound_filter(novel_id=novel_id, chapter_max=chapter_max),
            limit=limit,
            with_payload=True,
        ).points

    @story_read
    def hybrid_chunks(
        self,
        *,
        novel_id: str,
        chapter_max: int,
        dense: list[float],
        sparse: models.SparseVector,
        prefetch_limit: int,
        limit: int,
        entity_id: Optional[int] = None,
    ) -> list[models.ScoredPoint]:
        """Dense and sparse prefetch fused by RRF; each leg and the fusion carry the bound filter.

        With entity_id, only chunks tagged with that entity match, in every leg as well.
        """
        bound = self._bound_filter(novel_id=novel_id, chapter_max=chapter_max)
        if entity_id is not None:
            bound.must.append(
                models.FieldCondition(key="entity_ids", match=models.MatchValue(value=entity_id))
            )
        return self._client.query_points(
            collection_name=self._chunks,
            prefetch=[
                models.Prefetch(query=dense, using=DENSE_VECTOR, filter=bound, limit=prefetch_limit),
                models.Prefetch(query=sparse, using=SPARSE_VECTOR, filter=bound, limit=prefetch_limit),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=bound,
            limit=limit,
            with_payload=True,
        ).points

    @staticmethod
    def _bound_filter(*, novel_id: str, chapter_max: int) -> models.Filter:
        """The tenant and spoiler filter. Every chunk read goes through this."""
        return models.Filter(
            must=[
                models.FieldCondition(key="novel_id", match=models.MatchValue(value=novel_id)),
                models.FieldCondition(key="chapter_id", range=models.Range(lte=chapter_max)),
            ]
        )
