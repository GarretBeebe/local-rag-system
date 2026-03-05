"""
Drops the Qdrant collection, permanently removing all indexed vectors.

Run this to wipe the knowledge base before re-indexing from scratch:
  python ingest/reset_collection.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from settings import COLLECTION, qdrant_client

if qdrant_client.collection_exists(COLLECTION):
    qdrant_client.delete_collection(COLLECTION)

print("Collection removed.")
