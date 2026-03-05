"""
Minimal end-to-end smoke test for the RAG pipeline.

Embeds a single hardcoded document, stores it in Qdrant, then runs a
search query to verify that embedding, storage, and retrieval all work.
Run this after standing up Qdrant and pulling the embedding model.
"""

import sys
import uuid
from pathlib import Path
from qdrant_client.models import PointStruct
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from settings import COLLECTION, qdrant_client
from ingest.index_documents import embed, ensure_collection

def store_document(text: str) -> None:
    qdrant_client.upsert(
        collection_name=COLLECTION,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=embed(text),
                payload={"text": text},
            )
        ],
    )


def search(query: str) -> None:
    vector = embed(query)
    results = qdrant_client.query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=3,
    )
    for r in results.points:
        print(r.payload["text"], "score:", r.score)


if __name__ == "__main__":
    text = "Retrieval augmented generation combines vector search with language models."

    ensure_collection()
    store_document(text)
    search("What is RAG?")
