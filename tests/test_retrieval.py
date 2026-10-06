"""Unit tests for retrieval pipeline pure logic (rank fusion, filename detection, reranking)."""

from unittest.mock import MagicMock

import pytest
from qdrant_client import models

# conftest.py stubs sentence_transformers before this module is imported,
# so api.retrieval loads without loading a model.
import api.retrieval as retrieval
from api.retrieval import (
    Chunk,
    _extract_filenames,
    _fuse,
    _known_filenames,
    hybrid_recall,
    keyword_recall,
    rerank,
    retrieve_best,
    vector_recall,
)
from common.qdrant import DENSE_VECTOR, SPARSE_VECTOR


def _chunk(id: str, text: str, score: float = 1.0) -> Chunk:
    return Chunk(id=id, score=score, payload={"text": text})


# --- _fuse (Reciprocal Rank Fusion) ---


def test_fuse_ranks_chunks_found_by_both_searches_first():
    vector = [_chunk("a", "alpha"), _chunk("b", "beta")]
    keyword = [_chunk("c", "gamma"), _chunk("b", "beta")]
    assert [c.payload["text"] for c in _fuse(vector, keyword)] == ["beta", "alpha", "gamma"]


def test_fuse_counts_identical_text_once_across_ids():
    # The same file indexed under two paths yields two point ids with identical text.
    vector = [_chunk("a", "same text"), _chunk("b", "same text"), _chunk("c", "other")]
    fused = _fuse(vector)
    assert [c.id for c in fused] == ["a", "c"]


def test_fuse_does_not_let_duplicates_within_one_list_add_up():
    # "dup" appears three times in one list; "top" is first in both lists and must win.
    vector = [_chunk("t1", "top"), _chunk("d1", "dup"), _chunk("d2", "dup"), _chunk("d3", "dup")]
    keyword = [_chunk("t2", "top")]
    assert _fuse(vector, keyword)[0].payload["text"] == "top"


def test_fuse_drops_chunks_without_text():
    assert _fuse([_chunk("a", ""), Chunk(id="b", score=1.0, payload={})]) == []


def test_fuse_stores_fused_score():
    fused = _fuse([_chunk("a", "alpha", score=0.9)], [_chunk("a", "alpha", score=12.0)])
    assert fused[0].score == 2 / (retrieval._RRF_K + 1)


# --- _extract_filenames ---


def _set_known_filenames(monkeypatch, names: set[str]) -> None:
    monkeypatch.setattr(retrieval, "_known_filenames", lambda: sorted(names))


def test_extract_filenames_matches_case_insensitively(monkeypatch):
    _set_known_filenames(monkeypatch, {"README.md", "readme.md", "other.py"})
    assert sorted(_extract_filenames("summarize readme.md")) == ["README.md", "readme.md"]


def test_extract_filenames_fuzzy_matches_only_same_suffix(monkeypatch):
    _set_known_filenames(monkeypatch, {"STOP_BUTTON_PLAN.md", "example.md"})
    assert _extract_filenames("what is in stop-button-plan.md") == ["STOP_BUTTON_PLAN.md"]
    assert _extract_filenames("see example.com for details") == []


def test_extract_filenames_skips_unknown_candidates_before_a_known_one(monkeypatch):
    _set_known_filenames(monkeypatch, {"main.c"})
    assert _extract_filenames("e.g. what does main.c do") == ["main.c"]


def test_extract_filenames_without_candidate_returns_empty(monkeypatch):
    _set_known_filenames(monkeypatch, {"README.md"})
    assert _extract_filenames("how does retrieval work") == []


# --- retrieve_best ---


