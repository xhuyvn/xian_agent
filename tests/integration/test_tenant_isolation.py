"""Tenant and spoiler isolation against the real Qdrant from docker-compose.yml.

Skips when the server is unreachable, unless XN_REQUIRE_QDRANT=1, which makes that a failure.
"""

import os
import uuid

import pytest
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException

from xn.storage.qdrant import DENSE_VECTOR, SPARSE_VECTOR, ChunkPoint, QdrantStore

pytestmark = pytest.mark.integration

SIZE = 4
TARGET = [1.0, 0.0, 0.0, 0.0]
NEAR = [0.9, 0.1, 0.0, 0.0]
TERM = 7
SPARSE_QUERY = models.SparseVector(indices=[TERM], values=[1.0])


@pytest.fixture(scope="module")
def client():
    url = os.environ.get("XN_QDRANT_URL", "http://127.0.0.1:6233")
    c = QdrantClient(url=url, check_compatibility=False)
    try:
        c.get_collections()
    except ResponseHandlingException as exc:
        if os.environ.get("XN_REQUIRE_QDRANT") == "1":
            pytest.fail(f"Qdrant unreachable at {url}: {exc}")
        pytest.skip(f"Qdrant unreachable at {url}")
    return c


@pytest.fixture
def store(client):
    run = uuid.uuid4().hex
    chunks, arcs = f"test_xn_chunks_{run}", f"test_xn_arcs_{run}"
    s = QdrantStore(client, chunks_collection=chunks, arcs_collection=arcs, dense_size=SIZE)
    s.ensure_collections()
    s.names = (chunks, arcs)
    yield s
    client.delete_collection(chunks)
    client.delete_collection(arcs)


def chunk(chapter_id, dense, weight=1.0):
    return ChunkPoint(
        kind="text",
        chapter_id=chapter_id,
        chunk_no=0,
        arc_id=None,
        entity_ids=[1],
        text=f"chapter {chapter_id}",
        dense=dense,
        sparse_indices=[TERM],
        sparse_values=[weight],
    )


@pytest.fixture
def seeded(store):
    # Novel B holds the closest match, and novel A's chapter 6 beats its chapters 1-5.
    store.upsert_chunks(novel_id="a", points=[chunk(c, NEAR, 0.5) for c in (1, 5)] + [chunk(6, TARGET)])
    store.upsert_chunks(novel_id="b", points=[chunk(1, TARGET, 9.0), chunk(2, TARGET, 9.0)])
    return store


@pytest.mark.parametrize(
    ("using", "query"), [(DENSE_VECTOR, TARGET), (SPARSE_VECTOR, SPARSE_QUERY)], ids=["dense", "sparse"]
)
class TestIsolation:
    def test_novel_a_never_returns_novel_b(self, seeded, using, query):
        hits = seeded.query_chunks(novel_id="a", chapter_max=100, using=using, query=query, limit=50)
        assert {h.payload["novel_id"] for h in hits} == {"a"}
        assert len(hits) == 3

    def test_the_other_tenant_is_really_there(self, seeded, using, query):
        hits = seeded.query_chunks(novel_id="b", chapter_max=100, using=using, query=query, limit=50)
        assert {h.payload["novel_id"] for h in hits} == {"b"}
        assert len(hits) == 2

    def test_nothing_past_chapter_max_and_boundary_included(self, seeded, using, query):
        hits = seeded.query_chunks(novel_id="a", chapter_max=5, using=using, query=query, limit=50)
        assert sorted(h.payload["chapter_id"] for h in hits) == [1, 5]


def test_payload_indexes_exist(store, client):
    chunks_name, arcs_name = store.names
    chunks = client.get_collection(chunks_name).payload_schema
    assert set(chunks) == {"novel_id", "chapter_id", "arc_id", "kind", "entity_ids"}
    assert chunks["novel_id"].params.is_tenant is True
    assert chunks["chapter_id"].params.range is True
    arcs = client.get_collection(arcs_name).payload_schema
    assert set(arcs) == {"novel_id", "arc_id", "start_chapter", "end_chapter"}
    assert arcs["novel_id"].params.is_tenant is True


def test_collections_use_the_agreed_vectors(store, client):
    chunks_name, arcs_name = store.names
    params = client.get_collection(chunks_name).config.params
    assert params.vectors[DENSE_VECTOR].distance == models.Distance.COSINE
    assert params.sparse_vectors[SPARSE_VECTOR].modifier == models.Modifier.IDF
    assert client.get_collection(arcs_name).config.params.vectors[DENSE_VECTOR].size == SIZE


def test_second_ensure_and_reupsert_change_nothing(seeded, client):
    chunks_name, _ = seeded.names
    before = client.count(chunks_name, exact=True).count
    seeded.ensure_collections()
    seeded.upsert_chunks(novel_id="b", points=[chunk(1, TARGET, 9.0)])
    assert client.count(chunks_name, exact=True).count == before == 5
