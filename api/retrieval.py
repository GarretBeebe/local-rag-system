"""
Multi-stage retrieval pipeline: hybrid recall, MMR diversification, and reranking.

Pipeline stages:
  1. hybrid_recall  — combines Qdrant vector search with BM25 keyword search
  2. mmr_select     — applies Maximal Marginal Relevance to diversify results
  3. rerank         — scores (question, chunk) pairs with a cross-encoder model

Entry point for callers is retrieve_best(), which runs all three stages and
returns the top-ranked chunks ready to be passed to the LLM.
"""

import math
from typing import Any

import requests
from sentence_transformers import CrossEncoder

from api.keyword_index import KeywordIndex
from settings import COLLECTION, EMBED_MODEL, MAX_EMBED_CHARS, OLLAMA_BASE_URL, RERANK_MODEL, qdrant_client

reranker = CrossEncoder(RERANK_MODEL, device="cpu")
keyword_index = KeywordIndex()

def embed(text: str) -> list[float]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot embed empty text")

    if len(text) > MAX_EMBED_CHARS:
        text = text[:MAX_EMBED_CHARS]

    r = requests.post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def qdrant_recall(question_vec: list[float], limit: int = 30) -> list[dict[str, Any]]:
    """Returns candidate chunks with payload + vector (vector needed for MMR)."""
    res = qdrant_client.query_points(
        collection_name=COLLECTION,
        query=question_vec,
        limit=limit,
        with_payload=True,
        with_vectors=True,
    )
    return [
        {"id": p.id, "score": p.score, "vector": p.vector, "payload": p.payload}
        for p in res.points
    ]


def mmr_select(
    question_vec: list[float],
    candidates: list[dict[str, Any]],
    top_n: int = 8,
    lambda_mult: float = 0.7,
) -> list[dict[str, Any]]:
    selected = []
    remaining = candidates[:]

    while remaining and len(selected) < top_n:
        def mmr_score(c):
            sim_to_query = cosine(question_vec, c["vector"])
            diversity_penalty = (
                max(cosine(c["vector"], s["vector"]) for s in selected)
                if selected else 0.0
            )
            return lambda_mult * sim_to_query - (1.0 - lambda_mult) * diversity_penalty

        best = max(remaining, key=mmr_score)
        selected.append(best)
        remaining.remove(best)

    return selected


def rerank(question: str, candidates: list[dict[str, Any]], top_n: int = 6) -> list[dict[str, Any]]:
    """Cross-encoder reranking: scores (question, chunk) pairs directly."""
    if not candidates:
        return []

    pairs = [(question, c["payload"]["text"]) for c in candidates]
    scores = reranker.predict(pairs)

    for c, s in zip(candidates, scores, strict=False):
        c["rerank_score"] = float(s)

    return sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)[:top_n]


def hybrid_recall(
    question: str,
    question_vec: list[float],
    limit: int = 20,
) -> list[dict[str, Any]]:
    vector_results = qdrant_recall(question_vec, limit=limit)
    keyword_results = keyword_index.search(question, limit=limit)

    keyword_candidates = [
        {"payload": r["payload"], "vector": None, "score": r["bm25_score"]}
        for r in keyword_results
    ]

    return vector_results + keyword_candidates


def retrieve_best(
    question: str,
    recall_k: int = 30,
    mmr_k: int = 10,
    final_k: int = 6,
) -> list[dict[str, Any]]:
    qvec = embed(question)
    candidates = hybrid_recall(question, qvec, limit=recall_k)

    if not candidates:
        return []

    vector_candidates = [c for c in candidates if c.get("vector") is not None]

    if not vector_candidates:
        return rerank(question, candidates, top_n=final_k)

    diversified = mmr_select(qvec, vector_candidates, top_n=mmr_k)
    merged = diversified + [c for c in candidates if c.get("vector") is None]

    return rerank(question, merged, top_n=final_k)
