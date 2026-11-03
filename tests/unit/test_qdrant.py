"""Unit tests for the Qdrant store, using the in-memory client (payload indexes have no effect there)."""

from types import SimpleNamespace
import warnings

import pytest
from qdrant_client import QdrantClient, models

from xn.storage.qdrant import DENSE_VECTOR, SPARSE_VECTOR, ChunkPoint, QdrantStore, point_id

SIZE = 4


@pytest.fixture
def client():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        yield QdrantClient(":memory:")


@pytest.fixture
def store(client):
    s = QdrantStore(client, chunks_collection="c", arcs_collection="a", dense_size=SIZE)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        s.ensure_collections()
    return s


def chunk(chapter_id=1, chunk_no=0, dense=(1.0, 0.0, 0.0, 0.0), term=7):
    return ChunkPoint(
        kind="text",
        chapter_id=chapter_id,
        chunk_no=chunk_no,
        arc_id=None,
        entity_ids=[1],
        text="x",
        dense=list(dense),
        sparse_indices=[term],
        sparse_values=[1.0],
    )


def test_point_id_is_deterministic():
    assert point_id("pntt1", "text", 3, 2) == point_id("pntt1", "text", 3, 2)


@pytest.mark.parametrize(
    "args",
    [("pntt2", "text", 3, 2), ("pntt1", "summary", 3, 2), ("pntt1", "text", 4, 2), ("pntt1", "text", 3, 1)],
)
def test_point_id_differs_on_any_input(args):
    assert point_id(*args) != point_id("pntt1", "text", 3, 2)


def test_point_id_does_not_collide_across_field_boundaries():
    assert point_id("a", "text", 12, 3) != point_id("a", "text", 1, 23)


def test_from_config_reads_the_qdrant_block(client):
    config = {
        "qdrant": {"url": "unused", "collections": {"chunks": "c", "arcs": "a"}, "dense_size": SIZE}
    }
    store = QdrantStore.from_config(config, client=client)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        store.ensure_collections()
    assert client.collection_exists("c")
    assert client.collection_exists("a")


def test_ensure_collections_twice_keeps_the_points(store, client):
    store.upsert_chunks(novel_id="n", points=[chunk()])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        store.ensure_collections()
    assert client.count("c").count == 1


def test_vector_config(store, client):
    chunks = client.get_collection("c").config.params
    assert chunks.vectors[DENSE_VECTOR].size == SIZE
    assert chunks.vectors[DENSE_VECTOR].distance == models.Distance.COSINE
    assert chunks.sparse_vectors[SPARSE_VECTOR].modifier == models.Modifier.IDF
    arcs = client.get_collection("a").config.params
    assert arcs.vectors[DENSE_VECTOR].size == SIZE
    assert not arcs.sparse_vectors


def test_dense_size_mismatch_raises(store, client):
    wrong = QdrantStore(client, chunks_collection="c", arcs_collection="a", dense_size=SIZE + 1)
    with pytest.raises(ValueError, match="dense size"):
        wrong.ensure_collections()


def test_upsert_twice_leaves_one_point(store, client):
    store.upsert_chunks(novel_id="n", points=[chunk()])
    store.upsert_chunks(novel_id="n", points=[chunk()])
    assert client.count("c").count == 1


@pytest.mark.parametrize("novel_id", [None, ""])
def test_upsert_requires_novel_id(store, novel_id):
    with pytest.raises(ValueError):
        store.upsert_chunks(novel_id=novel_id, points=[chunk()])


@pytest.mark.parametrize(
    ("novel_id", "chapter_max"), [(None, 1), ("", 1), ("n", None), ("n", True), ("n", "1")]
)
def test_query_rejects_missing_bounds(store, novel_id, chapter_max):
    with pytest.raises(ValueError):
        store.query_chunks(
            novel_id=novel_id, chapter_max=chapter_max, using=DENSE_VECTOR, query=[1.0] * SIZE, limit=5
        )


def test_query_rejects_unknown_vector(store):
    with pytest.raises(ValueError, match="using"):
        store.query_chunks(novel_id="n", chapter_max=1, using="other", query=[1.0] * SIZE, limit=5)


def test_query_applies_tenant_and_chapter_bound(store):
    store.upsert_chunks(novel_id="a", points=[chunk(chapter_id=1), chunk(chapter_id=2), chunk(chapter_id=3)])
    store.upsert_chunks(novel_id="b", points=[chunk(chapter_id=1)])
    hits = store.query_chunks(
        novel_id="a", chapter_max=2, using=DENSE_VECTOR, query=[1.0, 0.0, 0.0, 0.0], limit=10
    )
    assert sorted((h.payload["novel_id"], h.payload["chapter_id"]) for h in hits) == [("a", 1), ("a", 2)]


def sparse(term=7):
    return models.SparseVector(indices=[term], values=[1.0])


def test_hybrid_applies_tenant_and_chapter_bound(store):
    store.upsert_chunks(novel_id="a", points=[chunk(chapter_id=c, term=7) for c in (1, 2, 3)])
    store.upsert_chunks(novel_id="b", points=[chunk(chapter_id=1, term=7)])
    hits = store.hybrid_chunks(
        novel_id="a", chapter_max=2, dense=[1.0, 0.0, 0.0, 0.0], sparse=sparse(), prefetch_limit=40, limit=30
    )
    assert sorted((h.payload["novel_id"], h.payload["chapter_id"]) for h in hits) == [("a", 1), ("a", 2)]


def test_hybrid_fuses_dense_only_and_sparse_only_hits(store):
    store.upsert_chunks(
        novel_id="a",
        points=[
            chunk(chapter_id=1, dense=(1.0, 0.0, 0.0, 0.0), term=1),
            chunk(chapter_id=2, dense=(0.0, 1.0, 0.0, 0.0), term=7),
        ],
    )
    hits = store.hybrid_chunks(
        novel_id="a", chapter_max=5, dense=[1.0, 0.0, 0.0, 0.0], sparse=sparse(7), prefetch_limit=1, limit=30
    )
    assert sorted(h.payload["chapter_id"] for h in hits) == [1, 2]


def test_hybrid_puts_the_bound_filter_in_every_leg():
    class Recorder:
        def query_points(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(points=[])

    client = Recorder()
    store = QdrantStore(client, chunks_collection="c", arcs_collection="a", dense_size=SIZE)
    store.hybrid_chunks(
        novel_id="a", chapter_max=9, dense=[1.0] * SIZE, sparse=sparse(), prefetch_limit=40, limit=30
    )
    expected = QdrantStore._bound_filter(novel_id="a", chapter_max=9)
    legs = client.kwargs["prefetch"]
    assert [leg.using for leg in legs] == [DENSE_VECTOR, SPARSE_VECTOR]
    assert all(leg.filter == expected and leg.limit == 40 for leg in legs)
    assert client.kwargs["query_filter"] == expected
    assert client.kwargs["query"] == models.FusionQuery(fusion=models.Fusion.RRF)
    assert client.kwargs["limit"] == 30


@pytest.mark.parametrize("novel_id,chapter_max", [(None, 1), ("", 1), ("a", None), ("a", True)])
def test_hybrid_rejects_missing_bounds(store, novel_id, chapter_max):
    with pytest.raises(ValueError):
        store.hybrid_chunks(
            novel_id=novel_id, chapter_max=chapter_max, dense=[1.0] * SIZE, sparse=sparse(), prefetch_limit=1, limit=1
        )
