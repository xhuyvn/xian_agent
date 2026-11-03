"""Unit tests for the Ollama embed client, using a mock transport."""

import json

import httpx
import pytest

from xn.embed import EmbedError, OllamaEmbedder

SIZE = 3


def make(handler, batch_size=2):
    client = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return OllamaEmbedder(
        base_url="http://ollama",
        model="bge-m3:latest",
        batch_size=batch_size,
        dense_size=SIZE,
        client=client,
    )


def echo_handler(calls):
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200, json={"embeddings": [[float(len(t)), 0.0, 0.0] for t in body["input"]]}
        )

    return handler


def test_embed_batches_in_order_and_refuses_truncation():
    calls = []
    vectors = make(echo_handler(calls)).embed(["a", "bb", "ccc"])
    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0]
    assert [c["input"] for c in calls] == [["a", "bb"], ["ccc"]]
    assert all(c["truncate"] is False and c["model"] == "bge-m3:latest" for c in calls)


def test_embed_with_no_texts_makes_no_call():
    calls = []
    assert make(echo_handler(calls)).embed([]) == []
    assert calls == []


@pytest.mark.parametrize(
    "payload",
    [{"embeddings": [[1.0, 0.0, 0.0]]}, {"embeddings": [[1.0], [2.0]]}, {}],
    ids=["too-few", "wrong-size", "missing"],
)
def test_embed_rejects_a_bad_response(payload):
    with pytest.raises(EmbedError):
        make(lambda r: httpx.Response(200, json=payload)).embed(["a", "b"])


def test_http_error_becomes_embed_error():
    with pytest.raises(EmbedError, match="500"):
        make(lambda r: httpx.Response(500)).embed(["a"])


def test_connection_error_becomes_embed_error():
    def refuse(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(EmbedError, match="refused"):
        make(refuse).embed(["a"])


def test_non_json_body_becomes_embed_error():
    with pytest.raises(EmbedError):
        make(lambda r: httpx.Response(200, text="not json")).embed(["a"])


def test_other_loaded_models_leaves_out_the_embedder():
    payload = {"models": [{"name": "bge-m3:latest"}, {"name": "qwen38-udiq4-hui-10k:latest"}]}
    assert make(lambda r: httpx.Response(200, json=payload)).other_loaded_models() == [
        "qwen38-udiq4-hui-10k:latest"
    ]


def test_other_loaded_models_is_empty_when_ollama_is_idle():
    assert make(lambda r: httpx.Response(200, json={"models": []})).other_loaded_models() == []


def test_from_config_reads_the_embedder_settings():
    config = {
        "models": {"embedder": {"name": "m", "base_url": "http://x", "batch_size": 8}},
        "qdrant": {"dense_size": 1024},
    }
    embedder = OllamaEmbedder.from_config(config)
    assert embedder.model == "m"
    assert embedder._batch_size == 8
    assert embedder._dense_size == 1024


def test_query_encoder_sends_the_query_options():
    calls = []
    client = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(echo_handler(calls)))
    config = {
        "models": {
            "embedder": {
                "name": "m",
                "base_url": "http://ollama",
                "batch_size": 8,
                "query_options": {"num_gpu": 0},
            }
        },
        "qdrant": {"dense_size": SIZE},
    }
    OllamaEmbedder.from_config(config, client, query=True).embed(["q"])
    OllamaEmbedder.from_config(config, client).embed(["d"])
    assert calls[0]["options"] == {"num_gpu": 0}
    assert "options" not in calls[1]
