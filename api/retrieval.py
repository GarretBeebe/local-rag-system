"""
Multi-stage retrieval pipeline: hybrid recall, MMR diversification, and reranking.

Pipeline stages:
  1. hybrid_recall  — combines Qdrant vector search with BM25 keyword search
  2. mmr_select     — applies Maximal Marginal Relevance to diversify results
  3. rerank         — scores (question, chunk) pairs with a cross-encoder model

Entry point for callers is retrieve_best(), which runs all three stages and
returns the top-ranked chunks ready to be passed to the LLM.
"""

import logging
import math
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeAlias

from qdrant_client.models import FieldCondition, Filter, MatchValue
from sentence_transformers import CrossEncoder

from api.embed import embed
from api.keyword_index import KeywordIndex
from indexer.fingerprint_store import list_all_paths
from settings import COLLECTION, MMR_ENABLED, RAG_TIMING, RERANK_MODEL, qdrant_client

logger = logging.getLogger(__name__)

Chunk: TypeAlias = dict[str, Any]


@contextmanager
def timed(label: str):
    if not RAG_TIMING:
        yield
        return
    t = time.perf_counter()
    yield
    logger.debug("%s: %.3fs", label, time.perf_counter() - t)

reranker = CrossEncoder(RERANK_MODEL, device="cpu")
keyword_index = KeywordIndex()

_FILENAME_RE = re.compile(r"\b([\w.-]+\.[a-zA-Z]{2,5})\b")


def _extract_filename(question: str) -> str | None:
    """Return a filename from the query if it matches a known indexed file, else None."""
    match = _FILENAME_RE.search(question)
    if not match:
        return None
    candidate = match.group(1)
    known = {Path(p).name for p in list_all_paths()}
    return candidate if candidate in known else None


def cosine(a: list[float], b: list[float]) -> float:
    """Returns the cosine similarity between two vectors, or 0.0 if either is zero-length."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def qdrant_recall(
    question_vec: list[float],
    limit: int = 15,
    with_vectors: bool = True,
    query_filter: Filter | None = None,
) -> list[Chunk]:
    """Returns candidate chunks; fetches vectors only when needed for MMR."""
    with timed("qdrant_recall"):
        res = qdrant_client.query_points(
            collection_name=COLLECTION,
            query=question_vec,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=with_vectors,
        )
        results = [
            {"id": p.id, "score": p.score, "vector": p.vector, "payload": p.payload}
            for p in res.points
        ]
    return results


def mmr_select(
    question_vec: list[float],
    candidates: list[Chunk],
    top_n: int = 8,
    lambda_mult: float = 0.7,
) -> list[Chunk]:
    """Select a diverse subset of candidates using Maximal Marginal Relevance.

    Only candidates that include a dense vector are considered; keyword-only
    results without vectors should be filtered out by the caller.
    """
    def mmr_score(c, selected):
        sim_to_query = cosine(question_vec, c["vector"])
        diversity_penalty = (
            max(cosine(c["vector"], s["vector"]) for s in selected)
            if selected else 0.0
        )
        return lambda_mult * sim_to_query - (1.0 - lambda_mult) * diversity_penalty

    selected = []
    remaining = candidates[:]

    while remaining and len(selected) < top_n:
        best = max(remaining, key=lambda c: mmr_score(c, selected))
        selected.append(best)
        remaining.remove(best)

    return selected


def rerank(question: str, candidates: list[Chunk], top_n: int = 4) -> list[Chunk]:
    """Cross-encoder reranking: scores (question, chunk) pairs directly."""
    if not candidates:
        return []

    with timed("rerank"):
        pairs = [(question, c["payload"]["text"]) for c in candidates]
        scores = reranker.predict(pairs)

    for c, s in zip(candidates, scores, strict=False):
        c["rerank_score"] = float(s)

    return sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)[:top_n]


def hybrid_recall(
    question: str,
    question_vec: list[float],
    limit: int = 15,
    filename: str | None = None,
) -> list[Chunk]:
    """Combine dense vector recall from Qdrant with BM25 keyword search results."""
    query_filter = (
        Filter(must=[FieldCondition(key="filename", match=MatchValue(value=filename))])
        if filename else None
    )
    vector_results = qdrant_recall(question_vec, limit=limit,
                                   with_vectors=MMR_ENABLED, query_filter=query_filter)

    keyword_results = keyword_index.search(question, limit=limit)
    if filename:
        keyword_results = [r for r in keyword_results if r["payload"].get("filename") == filename]

    keyword_candidates = [
        {"id": r["id"], "payload": r["payload"], "vector": None, "score": r["bm25_score"]}
        for r in keyword_results
    ]

    return vector_results + keyword_candidates


def retrieve_best(
    question: str,
    recall_k: int = 15,
    mmr_k: int = 12,
    final_k: int = 4,
) -> list[Chunk]:
    """Run hybrid recall, optional MMR diversification, and reranking to get top chunks."""
    filename = _extract_filename(question)

    with timed("embed"):
        qvec = embed(question)

    with timed("hybrid_recall"):
        candidates = hybrid_recall(question, qvec, limit=recall_k, filename=filename)

    if not candidates:
        return []

    # Dedupe by point id — vector and keyword results can overlap.
    seen: set[str] = set()
    deduped = []
    for c in candidates:
        if c["id"] not in seen:
            seen.add(c["id"])
            deduped.append(c)
    candidates = deduped

    vector_candidates = [c for c in candidates if c.get("vector") is not None]

    if not vector_candidates:
        return rerank(question, candidates, top_n=final_k)

    if MMR_ENABLED:
        with timed("mmr_select"):
            diversified = mmr_select(qvec, vector_candidates, top_n=mmr_k)
    else:
        diversified = vector_candidates[:mmr_k]

    merged = diversified + [c for c in candidates if c.get("vector") is None]
    return rerank(question, merged, top_n=final_k)
