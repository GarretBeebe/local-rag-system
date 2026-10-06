"""
Multi-stage retrieval pipeline: hybrid recall, rank fusion, and reranking.

Pipeline stages:
  1. hybrid_recall  — Qdrant vector search and BM25 keyword search, run side by side
  2. _fuse          — Reciprocal Rank Fusion merges both ranked lists and drops chunks
                      whose text is an exact duplicate (e.g. the same file indexed twice)
  3. rerank         — scores (question, chunk) pairs with a cross-encoder model

Entry point for callers is retrieve_best(), which runs all three stages and
returns the top-ranked chunks ready to be passed to the LLM.
"""

import difflib
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qdrant_client.models import FieldCondition, Filter, MatchAny
from sentence_transformers import CrossEncoder

from api.embed import embed
from api.keyword_index import KeywordIndex, KeywordResult
from api.timing import timed as _timed
from common.qdrant import get_qdrant_client
from settings import COLLECTION, FINAL_K, RECALL_K, RERANK_K, RERANK_MODEL

logger = logging.getLogger(__name__)


class RetrievalError(Exception):
    """Raised when the vector store is unreachable or returns an infrastructure error."""


@dataclass
class Chunk:
    id: str | int
    payload: dict[str, Any]
    score: float
    rerank_score: float | None = field(default=None)


_reranker: CrossEncoder | None = None
_reranker_lock = threading.Lock()
_reranker_model_name = RERANK_MODEL
_keyword_index: KeywordIndex | None = None

# A "name.ext" token in the question; 1-letter extensions cover .c and .h files.
_FILENAME_RE = re.compile(r"\b([\w.-]+\.[a-zA-Z]{1,5})\b")
_FILENAME_FUZZY_CUTOFF = 0.75
# Standard Reciprocal Rank Fusion constant; damps the advantage of the very top ranks.
_RRF_K = 60
# CrossEncoder.predict length-sorts pairs before batching, so small batches keep short
# chunks from being padded to the longest one (measured ~2.4x faster than the default 32).
_RERANK_BATCH_SIZE = 8


def startup(rerank_model: str = RERANK_MODEL) -> None:
    """Start retrieval support services. The reranker model loads on first use."""
    global _reranker, _reranker_model_name, _keyword_index
    if _reranker_model_name != rerank_model:
        _reranker = None
    _reranker_model_name = rerank_model
    _keyword_index = KeywordIndex()
    _keyword_index.start()


def shutdown() -> None:
    """Stop retrieval support services. Call from FastAPI lifespan cleanup."""
    global _keyword_index
    if _keyword_index is not None:
        _keyword_index.stop()
        _keyword_index = None


def _get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                logger.info("Loading reranker model: %s", _reranker_model_name)
                _reranker = CrossEncoder(_reranker_model_name, device="cpu")
    return _reranker


def _get_keyword_index() -> KeywordIndex:
    if _keyword_index is None:
        raise RuntimeError("api.retrieval.startup() has not been called")
    return _keyword_index


def _extract_filenames(question: str) -> list[str]:
    """Return the indexed filenames a question names (every case variant), or []."""
    candidates = [m.group(1).lower() for m in _FILENAME_RE.finditer(question)]
    if not candidates:
        return []
    by_lower: dict[str, list[str]] = {}
    for name in _get_keyword_index().known_filenames:
        by_lower.setdefault(name.lower(), []).append(name)
    for candidate in candidates:
        if candidate in by_lower:
            return by_lower[candidate]
        suffix = Path(candidate).suffix
        same_suffix = [name for name in by_lower if name.endswith(suffix)]
        close = difflib.get_close_matches(
            candidate, same_suffix, n=1, cutoff=_FILENAME_FUZZY_CUTOFF
        )
        if close:
            return by_lower[close[0]]
    return []


