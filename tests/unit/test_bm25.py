"""Unit tests for the BM25 sparse vectors."""

import os
import subprocess
import sys

from xn.storage.bm25 import AVG_DOC_WORDS, B, K1, document_vector, query_vector, term_id, tokenize


def test_tokenize_lowercases_keeps_diacritics_and_drops_punctuation():
    assert tokenize("Hàn Lập, HÀN LẬP!") == ["hàn", "lập", "hàn", "lập"]


def test_tokenize_composes_decomposed_text():
    assert tokenize("Hàn") == tokenize("Hàn")


def test_term_id_is_32_bit_and_differs_by_diacritic():
    assert 0 <= term_id("hàn") < 2**32
    assert term_id("hàn") != term_id("han")


def test_term_id_is_stable_across_processes():
    """Python's own hash() is salted per process, which would break every stored vector."""
    code = "from xn.storage.bm25 import term_id; print(term_id('hàn'))"
    outputs = {
        subprocess.run(
            [sys.executable, "-X", "utf8", "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout.strip()
        for seed in ("1", "2")
    }
    assert outputs == {str(term_id("hàn"))}


def test_document_vector_uses_the_bm25_formula():
    indices, values = document_vector("hàn hàn lập")
    weights = dict(zip(indices, values))
    norm = K1 * (1 - B + B * 3 / AVG_DOC_WORDS)
    assert weights[term_id("hàn")] == 2 * (K1 + 1) / (2 + norm)
    assert weights[term_id("lập")] == 1 * (K1 + 1) / (1 + norm)


def test_longer_document_weighs_the_same_term_less():
    short = dict(zip(*document_vector("hàn " + "lập " * 10)))
    long = dict(zip(*document_vector("hàn " + "lập " * 600)))
    assert long[term_id("hàn")] < short[term_id("hàn")]


def test_vectors_have_sorted_unique_indices():
    for vector in (document_vector, query_vector):
        indices, values = vector("lập hàn lập môn")
        assert indices == sorted(set(indices))
        assert len(indices) == len(values) == 3


def test_query_vector_weighs_every_term_equally():
    assert query_vector("hàn hàn lập")[1] == [1.0, 1.0]


def test_empty_text_gives_empty_vectors():
    assert document_vector("") == ([], [])
    assert query_vector("...") == ([], [])
