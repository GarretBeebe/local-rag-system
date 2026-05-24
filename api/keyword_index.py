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
from common.qdrant import get_qdrant_client
from settings import COLLECTION, KEYWORD_REFRESH_INTERVAL

logger = logging.getLogger(__name__)

_SCROLL_PAGE_SIZE = 1000


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
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Build the initial index and start the background refresh thread.

        Call this from the application lifespan, not at import time.
        Guards against repeated calls so lifespan restarts don't leak threads.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        init_index_state()
        self._build()
        self._last_seen_version = get_index_version()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._refresh_loop, args=(self._refresh_interval,), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the refresh thread to exit and wait for it to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _build(self) -> None:
        t0 = time.monotonic()
        docs, payloads, ids, filenames = [], [], [], set()
        offset = None
        client = get_qdrant_client()
        try:
            if not client.collection_exists(COLLECTION):
                self._replace_index(docs, payloads, ids, filenames)
                logger.info("KeywordIndex built: collection %s does not exist yet", COLLECTION)
                return
        except Exception as e:
            self._replace_index(docs, payloads, ids, filenames)
            logger.warning("KeywordIndex collection check failed: %s", e, exc_info=True)
            return
        while True:
            points, next_offset = client.scroll(
                collection_name=COLLECTION,
                limit=_SCROLL_PAGE_SIZE,
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
        self._replace_index(docs, payloads, ids, filenames, bm25)
        logger.info("KeywordIndex built: %d docs in %.2fs", len(docs), elapsed)

    def _replace_index(
        self,
        docs: list[list[str]],
        payloads: list[dict[str, Any]],
        ids: list[str | int],
        filenames: set[str],
        bm25: BM25Okapi | None = None,
    ) -> None:
        with self._lock:
            self._docs, self._payloads, self._ids, self._bm25 = docs, payloads, ids, bm25
            self.known_filenames = filenames

    def _refresh_loop(self, interval: int) -> None:
        while not self._stop.wait(timeout=interval):
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
