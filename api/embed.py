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

import api.ollama_client as ollama_client
from settings import EMBED_MODEL, MAX_EMBED_CHARS, OLLAMA_EMBED_TIMEOUT_SECONDS, VECTOR_SIZE

logger = logging.getLogger(__name__)


def _prepare_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot embed empty text")
    if len(text) > MAX_EMBED_CHARS:
        logger.warning(
            "Truncating text from %d to %d chars for embedding", len(text), MAX_EMBED_CHARS
        )
        text = text[:MAX_EMBED_CHARS]
    return text


def _validate_vector(vector: list[float], model: str = EMBED_MODEL) -> list[float]:
    if len(vector) != VECTOR_SIZE:
        raise RuntimeError(
            f"Embedding model {model!r} returned {len(vector)} dimensions; "
            f"configured VECTOR_SIZE is {VECTOR_SIZE}"
        )
    return vector


def embed(text: str) -> list[float]:
    """Return an embedding vector for the given text via the Ollama embeddings API."""
    text = _prepare_text(text)

    response = ollama_client.post_with_retry(
        "/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=OLLAMA_EMBED_TIMEOUT_SECONDS,
    )

    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError(f"Embedding service returned invalid JSON: {e}") from e

    if "embedding" not in data:
        raise RuntimeError("Embedding response missing 'embedding' field")

    return _validate_vector(data["embedding"])


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Return embedding vectors for multiple texts via Ollama's batch embed API."""
    if not texts:
        return []
    prepared = [_prepare_text(text) for text in texts]

    response = ollama_client.post_with_retry(
        "/api/embed",
        json={"model": EMBED_MODEL, "input": prepared},
        timeout=OLLAMA_EMBED_TIMEOUT_SECONDS,
    )

    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError(f"Batch embedding service returned invalid JSON: {e}") from e

    if "embeddings" not in data:
        raise RuntimeError("Batch embedding response missing 'embeddings' field")

    vectors = data["embeddings"]
    if len(vectors) != len(prepared):
        raise RuntimeError(
            f"Batch embedding returned {len(vectors)} vectors for {len(prepared)} texts"
        )
    return [_validate_vector(vector) for vector in vectors]
