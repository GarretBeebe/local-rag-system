"""
Drops the Qdrant collection, permanently removing all indexed vectors.

Run this to wipe the knowledge base before re-indexing from scratch:
  python ingest/reset_collection.py
"""

from settings import COLLECTION, qdrant_client

if qdrant_client.collection_exists(COLLECTION):
    qdrant_client.delete_collection(COLLECTION)

print("Collection removed.")
