"""Unit tests for retrieval pipeline pure logic (cosine similarity, MMR)."""

import pytest

# conftest.py patches sentence_transformers and KeywordIndex._build before
# this module is imported, so api.retrieval loads without side effects.
from api.retrieval import Chunk, _deduplicate, cosine, mmr_select


def _chunk(id: str, vector: list[float], text: str = "") -> Chunk:
    return Chunk(id=id, score=1.0, vector=vector, payload={"text": text})


# --- cosine ---

def test_cosine_identical_vectors():
    v = [1.0, 0.0, 0.0]
    assert cosine(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_opposite_vectors():
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_zero_vector_returns_zero():
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([1.0, 1.0], [0.0, 0.0]) == 0.0


# --- mmr_select ---

def test_mmr_select_returns_at_most_top_n():
    q = [1.0, 0.0]
    candidates = [_chunk(str(i), [1.0, float(i)]) for i in range(10)]
    result = mmr_select(q, candidates, top_n=3)
    assert len(result) <= 3


def test_mmr_select_empty_candidates():
    assert mmr_select([1.0, 0.0], [], top_n=4) == []


def test_mmr_select_fewer_candidates_than_top_n():
    q = [1.0, 0.0]
    candidates = [_chunk("a", [1.0, 0.0]), _chunk("b", [0.0, 1.0])]
    result = mmr_select(q, candidates, top_n=10)
    assert len(result) == 2


def test_mmr_select_prefers_relevant_and_diverse():
    # With lambda_mult=0.3 (70% diversity weight), the diverse-but-orthogonal
    # candidate c should outscore the near-duplicate b.
    # After selecting a=[1,0] first:
    #   score(b) = 0.3 * cosine(q,b) - 0.7 * cosine(b,a) ≈ 0.3 * 1 - 0.7 * 1 = -0.4
    #   score(c) = 0.3 * cosine(q,c) - 0.7 * cosine(c,a) = 0.3 * 0 - 0.7 * 0  =  0
    # c wins.
    q = [1.0, 0.0]
    a = _chunk("a", [1.0, 0.0])
    b = _chunk("b", [0.99, 0.01])  # near-duplicate of a
    c = _chunk("c", [0.0, 1.0])   # orthogonal — diverse
    result = mmr_select(q, [a, b, c], top_n=2, lambda_mult=0.3)
    ids = {r.id for r in result}
    assert "a" in ids
    assert "c" in ids
    assert "b" not in ids


def test_mmr_select_preserves_chunk_data():
    q = [1.0, 0.0]
    chunk = _chunk("x", [1.0, 0.0], text="hello world")
    result = mmr_select(q, [chunk], top_n=1)
    assert result[0].payload["text"] == "hello world"


# --- _deduplicate ---

def test_deduplication_removes_duplicate_ids():
    chunks = [
        _chunk("a", [1.0, 0.0]),
        _chunk("b", [0.0, 1.0]),
        _chunk("a", [1.0, 0.0]),  # duplicate
    ]
    result = _deduplicate(chunks)
    assert len(result) == 2
    assert [c.id for c in result] == ["a", "b"]


def test_deduplication_keeps_first_occurrence():
    chunks = [
        Chunk(id="a", score=0.9, vector=[1.0], payload={"text": "first"}),
        Chunk(id="a", score=0.5, vector=[1.0], payload={"text": "second"}),
    ]
    result = _deduplicate(chunks)
    assert result[0].payload["text"] == "first"


def test_deduplication_no_duplicates_unchanged():
    chunks = [_chunk("a", [1.0, 0.0]), _chunk("b", [0.0, 1.0])]
    result = _deduplicate(chunks)
    assert len(result) == 2
