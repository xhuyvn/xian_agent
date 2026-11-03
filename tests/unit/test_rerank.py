"""Unit tests for the reranker wrapper, with a fake cross-encoder."""

import pytest

from xn.rerank import CrossEncoderReranker, RerankError


class FakeEncoder:
    def __init__(self, scores=None, error=None):
        self.scores = scores
        self.error = error
        self.calls = []

    def predict(self, pairs, batch_size, show_progress_bar):
        self.calls.append((pairs, batch_size))
        if self.error:
            raise self.error
        return self.scores if self.scores is not None else [len(p) / 10 for _, p in pairs]


def make(encoder, loads=None):
    def loader(name, device, max_length):
        if loads is not None:
            loads.append((name, device, max_length))
        return encoder

    return CrossEncoderReranker(model="m", device="cuda", max_length=512, batch_size=4, loader=loader)


def test_scores_pairs_in_order_and_loads_once():
    loads = []
    encoder = FakeEncoder()
    reranker = make(encoder, loads)
    assert reranker.score("q", ["a", "bbb"]) == [0.1, 0.3]
    assert reranker.score("q", ["cc"]) == [0.2]
    assert loads == [("m", "cuda", 512)]
    assert encoder.calls[0] == ([("q", "a"), ("q", "bbb")], 4)


def test_no_passages_does_not_load():
    loads = []
    assert make(FakeEncoder(), loads).score("q", []) == []
    assert loads == []


def test_model_error_becomes_rerank_error():
    with pytest.raises(RerankError):
        make(FakeEncoder(error=RuntimeError("CUDA out of memory"))).score("q", ["a"])


def test_load_error_becomes_rerank_error():
    def loader(name, device, max_length):
        raise OSError("no such model")

    reranker = CrossEncoderReranker(model="m", device="cuda", max_length=512, batch_size=4, loader=loader)
    with pytest.raises(RerankError):
        reranker.score("q", ["a"])


def test_wrong_score_count_is_an_error():
    with pytest.raises(RerankError):
        make(FakeEncoder(scores=[0.5])).score("q", ["a", "b"])


def test_from_config_reads_the_reranker_block():
    config = {"models": {"reranker": {"name": "r", "device": "cpu", "max_length": 256, "batch_size": 2}}}
    reranker = CrossEncoderReranker.from_config(config)
    assert (reranker.model, reranker._device, reranker._max_length, reranker._batch_size) == ("r", "cpu", 256, 2)