@pytest.mark.parametrize(
    ("rerank_k", "final_k", "reranked_n"),
    [(4, 2, 4), (3, 6, 6)],  # second case: /v1/retrieve asking for more than RERANK_K
)
def test_retrieve_best_reranks_max_of_rerank_k_and_final_k(
    monkeypatch, rerank_k, final_k, reranked_n
):
    vector = [_chunk(f"v{i}", f"vector text {i}") for i in range(5)]
    keyword = [_chunk(f"k{i}", f"keyword text {i}") for i in range(5)]
    reranked = []

    def fake_rerank(question, candidates, top_n):
        reranked.extend(candidates)
        return candidates[:top_n]

    monkeypatch.setattr(retrieval, "embed", lambda q: [0.0])
    monkeypatch.setattr(retrieval, "hybrid_recall", lambda *a, **kw: (vector, keyword))
    monkeypatch.setattr(retrieval, "rerank", fake_rerank)

    result = retrieve_best("q", rerank_k=rerank_k, final_k=final_k)

    assert len(reranked) == reranked_n
    assert len(result) == final_k


# --- reranker lifecycle ---


def test_rerank_loads_reranker_on_first_use(monkeypatch):
    constructed = []

    class FakeCrossEncoder:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))

        def predict(self, pairs, batch_size):
            return [0.5 for _ in pairs]

    monkeypatch.setattr(retrieval, "CrossEncoder", FakeCrossEncoder)
    monkeypatch.setattr(retrieval, "_reranker", None)
    monkeypatch.setattr(retrieval, "RERANK_MODEL", "test-reranker")

    result = rerank("hello", [_chunk("a", "world")])

    assert result[0].rerank_score == 0.5
    assert constructed == [(("test-reranker",), {"device": "cpu"})]


def test_rerank_sorts_by_score_and_uses_small_batches(monkeypatch):
    calls = []

    class FakeReranker:
        def predict(self, pairs, batch_size):
            calls.append(batch_size)
            return [{"low": 0.1, "high": 0.9, "mid": 0.5}[text] for _, text in pairs]

    monkeypatch.setattr(retrieval, "_reranker", FakeReranker())

    result = rerank("q", [_chunk("1", "low"), _chunk("2", "high"), _chunk("3", "mid")], top_n=2)

    assert [c.payload["text"] for c in result] == ["high", "mid"]
    assert calls == [retrieval._RERANK_BATCH_SIZE]


# --- Qdrant recall (dense + BM25 sparse vectors) ---


def _mock_client(monkeypatch, **behaviour) -> MagicMock:
    client = MagicMock(**{"query_points.return_value.points": [], **behaviour})
    monkeypatch.setattr(retrieval, "get_qdrant_client", lambda: client)
    return client


def test_keyword_recall_queries_bm25_vector_with_word_split_text(monkeypatch):
    client = _mock_client(monkeypatch)

    keyword_recall("What does retrieve_best() do?")

    kwargs = client.query_points.call_args.kwargs
    assert kwargs["using"] == SPARSE_VECTOR
    assert isinstance(kwargs["query"], models.Document)
    assert kwargs["query"].text == "What does retrieve_best do"
    assert kwargs["query"].model == "Qdrant/bm25"


def test_vector_recall_queries_dense_vector(monkeypatch):
    client = _mock_client(monkeypatch)

    vector_recall([0.1, 0.2])

    assert client.query_points.call_args.kwargs["using"] == DENSE_VECTOR


def test_hybrid_recall_applies_filename_filter_to_both_searches(monkeypatch):
    client = _mock_client(monkeypatch)

    hybrid_recall("summarize readme.md", [0.1], filenames=["README.md", "readme.md"])

    filters = [c.kwargs["query_filter"] for c in client.query_points.call_args_list]
    assert len(filters) == 2
    assert all(f.must[0].match.any == ["README.md", "readme.md"] for f in filters)


def test_keyword_recall_failure_degrades_to_no_keyword_results(monkeypatch):
    _mock_client(monkeypatch, **{"query_points.side_effect": RuntimeError("inference failed")})
    assert keyword_recall("anything") == []


def test_known_filenames_reads_facet_values(monkeypatch):
    client = _mock_client(monkeypatch)
    client.facet.return_value.hits = [MagicMock(value="README.md"), MagicMock(value="a.py")]
    assert _known_filenames() == ["README.md", "a.py"]
    assert client.facet.call_args.kwargs["key"] == "filename"


def test_known_filenames_failure_returns_empty(monkeypatch):
    _mock_client(monkeypatch, **{"facet.side_effect": RuntimeError("no index")})
    assert _known_filenames() == []
