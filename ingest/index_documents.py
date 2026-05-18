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
import time
import uuid
from pathlib import Path
from typing import Literal

from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)
from tqdm import tqdm

from api.embed import embed
from common.paths import has_allowed_extension, normalize_path
from ingest.chunkers import chunk_document
from settings import (
    ALLOWED_EXTENSIONS,
    COLLECTION,
    DOCS_PATH,
    MAX_FILE_SIZE,
    VECTOR_SIZE,
    get_qdrant_client,
)

logger = logging.getLogger(__name__)


@functools.cache
def ensure_collection() -> None:
    if not get_qdrant_client().collection_exists(COLLECTION):
        logger.info("Collection missing — creating new collection")
        get_qdrant_client().create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def load_files() -> list[Path]:
    return [
        p
        for p in DOCS_PATH.rglob("*")
        if p.is_file() and has_allowed_extension(p, ALLOWED_EXTENSIONS)
    ]


def index_file(path: Path) -> Literal["indexed", "skipped", "failed"]:
    ensure_collection()
    normalized_path = normalize_path(path)

    if path.stat().st_size > MAX_FILE_SIZE:
        logger.info("Skipping large file: %s", path)
        return "skipped"

    t_read = time.monotonic()
    try:
        text = path.read_text(errors="ignore")
        logger.debug("Read %s with errors='ignore'", path)
    except Exception as e:
        logger.warning("Skipping unreadable file %s: %s", path, e)
        return "skipped"
    t_read = time.monotonic() - t_read

    t_chunk = time.monotonic()
    chunks = chunk_document(path, text)
    chunks = [c.strip() for c in chunks if c and c.strip()]
    t_chunk = time.monotonic() - t_chunk

    if not chunks:
        logger.info("No non-empty chunks for %s", path)
        return "skipped"

    document_id = str(uuid.uuid5(uuid.NAMESPACE_URL, normalized_path))

    t_embed = time.monotonic()
    points = []
    for i, chunk in enumerate(chunks):
        try:
            vec = embed(chunk)
        except Exception as e:
            logger.warning("Skipping chunk %s for %s due to embedding failure: %s", i, path, e)
            continue

        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={
                    "text": chunk,
                    "document_id": document_id,
                    "filename": path.name,
                    "filepath": normalized_path,
                    "chunk_index": i,
                    "chunk_total": 0,  # corrected below once all chunks are embedded
                },
            )
        )
    t_embed = time.monotonic() - t_embed

    if not points:
        logger.warning("No valid chunks to index for %s", path)
        return "failed"

    # Correct chunk_total now that we know how many chunks actually embedded.
    for p in points:
        p.payload["chunk_total"] = len(points)

    # Prepare-then-swap: all replacement points are ready; now do destructive ops.
    t_upsert = time.monotonic()
    try:
        delete_document(path)
    except Exception as e:
        logger.error("Failed to delete existing vectors for %s: %s", path, e)
        return "failed"

    try:
        get_qdrant_client().upsert(collection_name=COLLECTION, points=points)
    except Exception as e:
        # Delete succeeded but upsert failed: document is absent until next successful re-index.
        logger.error(
            "Upsert failed for %s after delete — absent from index until next re-index: %s",
            path,
            e,
        )
        return "failed"
    t_upsert = time.monotonic() - t_upsert

    logger.info(
        "Indexed %s: %d chunks — read=%.2fs chunk=%.2fs embed=%.2fs upsert=%.2fs",
        path,
        len(points),
        t_read,
        t_chunk,
        t_embed,
        t_upsert,
    )
    return "indexed"


def delete_document(filepath: Path | str) -> None:
    normalized_path = normalize_path(filepath)
    logger.info("Deleting vectors for: %s", normalized_path)
    get_qdrant_client().delete(
        collection_name=COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="filepath", match=MatchValue(value=normalized_path))]
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
