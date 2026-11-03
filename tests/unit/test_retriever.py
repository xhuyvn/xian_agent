"""Unit tests for the hybrid retriever, with a fake store, encoder and reranker."""

from types import SimpleNamespace
from typing import Any

import pytest

from xn.storage import bm25
from xn.storage.retriever import HybridRetriever


class FakeStore:
    def __init__(self, hits: list[Any]) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    def hybrid_chunks(self, **kwargs: Any) -> list[Any]:
        self.calls.append(kwargs)
        return self.hits


class FakeEncoder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[0.5, 0.5] for _ in texts]


class FakeReranker:
    """Scores a passage by the number in its text, so the order is easy to read."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, passages: list[str]) -> list[float]:
        self.calls.append((query, passages))
        return [int(p.split()[-1]) / 100 for p in passages]


def hit(chapter_id: int, n: int, novel_id: str = "a", entity_ids: tuple[int, ...] = (1,)) -> Any:
    return SimpleNamespace(
        payload={
            "novel_id": novel_id,
            "chapter_id": chapter_id,
            "chunk_no": n,
            "kind": "text",
            "text": f"đoạn {n}",
            "entity_ids": list(entity_ids),
        }
    )


def make(hits: list[Any], top_k: int = 2) -> tuple[HybridRetriever, FakeStore, FakeEncoder, FakeReranker]:
    store, encoder, reranker = FakeStore(hits), FakeEncoder(), FakeReranker()
    retriever = HybridRetriever(
        store=store, encoder=encoder, reranker=reranker, prefetch_k=40, fused_k=30, top_k=top_k  # type: ignore[arg-type]
    )
    return retriever, store, encoder, reranker


def test_reranks_the_fused_list_and_keeps_top_k() -> None:
    retriever, store, encoder, reranker = make([hit(1, 10), hit(2, 90), hit(3, 50)])
    chunks = retriever.retrieve(novel_id="a", chapter_max=5, query="Hàn Lập")
    assert [(c.chapter_id, c.score) for c in chunks] == [(2, 0.9), (3, 0.5)]
    assert encoder.calls == [["Hàn Lập"]]
    assert reranker.calls == [("Hàn Lập", ["đoạn 10", "đoạn 90", "đoạn 50"])]
    assert set(retriever.last_timings) == {"encode", "fuse", "rerank"}


def test_passes_the_bound_and_both_vectors_to_the_store() -> None:
    retriever, store, _, _ = make([])
    retriever.retrieve(novel_id="a", chapter_max=7, query="Hàn Lập")
    call = store.calls[0]
    indices, values = bm25.query_vector("Hàn Lập")
    assert (call["novel_id"], call["chapter_max"]) == ("a", 7)
    assert call["dense"] == [0.5, 0.5]
    assert (call["sparse"].indices, call["sparse"].values) == (indices, values)
    assert (call["prefetch_limit"], call["limit"]) == (40, 30)


def test_drops_any_hit_outside_the_bound_before_reranking() -> None:
    retriever, _, _, reranker = make([hit(9, 90), hit(1, 80, novel_id="b"), hit(2, 10)], top_k=8)
    chunks = retriever.retrieve(novel_id="a", chapter_max=5, query="Hàn Lập")
    assert [c.chapter_id for c in chunks] == [2]
    assert reranker.calls[0][1] == ["đoạn 10"]


def test_entity_search_filters_tags_and_overrides_top_k() -> None:
    retriever, store, _, reranker = make(
        [hit(1, 90, entity_ids=(2,)), hit(2, 80, entity_ids=(1, 2)), hit(3, 70), hit(4, 60, entity_ids=(2,))]
    )
    chunks = retriever.retrieve(novel_id="a", chapter_max=5, query="Hàn Lập", entity_id=2, top_k=3)
    assert store.calls[0]["entity_id"] == 2
    # The untagged chunk is dropped before reranking, as a guard against a store bug.
    assert reranker.calls[0][1] == ["đoạn 90", "đoạn 80", "đoạn 60"]
    assert [(c.chapter_id, c.entity_id) for c in chunks] == [(1, 2), (2, 2), (4, 2)]


def test_search_without_entity_leaves_it_unset() -> None:
    retriever, store, _, _ = make([hit(1, 90)])
    chunks = retriever.retrieve(novel_id="a", chapter_max=5, query="Hàn Lập")
    assert store.calls[0]["entity_id"] is None
    assert chunks[0].entity_id is None


def test_no_hits_skips_the_reranker() -> None:
    retriever, _, _, reranker = make([])
    assert retriever.retrieve(novel_id="a", chapter_max=5, query="Hàn Lập") == []
    assert reranker.calls == []


@pytest.mark.parametrize("query", ["", "  ?! "])
def test_a_query_without_words_makes_no_calls(query: str) -> None:
    retriever, store, encoder, _ = make([hit(1, 10)])
    assert retriever.retrieve(novel_id="a", chapter_max=5, query=query) == []
    assert store.calls == [] and encoder.calls == []


def test_from_config_builds_the_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    # QdrantClient checks the server version on creation; no network in unit tests.
    monkeypatch.setattr("xn.storage.qdrant.QdrantClient", lambda url: SimpleNamespace(url=url))
    config = {
        "models": {
            "embedder": {"name": "bge-m3", "base_url": "http://x", "batch_size": 8, "query_options": {"num_gpu": 0}},
            "reranker": {"name": "r", "device": "cuda", "max_length": 512, "batch_size": 16},
        },
        "qdrant": {"url": "http://127.0.0.1:1", "collections": {"chunks": "c", "arcs": "a"}, "dense_size": 1024},
        "retrieval": {"prefetch_k": 40, "fused_k": 30, "top_k": 8},
    }
    retriever = HybridRetriever.from_config(config)
    assert (retriever._prefetch_k, retriever._fused_k, retriever._top_k) == (40, 30, 8)
    assert retriever._encoder._options == {"num_gpu": 0}
    assert retriever._reranker.model == "r"
    assert retriever._store._client.url == "http://127.0.0.1:1"
