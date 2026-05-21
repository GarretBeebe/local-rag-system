"""
BM25 keyword index over the full Qdrant document collection.

KeywordIndex loads all stored chunks at startup and builds an in-memory
BM25 index for fast keyword-based recall. Used alongside vector search
in the hybrid retrieval pipeline.

The index refreshes itself in the background every `refresh_interval` seconds
so documents indexed by the watcher after API startup are included.
"""

import heapq
import logging
import threading
import time
from typing import Any

from rank_bm25 import BM25Okapi

from common.index_state import get_index_version
from common.index_state import init_db as init_index_state
from settings import COLLECTION, KEYWORD_REFRESH_INTERVAL, get_qdrant_client

logger = logging.getLogger(__name__)


class KeywordIndex:

    def __init__(self, refresh_interval: int = KEYWORD_REFRESH_INTERVAL):
        self._lock = threading.Lock()
        self._refresh_interval = refresh_interval
        self._docs: list[list[str]] = []
        self._payloads: list[dict[str, Any]] = []
        self._ids: list[str | int] = []
        self._bm25: BM25Okapi | None = None
        self.known_filenames: set[str] = set()
        self._last_seen_version: int | None = None

    def start(self) -> None:
        """Build the initial index and start the background refresh thread.

        Call this from the application lifespan, not at import time.
        """
        init_index_state()
        self._build()
        self._last_seen_version = get_index_version()
        t = threading.Thread(target=self._refresh_loop, args=(self._refresh_interval,), daemon=True)
        t.start()

    def _build(self) -> None:
        t0 = time.monotonic()
        docs, payloads, ids, filenames = [], [], [], set()
        offset = None
        while True:
            points, next_offset = get_qdrant_client().scroll(
                collection_name=COLLECTION,
                limit=1000,
                offset=offset,
                with_payload=True,
            )
            for p in points:
                filename = p.payload.get("filename", "")
                text = f"{filename} {p.payload.get('text', '')}"
                docs.append(text.lower().split())
                payloads.append(p.payload)
                ids.append(p.id)
                if filename:
                    filenames.add(filename)
            if next_offset is None:
                break
            offset = next_offset
        bm25 = BM25Okapi(docs) if docs else None
        elapsed = time.monotonic() - t0
        with self._lock:
            self._docs, self._payloads, self._ids, self._bm25 = docs, payloads, ids, bm25
            self.known_filenames = filenames
        logger.info("KeywordIndex built: %d docs in %.2fs", len(docs), elapsed)

    def _refresh_loop(self, interval: int) -> None:
        while True:
            time.sleep(interval)
            self._refresh_if_changed()

    def _refresh_if_changed(self) -> bool:
        """Rebuild BM25 only when the shared document index version changes."""
        try:
            version = get_index_version()
            if version == self._last_seen_version:
                return False
            self._build()
            self._last_seen_version = version
            return True
        except Exception as e:
            logger.warning("KeywordIndex refresh failed: %s", e, exc_info=True)
            return False

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            bm25, ids, payloads = self._bm25, self._ids, self._payloads
        if bm25 is None:
            return []
        tokens = query.lower().split()
        scores = bm25.get_scores(tokens)
        pairs = ((s, pid, pl) for s, pid, pl in zip(scores, ids, payloads, strict=False) if s > 0)
        ranked = heapq.nlargest(limit, pairs, key=lambda x: x[0])
        return [
            {"id": pid, "payload": payload, "bm25_score": score}
            for score, pid, payload in ranked
        ]
