"""
Shared embedding helper used by both the retrieval pipeline and the ingest pipeline.

Kept separate from api.retrieval to avoid loading heavy ML models (CrossEncoder,
KeywordIndex) in contexts that only need embedding (e.g. batch indexing).

Batch embedding: the Ollama /api/embeddings endpoint used here does not support
batch input (it takes a single "prompt" string). Ollama 0.1.25+ added /api/embed
which accepts {"model": ..., "input": [text1, text2, ...]}. If the deployment
upgrades Ollama, add embed_batch() using /api/embed and update index_documents.py
to batch embed all chunks per file in one request.
"""

import logging

from requests import RequestException

import api.ollama_client as ollama_client
from settings import EMBED_MODEL, MAX_EMBED_CHARS, OLLAMA_EMBED_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


def embed(text: str) -> list[float]:
    """Return an embedding vector for the given text via the Ollama embeddings API."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot embed empty text")

    if len(text) > MAX_EMBED_CHARS:
        logger.warning(
            "Truncating text from %d to %d chars for embedding", len(text), MAX_EMBED_CHARS
        )
        text = text[:MAX_EMBED_CHARS]

    try:
        response = ollama_client.post(
            "/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=OLLAMA_EMBED_TIMEOUT_SECONDS,
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
