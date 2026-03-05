"""
Document ingestion pipeline: loads files, splits them into overlapping chunks,
generates embeddings via Ollama, and upserts the results into Qdrant.

Exposes two public functions used by the filesystem watcher:
  - index_file(path)          — chunk, embed, and upsert a single file
  - delete_document(filepath) — remove all vectors belonging to a file

Can also be run directly as a script to batch-index the documents directory:
  python ingest/index_documents.py
"""

import sys
import uuid
import requests
from pathlib import Path
from typing import List
from tqdm import tqdm
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from settings import ALLOWED_EXTENSIONS, COLLECTION, EMBED_MODEL, OLLAMA_BASE_URL, VECTOR_SIZE, qdrant_client


# Path used by the manual batch-indexing script (main).
# The watcher uses paths from watcher_config.yaml instead.
DOCS_PATH = Path(__file__).parent.parent / "documents"

splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
)


def ensure_collection() -> None:
    if not qdrant_client.collection_exists(COLLECTION):
        print("Collection missing — creating new collection")
        qdrant_client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def embed(text: str) -> List[float]:
    r = requests.post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["embedding"]


def load_files() -> List[Path]:
    return [p for p in DOCS_PATH.rglob("*") if p.is_file() and p.suffix in ALLOWED_EXTENSIONS]


def index_file(path: Path) -> None:
    ensure_collection()

    text = path.read_text()
    chunks = splitter.split_text(text)
    document_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(path)))

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=embed(chunk),
            payload={
                "text": chunk,
                "document_id": document_id,
                "filename": path.name,
                "filepath": str(path),
                "chunk_index": i,
                "chunk_total": len(chunks),
            },
        )
        for i, chunk in enumerate(chunks)
    ]

    qdrant_client.upsert(collection_name=COLLECTION, points=points)


def delete_document(filepath: str) -> None:
    print(f"Deleting vectors for: {filepath}")
    qdrant_client.delete(
        collection_name=COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="filepath", match=MatchValue(value=filepath))]
        ),
    )


def main() -> None:
    files = load_files()
    print(f"Found {len(files)} files to index")

    if not files:
        return

    ensure_collection()

    for f in files:
        print(" ", f)

    for f in tqdm(files):
        index_file(f)


if __name__ == "__main__":
    main()
