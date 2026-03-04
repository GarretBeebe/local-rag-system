from __future__ import annotations

import math
import requests
from typing import List, Dict, Any, Tuple

from qdrant_client import QdrantClient
from sentence_transformers import CrossEncoder
from keyword_index import KeywordIndex


# ---- Ollama ----
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"

# ---- Qdrant ----
COLLECTION = "documents"
client = QdrantClient(host="localhost", port=6333)

# ---- Reranker ----
# Strong default reranker (CPU-friendly). If you want higher quality later:
# "BAAI/bge-reranker-large" (slower) or "BAAI/bge-reranker-v2-m3" (if supported)
RERANK_MODEL = "BAAI/bge-reranker-base"
reranker = CrossEncoder(RERANK_MODEL, device="cpu")

keyword_index = KeywordIndex()


def embed(text: str) -> List[float]:
    r = requests.post(
        OLLAMA_EMBED_URL,
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def cosine(a: List[float], b: List[float]) -> float:
    # small, safe cosine for MMR
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def qdrant_recall(question: str, limit: int = 30) -> List[Dict[str, Any]]:
    """
    Returns candidate chunks with payload + vector (vector needed for MMR).
    """
    qvec = embed(question)

    res = client.query_points(
        collection_name=COLLECTION,
        query=qvec,
        limit=limit,
        with_payload=True,
        with_vectors=True,
    )

    points: List[Dict[str, Any]] = []
    for p in res.points:
        points.append(
            {
                "id": p.id,
                "score": p.score,
                "vector": p.vector,
                "payload": p.payload,
            }
        )
    return points


def mmr_select(question_vec, candidates, top_n=8, lambda_mult=0.7):

    selected = []
    remaining = candidates[:]

    while remaining and len(selected) < top_n:

        best = None
        best_score = -1e9

        for c in remaining:

            sim_to_query = cosine(question_vec, c["vector"])

            if not selected:
                diversity_penalty = 0.0
            else:
                max_sim_to_selected = max(
                    cosine(c["vector"], s["vector"]) for s in selected
                )
                diversity_penalty = max_sim_to_selected

            mmr_score = (lambda_mult * sim_to_query) - (
                (1.0 - lambda_mult) * diversity_penalty
            )

            if mmr_score > best_score:
                best_score = mmr_score
                best = c

        selected.append(best)
        remaining.remove(best)

    return selected


def rerank(question: str, candidates: List[Dict[str, Any]], top_n: int = 6) -> List[Dict[str, Any]]:
    """
    Cross-encoder reranking: scores (question, chunk) pairs directly.
    """
    if not candidates:
        return []

    pairs: List[Tuple[str, str]] = []
    for c in candidates:
        pairs.append((question, c["payload"]["text"]))

    scores = reranker.predict(pairs)

    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)

    candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
    return candidates[:top_n]

def hybrid_recall(question):

    vector_results = qdrant_recall(question, limit=20)

    keyword_results = keyword_index.search(question, limit=20)

    combined = []

    for r in vector_results:
        combined.append(r)

    for r in keyword_results:
        combined.append({
            "payload": r["payload"],
            "vector": None,
            "score": r["bm25_score"]
        })

    return combined


def retrieve_best(question: str, recall_k=30, mmr_k=10, final_k=6):

    # Hybrid recall
    candidates = hybrid_recall(question)

    if not candidates:
        return []

    # Only vectors participate in MMR
    vector_candidates = [c for c in candidates if c.get("vector") is not None]

    if not vector_candidates:
        return rerank(question, candidates, top_n=final_k)

    qvec = embed(question)

    diversified = mmr_select(qvec, vector_candidates, top_n=mmr_k, lambda_mult=0.7)

    # Merge keyword-only results back in before reranking
    merged = diversified + [c for c in candidates if c.get("vector") is None]

    best = rerank(question, merged, top_n=final_k)

    return best
