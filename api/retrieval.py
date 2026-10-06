"""
Multi-stage retrieval pipeline: hybrid recall, rank fusion, and reranking.

Pipeline stages:
  1. hybrid_recall  — Qdrant vector search and Qdrant BM25 keyword search, side by side
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qdrant_client import models
from qdrant_client.models import FieldCondition, Filter, MatchAny
from sentence_transformers import CrossEncoder

from api.embed import embed
from api.timing import timed as _timed
from common.qdrant import DENSE_VECTOR, SPARSE_VECTOR, bm25_document, get_qdrant_client
from settings import COLLECTION, FINAL_K, RECALL_K, RERANK_K, RERANK_MODEL

logger = logging.getLogger(__name__)


class RetrievalError(Exception):
    """Raised when the vector store is unreachable or returns an infrastructure error."""


@dataclass
class Chunk:
    id: str | int
    payload: dict[str, Any]
    score: float
    rerank_score: float | None = None


_reranker: CrossEncoder | None = None
_reranker_lock = threading.Lock()

# A "name.ext" token in the question; 1-letter extensions cover .c and .h files.
_FILENAME_RE = re.compile(r"\b([\w.-]+\.[a-zA-Z]{1,5})\b")
_FILENAME_FUZZY_CUTOFF = 0.75
# Upper bound on distinct indexed filenames fetched for filename matching (facet default: 10).
_FILENAME_FACET_LIMIT = 100_000
# Standard Reciprocal Rank Fusion constant; damps the advantage of the very top ranks.
_RRF_K = 60
# CrossEncoder.predict length-sorts pairs before batching, so small batches keep short
# chunks from being padded to the longest one (measured ~2.4x faster than the default 32).
_RERANK_BATCH_SIZE = 8


def _get_reranker() -> CrossEncoder:
    """Return the cross-encoder, loading it on first use."""
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                logger.info("Loading reranker model: %s", RERANK_MODEL)
                _reranker = CrossEncoder(RERANK_MODEL, device="cpu")
    return _reranker


def _known_filenames() -> list[str]:
    """Return every distinct indexed filename (needs the keyword index on `filename`)."""
    try:
        with _timed("filename_lookup"):
            response = get_qdrant_client().facet(
                collection_name=COLLECTION, key="filename", limit=_FILENAME_FACET_LIMIT, exact=True
            )
    except Exception as e:
        # Filename matching only narrows the search; without it, the query still runs.
        logger.warning("Filename lookup failed: %s: %s", type(e).__name__, e)
        return []
    return [str(hit.value) for hit in response.hits]


def _extract_filenames(question: str) -> list[str]:
    """Return the indexed filenames a question names (every case variant), or []."""
    candidates = [m.group(1).lower() for m in _FILENAME_RE.finditer(question)]
    if not candidates:
        return []
    by_lower: dict[str, list[str]] = {}
    for name in _known_filenames():
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


def _query_points(
    query: list[float] | models.Document, using: str, limit: int, query_filter: Filter | None
) -> list[Chunk]:
    res = get_qdrant_client().query_points(
        collection_name=COLLECTION,
        query=query,
        using=using,
        query_filter=query_filter,
        limit=limit,
        with_payload=True,
    )
    return [Chunk(id=p.id, score=p.score, payload=p.payload) for p in res.points]


def qdrant_recall(
    question_vec: list[float],
    limit: int = RECALL_K,
    query_filter: Filter | None = None,
) -> list[Chunk]:
    """Returns the nearest chunks by vector similarity."""
    with _timed("qdrant_recall"):
        try:
            return _query_points(question_vec, DENSE_VECTOR, limit, query_filter)
        except Exception as e:
            logger.error("Qdrant vector recall failed: %s: %s", type(e).__name__, e)
            raise RetrievalError(str(e)) from e


def keyword_recall(
    question: str,
    limit: int = RECALL_K,
    query_filter: Filter | None = None,
) -> list[Chunk]:
    """Returns the best BM25 matches; on failure, retrieval degrades to vector results only."""
    with _timed("keyword_recall"):
        try:
            return _query_points(bm25_document(question), SPARSE_VECTOR, limit, query_filter)
        except Exception as e:
            logger.error("BM25 keyword search failed: %s: %s", type(e).__name__, e)
            return []


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
    return (
        qdrant_recall(question_vec, limit=limit, query_filter=query_filter),
        keyword_recall(question, limit=limit, query_filter=query_filter),
    )


def _fuse(*ranked_lists: list[Chunk]) -> list[Chunk]:
    """Merge ranked lists with Reciprocal Rank Fusion; chunks with identical text count once.

    Each list adds 1 / (_RRF_K + rank) per distinct text, so a chunk both searches rank well
    rises to the top, while a text repeated within one list (the same file indexed twice)
    cannot add up. Chunks without text are dropped. The fused score replaces Chunk.score.
    """
    fused: dict[str, Chunk] = {}
    for ranked in ranked_lists:
        seen: set[str] = set()
        for chunk in ranked:
            text = chunk.payload.get("text", "")
            if not text or text in seen:
                continue
            seen.add(text)
            rank = len(seen)  # position among this list's distinct texts
            if text not in fused:
                chunk.score = 0.0
                fused[text] = chunk
            fused[text].score += 1.0 / (_RRF_K + rank)
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

    vector_results, keyword_results = hybrid_recall(
        question, qvec, limit=recall_k, filenames=filenames
    )

    # Rerank at least final_k candidates: /v1/retrieve may ask for more than RERANK_K.
    candidates = _fuse(vector_results, keyword_results)[: max(rerank_k, final_k)]
    return rerank(question, candidates, top_n=final_k)
