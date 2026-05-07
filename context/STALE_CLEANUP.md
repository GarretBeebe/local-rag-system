# Stale Document Cleanup

## Problem

The watcher removes Qdrant vectors and fingerprint entries only via live `on_deleted`
filesystem events. If the watcher is down when files are deleted — or if a whole
directory (e.g. a stale git worktree) is removed — the entries linger indefinitely
in both Qdrant and the SQLite fingerprint store. This pollutes search results with
chunks from files that no longer exist.

## Approach

Two complementary mechanisms:

1. **Standalone script** — `python -m ingest.cleanup_stale` — runnable manually via
   `docker exec` whenever stale data is known to exist.
2. **Watcher startup sweep** — the same cleanup logic runs automatically at the start
   of each watcher restart, before `initial_scan`.

The fingerprint store (`data/fingerprints.sqlite3`) is the authoritative list of every
file ever indexed. Cleanup iterates that list, checks each path on disk, and removes
any entry whose file is gone.

## Changes

### 1. `indexer/fingerprint_store.py` — add `list_all_paths()`

```python
def list_all_paths() -> list[str]:
    """Return all filepaths currently tracked in the fingerprint store."""
    conn = _get_conn()
    rows = conn.execute("SELECT filepath FROM fingerprints").fetchall()
    return [row[0] for row in rows]
```

No schema changes needed — reads the existing `fingerprints` table.

### 2. `ingest/cleanup_stale.py` — new standalone script

```python
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
```

### 3. `indexer/watcher.py` — call cleanup on startup

In `main()`, add the cleanup sweep before `initial_scan`:

```python
from ingest.cleanup_stale import cleanup_stale

def main() -> None:
    init_db()
    config = load_config()
    worker = IndexWorker()
    handler = WatchHandler(config, worker)

    cleanup_stale()              # ← remove stale entries before scanning
    initial_scan(config["watch_paths"], handler)
    ...
```

## Sequencing

Cleanup runs before `initial_scan` so that stale fingerprints are purged first. This
prevents a race where `initial_scan` skips a new file because the fingerprint store
still holds a hash for a path that no longer exists at a different location.

## Testing

```bash
# 1. Index a test file
echo "test" > /tmp/test_stale.txt
docker exec rag-watcher python -c "
from ingest.index_documents import index_file
from indexer.fingerprint_store import upsert_hash, init_db
from pathlib import Path
init_db()
index_file(Path('/tmp/test_stale.txt'))
upsert_hash('/tmp/test_stale.txt', 'fakehash')
"

# 2. Delete the file
rm /tmp/test_stale.txt

# 3. Run cleanup and verify removal
docker exec rag-watcher python -m ingest.cleanup_stale
# Expected: "Removing stale entry: /tmp/test_stale.txt"
# Expected: "Cleanup complete — removed 1 stale entries"

# 4. Verify gone from Qdrant
curl -s "http://localhost:6333/collections/documents/points/scroll" \
  -H "Content-Type: application/json" \
  -d '{"limit":10,"with_payload":true,"filter":{"must":[{"key":"filepath","match":{"value":"/tmp/test_stale.txt"}}]}}' \
  | python3 -m json.tool
# Expected: "result": {"points": []}
```

## Risks

- **Mounted paths vs host paths**: Cleanup runs inside the container, so `Path.exists()`
  checks container paths (`/watch/Code/...`). If a volume is unmounted but the container
  is running, all files under that mount would appear missing and be purged. This is
  correct behavior — if the volume is gone, the data is stale — but worth being aware of
  before running cleanup with partially-mounted volumes.
- **Performance**: `list_all_paths()` fetches all rows; `Path.exists()` is fast for local
  and bind-mounted paths. At typical scale (tens of thousands of files) this completes in
  seconds. No concern.
