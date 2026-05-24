"""
Document ingestion pipeline: loads files, splits them into overlapping chunks,
generates embeddings via Ollama, and upserts the results into Qdrant.

Exposes public functions used by the filesystem watcher:
  - index_file(path)                — chunk, embed, and upsert a single file
  - remove_indexed_document(path)   — delete vectors and fingerprint for a file
  - delete_document(filepath)       — remove only Qdrant vectors for a file

Can also be run directly as a script to batch-index the documents directory:
  python ingest/index_documents.py
"""

import logging
import time
import uuid
from pathlib import Path

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
from common.paths import has_allowed_extension, normalize_extensions, normalize_path
from common.types import IndexDecision
from indexer.fingerprint_store import delete_hash
from ingest.chunkers import chunk_document
from common.qdrant import get_qdrant_client
from settings import (
    ALLOWED_EXTENSIONS,
    COLLECTION,
    DOCS_PATH,
    MAX_FILE_SIZE,
    VECTOR_SIZE,
)

logger = logging.getLogger(__name__)
_ALLOWED_EXTENSIONS = normalize_extensions(ALLOWED_EXTENSIONS)


def ensure_collection() -> None:
    client = get_qdrant_client()
    if not client.collection_exists(COLLECTION):
        logger.info("Collection missing — creating new collection")
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def _read_file(path: Path) -> str | None:
    """Return file text, or None if the file should be skipped."""
    if path.stat().st_size > MAX_FILE_SIZE:
        logger.info("Skipping large file: %s", path)
        return None
    try:
        text = path.read_text(errors="ignore")
        logger.debug("Read %s with errors='ignore'", path)
        return text
    except Exception as e:
        logger.warning("Skipping unreadable file %s: %s", path, e)
        return None


def _embed_chunks(path: Path, chunks: list[str], document_id: str) -> list[PointStruct]:
    """Embed each chunk and return PointStructs with metadata."""
    points = []
    normalized = normalize_path(path)
    for i, chunk in enumerate(chunks):
        vec = embed(chunk)
        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vec,
                payload={
                    "text": chunk,
                    "document_id": document_id,
                    "filename": path.name,
                    "filepath": normalized,
                    "chunk_index": i,
                    "chunk_total": 0,  # corrected below once all chunks are embedded
                },
            )
        )
    for p in points:
        p.payload["chunk_total"] = len(points)
    return points


def _upsert_chunks(path: Path, points: list[PointStruct]) -> IndexDecision:
    """Upsert replacement points then delete stale vectors for path."""
    filepath = normalize_path(path)
    index_version = str(uuid.uuid4())
    for p in points:
        p.payload["index_version"] = index_version
        p.payload["active"] = True
    client = get_qdrant_client()
    try:
        client.upsert(collection_name=COLLECTION, points=points)
    except Exception as e:
        logger.error("Upsert failed for %s: %s", path, e)
        return IndexDecision.FAILED
    try:
        client.delete(
            collection_name=COLLECTION,
            points_selector=Filter(
                must=[FieldCondition(key="filepath", match=MatchValue(value=filepath))],
                must_not=[
                    FieldCondition(key="index_version", match=MatchValue(value=index_version))
                ],
            ),
        )
    except Exception as e:
        logger.error(
            "Stale vector cleanup failed for %s — fingerprint not updated, "
            "will retry on next poll: %s", path, e,
        )
        return IndexDecision.FAILED
    return IndexDecision.INDEXED


def index_file(path: Path) -> IndexDecision:
    ensure_collection()
    normalized_path = normalize_path(path)

    elapsed: dict[str, float] = {}

    t0 = time.monotonic()
    text = _read_file(path)
    elapsed["read"] = time.monotonic() - t0
    if text is None:
        return IndexDecision.SKIPPED

    t0 = time.monotonic()
    chunks = chunk_document(path, text)
    chunks = [c.strip() for c in chunks if c and c.strip()]
    elapsed["chunk"] = time.monotonic() - t0

    if not chunks:
        logger.info("No non-empty chunks for %s", path)
        return IndexDecision.SKIPPED

    document_id = str(uuid.uuid5(uuid.NAMESPACE_URL, normalized_path))

    t0 = time.monotonic()
    try:
        points = _embed_chunks(path, chunks, document_id)
    except Exception as e:
        logger.error("Embedding failed for %s: %s", path, e)
        return IndexDecision.FAILED
    elapsed["embed"] = time.monotonic() - t0

    if not points:
        logger.warning("No valid chunks to index for %s", path)
        return IndexDecision.FAILED

    t0 = time.monotonic()
    result = _upsert_chunks(path, points)
    elapsed["upsert"] = time.monotonic() - t0

    if result == IndexDecision.INDEXED:
        timing = " ".join(f"{k}={v:.2f}s" for k, v in elapsed.items())
        logger.info("Indexed %s: %d chunks — %s", path, len(points), timing)
    return result


def delete_document(filepath: Path | str) -> None:
    normalized_path = normalize_path(filepath)
    logger.info("Deleting vectors for: %s", normalized_path)
    get_qdrant_client().delete(
        collection_name=COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="filepath", match=MatchValue(value=normalized_path))]
        ),
    )


def remove_indexed_document(filepath: Path | str) -> None:
    """Delete Qdrant vectors and fingerprint record for a document."""
    delete_document(filepath)
    delete_hash(normalize_path(filepath))


def main() -> None:
    files = [
        p
        for p in DOCS_PATH.rglob("*")
        if p.is_file() and has_allowed_extension(p, _ALLOWED_EXTENSIONS)
    ]
    print(f"Found {len(files)} files to index")

    if not files:
        return

    for f in tqdm(files):
        index_file(f)


if __name__ == "__main__":
    main()
