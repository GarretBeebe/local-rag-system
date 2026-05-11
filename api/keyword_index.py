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

from settings import COLLECTION, qdrant_client

logger = logging.getLogger(__name__)


class KeywordIndex:

    def __init__(self, refresh_interval: int = 300):
        self._lock = threading.Lock()
        self._build()
        t = threading.Thread(target=self._refresh_loop, args=(refresh_interval,), daemon=True)
        t.start()

    def _build(self) -> None:
        docs, meta, ids = [], [], []
        offset = None
        while True:
            points, next_offset = qdrant_client.scroll(
                collection_name=COLLECTION,
                limit=1000,
                offset=offset,
                with_payload=True,
            )
            for p in points:
                docs.append(p.payload["text"].lower().split())
                meta.append(p.payload)
                ids.append(p.id)
            if next_offset is None:
                break
            offset = next_offset
        bm25 = BM25Okapi(docs) if docs else None
        with self._lock:
            self.docs, self.meta, self.ids, self.bm25 = docs, meta, ids, bm25

    def _refresh_loop(self, interval: int) -> None:
        while True:
            time.sleep(interval)
            try:
                self._build()
                logger.info("KeywordIndex refreshed (%d docs)", len(self.docs))
            except Exception as e:
                logger.warning("KeywordIndex refresh failed: %s", e)

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            bm25, ids, meta = self.bm25, self.ids, self.meta
        if bm25 is None:
            return []
        tokens = query.lower().split()
        scores = bm25.get_scores(tokens)
        pairs = ((s, pid, m) for s, pid, m in zip(scores, ids, meta) if s > 0)
        ranked = heapq.nlargest(limit, pairs, key=lambda x: x[0])
        return [{"id": pid, "payload": payload, "bm25_score": score} for score, pid, payload in ranked]
