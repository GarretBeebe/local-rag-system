"""
Remove Qdrant vectors and fingerprint entries for files that no longer exist on disk.

Usage (from project root):
    python -m ingest.cleanup_stale

Via docker exec:
    docker exec rag-watcher python -m ingest.cleanup_stale
"""

import logging
from pathlib import Path

from common.index_state import bump_index_version
from common.index_state import init_db as init_index_state
from indexer.fingerprint_store import init_db, list_all_paths
from ingest.index_documents import remove_indexed_document

logger = logging.getLogger(__name__)


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
            logger.info("Removing stale entry: %s", filepath)
            remove_indexed_document(filepath)
            removed += 1
    if removed:
        bump_index_version()
    logger.info("Cleanup complete — removed %d stale entries", removed)
    return removed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    init_db()
    init_index_state()
    cleanup_stale()
