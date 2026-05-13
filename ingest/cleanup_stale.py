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


def cleanup_stale(accessible_roots: list[Path] | None = None) -> int:
    """Delete vectors and hashes for every tracked path that no longer exists.

    If accessible_roots is provided, only files whose parent watch root appears
    in that list are eligible for deletion. This prevents mass-deletion when a
    bind mount is temporarily empty or unavailable.

    Returns the number of entries removed."""
    paths = list_all_paths()
    removed = 0
    for filepath in paths:
        p = Path(filepath)
        if accessible_roots is not None and not any(
            p.is_relative_to(root) for root in accessible_roots
        ):
            continue
        if not p.exists():
            logging.info("Removing stale entry: %s", filepath)
            delete_document(filepath)
            delete_hash(filepath)
            removed += 1
    logging.info("Cleanup complete — removed %d stale entries", removed)
    return removed


if __name__ == "__main__":
    init_db()
    cleanup_stale()
