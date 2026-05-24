"""
Drops the Qdrant collection and clears the fingerprint store, permanently
removing all indexed vectors and re-index state.

Run this to wipe the knowledge base before re-indexing from scratch:
  python ingest/reset_collection.py

Pass --vectors-only to skip the fingerprint reset (rarely needed):
  python ingest/reset_collection.py --vectors-only
"""

import argparse

import ingest.index_documents as index_documents
from common.qdrant import get_qdrant_client
from indexer.fingerprint_store import clear_hashes, init_db
from settings import COLLECTION


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--vectors-only",
        action="store_true",
        help="Delete vectors only; leave fingerprints intact.",
    )
    args = parser.parse_args()

    client = get_qdrant_client()
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    index_documents._collection_ensured = False
    print("Vector collection removed.")

    if not args.vectors_only:
        init_db()
        clear_hashes()
        print("Fingerprint store cleared.")
    else:
        print("Fingerprint store left intact (--vectors-only).")


if __name__ == "__main__":
    main()
