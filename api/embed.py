"""
Shared embedding helper used by both the retrieval pipeline and the ingest pipeline.

Kept separate from api.retrieval to avoid loading heavy ML models (CrossEncoder,
KeywordIndex) in contexts that only need embedding (e.g. the watcher).

Texts are embedded as-is, without the "search_query: " / "search_document: " prefixes
the nomic-embed-text model card asks for: on this corpus they lowered retrieval quality
(scripts/eval_retrieval.py, 2026-10-06: hit@4 0.95 without vs 0.90 with). Re-measure
before adding them; changing embedding inputs requires a full reindex.
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


def _validate_vector(vector: list[float]) -> list[float]:
    if len(vector) != VECTOR_SIZE:
        raise RuntimeError(
            f"Embedding model {EMBED_MODEL!r} returned {len(vector)} dimensions; "
            f"configured VECTOR_SIZE is {VECTOR_SIZE}"
        )
    return vector


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


def embed(text: str) -> list[float]:
    """Return an embedding vector for one text (a search question)."""
    return embed_batch([text])[0]
