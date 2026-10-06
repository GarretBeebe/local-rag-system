"""Unit tests for retrieval pipeline pure logic (rank fusion, filename detection, reranking)."""

# conftest.py patches sentence_transformers and KeywordIndex._build before
# this module is imported, so api.retrieval loads without side effects.
import api.retrieval as retrieval
from api.retrieval import Chunk, _extract_filenames, _fuse, rerank, retrieve_best


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
    class FakeKeywordIndex:
        known_filenames = names

    monkeypatch.setattr(retrieval, "_keyword_index", FakeKeywordIndex())


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


def test_retrieve_best_reranks_fused_candidates_up_to_rerank_k(monkeypatch):
    vector = [_chunk(f"v{i}", f"vector text {i}") for i in range(5)]
    keyword = [_chunk(f"k{i}", f"keyword text {i}") for i in range(5)]
    reranked = []

    def fake_rerank(question, candidates, top_n):
        reranked.extend(candidates)
        return candidates[:top_n]

    _set_known_filenames(monkeypatch, set())
    monkeypatch.setattr(retrieval, "embed", lambda q: [0.0])
    monkeypatch.setattr(retrieval, "hybrid_recall", lambda *a, **kw: (vector, keyword))
    monkeypatch.setattr(retrieval, "rerank", fake_rerank)

    result = retrieve_best("q", rerank_k=4, final_k=2)

    assert len(reranked) == 4
    assert len(result) == 2


def test_retrieve_best_reranks_at_least_final_k_candidates(monkeypatch):
    vector = [_chunk(f"v{i}", f"text {i}") for i in range(10)]
    reranked = []

    def fake_rerank(question, candidates, top_n):
        reranked.extend(candidates)
        return candidates[:top_n]

    _set_known_filenames(monkeypatch, set())
    monkeypatch.setattr(retrieval, "embed", lambda q: [0.0])
    monkeypatch.setattr(retrieval, "hybrid_recall", lambda *a, **kw: (vector, []))
    monkeypatch.setattr(retrieval, "rerank", fake_rerank)

    retrieve_best("q", rerank_k=3, final_k=6)

    assert len(reranked) == 6


# --- reranker lifecycle ---


def test_startup_does_not_load_reranker(monkeypatch):
    constructed = []

    class FakeCrossEncoder:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))

    class FakeKeywordIndex:
        known_filenames = set()

        def start(self):
            pass

    monkeypatch.setattr(retrieval, "CrossEncoder", FakeCrossEncoder)
    monkeypatch.setattr(retrieval, "KeywordIndex", FakeKeywordIndex)
    monkeypatch.setattr(retrieval, "_reranker", None)

    retrieval.startup("test-reranker")

    assert constructed == []


def test_rerank_loads_reranker_on_first_use(monkeypatch):
    constructed = []

    class FakeCrossEncoder:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))

        def predict(self, pairs, batch_size):
            return [0.5 for _ in pairs]

    monkeypatch.setattr(retrieval, "CrossEncoder", FakeCrossEncoder)
    monkeypatch.setattr(retrieval, "_reranker", None)
    monkeypatch.setattr(retrieval, "_reranker_model_name", "test-reranker")

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
