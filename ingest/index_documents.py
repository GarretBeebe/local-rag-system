"""
Document ingestion pipeline: loads files, splits them into overlapping chunks,
generates embeddings via Ollama, and upserts the results into Qdrant.

Exposes two public functions used by the filesystem watcher:
  - index_file(path)          — chunk, embed, and upsert a single file
  - delete_document(filepath) — remove all vectors belonging to a file

Can also be run directly as a script to batch-index the documents directory:
  python ingest/index_documents.py
"""

import functools
import logging
import uuid
from pathlib import Path

import requests
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)
from tqdm import tqdm

from ingest.chunkers import chunk_document
from settings import (
    ALLOWED_EXTENSIONS,
    COLLECTION,
    DOCS_PATH,
    EMBED_MODEL,
    MAX_EMBED_CHARS,
    MAX_FILE_SIZE,
    OLLAMA_BASE_URL,
    VECTOR_SIZE,
    qdrant_client,
)

logger = logging.getLogger(__name__)


@functools.cache
def ensure_collection() -> None:
    if not qdrant_client.collection_exists(COLLECTION):
        logger.info("Collection missing — creating new collection")
        qdrant_client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


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


def load_files() -> list[Path]:
    return [p for p in DOCS_PATH.rglob("*") if p.is_file() and p.suffix in ALLOWED_EXTENSIONS]


def index_file(path: Path) -> None:
    ensure_collection()

    if path.stat().st_size > MAX_FILE_SIZE:
        logger.info("Skipping large file: %s", path)
        return

    try:
        text = path.read_text(errors="ignore")
    except Exception as e:
        logger.warning("Skipping unreadable file %s: %s", path, e)
        return

    chunks = chunk_document(path, text)
    chunks = [c.strip() for c in chunks if c and c.strip()]

    if not chunks:
        logger.info("No non-empty chunks for %s", path)
        return

    document_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve())))
    points = []

    for i, chunk in enumerate(chunks):
        try:
            vec = embed(chunk)
        except Exception as e:
            logger.warning(
                "Skipping chunk %s for %s due to embedding failure: %s",
                i,
                path,
                e,
            )
            continue

        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={
                    "text": chunk,
                    "document_id": document_id,
                    "filename": path.name,
                    "filepath": str(path.resolve()),
                    "chunk_index": i,
                    "chunk_total": len(chunks),
                },
            )
        )

    if not points:
        logger.warning("No valid chunks to index for %s", path)
        return

    qdrant_client.upsert(collection_name=COLLECTION, points=points)


def delete_document(filepath: Path | str) -> None:
    logger.info("Deleting vectors for: %s", filepath)
    qdrant_client.delete(
        collection_name=COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="filepath", match=MatchValue(value=str(filepath)))]
        ),
    )


def main() -> None:
    files = load_files()
    print(f"Found {len(files)} files to index")

    if not files:
        return

    for f in tqdm(files):
        index_file(f)


if __name__ == "__main__":
    main()