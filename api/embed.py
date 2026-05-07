"""
Shared embedding helper used by both the retrieval pipeline and the ingest pipeline.

Kept separate from api.retrieval to avoid loading heavy ML models (CrossEncoder,
KeywordIndex) in contexts that only need embedding (e.g. batch indexing).
"""

from requests import RequestException

import api.ollama_client as ollama_client
from settings import EMBED_MODEL, MAX_EMBED_CHARS


def embed(text: str) -> list[float]:
    """Return an embedding vector for the given text via the Ollama embeddings API."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot embed empty text")

    if len(text) > MAX_EMBED_CHARS:
        text = text[:MAX_EMBED_CHARS]

    try:
        response = ollama_client.post(
            "/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=60,
        )
        response.raise_for_status()
    except RequestException as e:
        raise RuntimeError(f"Embedding request failed: {e}") from e

    try:
        data = response.json()
    except ValueError as e:
        # .json() failed
        raise RuntimeError(f"Embedding service returned invalid JSON: {e}") from e

    if "embedding" not in data:
        raise RuntimeError("Embedding response missing 'embedding' field")

    return data["embedding"]
