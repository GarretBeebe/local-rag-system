"""Shared QdrantClient singleton and the collection's vector layout."""

import re
import threading

from qdrant_client import QdrantClient, models

from settings import QDRANT_API_KEY, QDRANT_HOST, QDRANT_PORT

# Each point carries a dense embedding (meaning) and a BM25 sparse vector (exact words).
# Qdrant computes the BM25 vector server-side from the text in bm25_document().
DENSE_VECTOR = "dense"
SPARSE_VECTOR = "bm25"

# Qdrant's default "word" tokenizer splits identifiers (`retrieve_best` -> `retrieve`, `best`)
# and its "whitespace" tokenizer keeps punctuation (`retrieve_best(question):`). Splitting on
# \w+ here and tokenizing on whitespace keeps identifiers whole without punctuation. No
# stemming or stopwords: code identifiers are not English. avg_len is the measured mean word
# count per chunk; it is baked into stored vectors, so changing it requires a full reindex.
_BM25_OPTIONS = {"tokenizer": "whitespace", "language": "none", "avg_len": 64}
_WORD_RE = re.compile(r"\w+")

_qdrant_client: QdrantClient | None = None
_qdrant_lock = threading.Lock()


def get_qdrant_client() -> QdrantClient:
    """Return the shared QdrantClient, creating it on first call."""
    global _qdrant_client
    if _qdrant_client is None:
        with _qdrant_lock:
            if _qdrant_client is None:
                _qdrant_client = QdrantClient(
                    url=f"http://{QDRANT_HOST}:{QDRANT_PORT}",
                    api_key=QDRANT_API_KEY or None,
                )
    return _qdrant_client


def bm25_document(text: str) -> models.Document:
    """Return text for server-side BM25, prepared identically for indexing and queries."""
    return models.Document(
        text=" ".join(_WORD_RE.findall(text)), model="Qdrant/bm25", options=_BM25_OPTIONS
    )
