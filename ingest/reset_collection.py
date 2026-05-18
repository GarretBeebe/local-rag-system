"""
Drops the Qdrant collection, permanently removing all indexed vectors.

Run this to wipe the knowledge base before re-indexing from scratch:
  python ingest/reset_collection.py
"""

from settings import COLLECTION, get_qdrant_client


def main() -> None:
    client = get_qdrant_client()
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    print("Collection removed.")


if __name__ == "__main__":
    main()
