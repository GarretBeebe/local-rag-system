"""
Shared embedding helper used by both the retrieval pipeline and the ingest pipeline.

Kept separate from api.retrieval to avoid loading heavy ML models (CrossEncoder,
KeywordIndex) in contexts that only need embedding (e.g. batch indexing).
"""

import requests

from settings import EMBED_MODEL, MAX_EMBED_CHARS, OLLAMA_BASE_URL


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