def qdrant_recall(
    question_vec: list[float],
    limit: int = RECALL_K,
    query_filter: Filter | None = None,
) -> list[Chunk]:
    """Returns the nearest chunks by vector similarity."""
    with _timed("qdrant_recall"):
        try:
            res = get_qdrant_client().query_points(
                collection_name=COLLECTION,
                query=question_vec,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
            results = [Chunk(id=p.id, score=p.score, payload=p.payload) for p in res.points]
        except Exception as e:
            logger.error("Qdrant vector recall failed: %s: %s", type(e).__name__, e)
            raise RetrievalError(str(e)) from e
    return results


def rerank(question: str, candidates: list[Chunk], top_n: int = FINAL_K) -> list[Chunk]:
    """Cross-encoder reranking: scores (question, chunk) pairs directly."""
    if not candidates:
        return []

    with _timed("rerank"):
        pairs = [(question, c.payload.get("text", "")) for c in candidates]
        scores = _get_reranker().predict(pairs, batch_size=_RERANK_BATCH_SIZE)

    for c, s in zip(candidates, scores, strict=True):
        c.rerank_score = float(s)

    return sorted(candidates, key=lambda x: x.rerank_score or 0.0, reverse=True)[:top_n]


def hybrid_recall(
    question: str,
    question_vec: list[float],
    limit: int = RECALL_K,
    filenames: list[str] | None = None,
) -> tuple[list[Chunk], list[Chunk]]:
    """Return (vector results, keyword results), each ranked best-first."""
    query_filter = (
        Filter(must=[FieldCondition(key="filename", match=MatchAny(any=filenames))])
        if filenames
        else None
    )
    vector_results = qdrant_recall(question_vec, limit=limit, query_filter=query_filter)

    keyword_results: list[KeywordResult]
    try:
        keyword_results = _get_keyword_index().search(question, limit=limit)
    except Exception as e:
        logger.error("BM25 keyword search failed: %s: %s", type(e).__name__, e)
        keyword_results = []
    if filenames:
        keyword_results = [r for r in keyword_results if r["payload"].get("filename") in filenames]

    keyword_chunks = [
        Chunk(id=r["id"], payload=r["payload"], score=r["bm25_score"]) for r in keyword_results
    ]
    return vector_results, keyword_chunks


def _fuse(*ranked_lists: list[Chunk]) -> list[Chunk]:
    """Merge ranked lists with Reciprocal Rank Fusion; chunks with identical text count once.

    Each list adds 1 / (_RRF_K + rank) per distinct text, so a chunk both searches rank well
    rises to the top, while a text repeated within one list (the same file indexed twice)
    cannot add up. Chunks without text are dropped. The fused score replaces Chunk.score.
    """
    fused: dict[str, Chunk] = {}
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        seen: set[str] = set()
        for chunk in ranked:
            text = chunk.payload.get("text", "")
            if not text or text in seen:
                continue
            seen.add(text)
            rank = len(seen)
            fused.setdefault(text, chunk)
            scores[text] = scores.get(text, 0.0) + 1.0 / (_RRF_K + rank)
    for text, chunk in fused.items():
        chunk.score = scores[text]
    return sorted(fused.values(), key=lambda c: c.score, reverse=True)


def retrieve_best(
    question: str,
    recall_k: int = RECALL_K,
    rerank_k: int = RERANK_K,
    final_k: int = FINAL_K,
) -> list[Chunk]:
    """Run hybrid recall, rank fusion, and cross-encoder reranking to get the top chunks."""
    filenames = _extract_filenames(question)

    with _timed("embed"):
        qvec = embed(question)

    with _timed("hybrid_recall"):
        vector_results, keyword_results = hybrid_recall(
            question, qvec, limit=recall_k, filenames=filenames
        )

    # Rerank at least final_k candidates: /v1/retrieve may ask for more than RERANK_K.
    candidates = _fuse(vector_results, keyword_results)[: max(rerank_k, final_k)]
    return rerank(question, candidates, top_n=final_k)
