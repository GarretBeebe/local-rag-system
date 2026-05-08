"""
Remove Qdrant vectors and fingerprint entries for files that no longer exist on disk.

Usage (from project root):
    python -m ingest.cleanup_stale

Via docker exec:
    docker exec rag-watcher python -m ingest.cleanup_stale
"""

import logging
from pathlib import Path

from indexer.fingerprint_store import delete_hash, init_db, list_all_paths
from ingest.index_documents import delete_document

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def cleanup_stale() -> int:
    """Delete vectors and hashes for every tracked path that no longer exists.
    Returns the number of entries removed."""
    paths = list_all_paths()
    removed = 0
    for filepath in paths:
        if not Path(filepath).exists():
            logging.info("Removing stale entry: %s", filepath)
            delete_document(filepath)
            delete_hash(filepath)
            removed += 1
    logging.info("Cleanup complete — removed %d stale entries", removed)
    return removed


if __name__ == "__main__":
    init_db()
    cleanup_stale()
