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
from typing import Any, TypedDict

from rank_bm25 import BM25Okapi

from common.index_state import get_index_version
from common.index_state import init_db as init_index_state
from common.qdrant import get_qdrant_client
from settings import (
    COLLECTION,
    KEYWORD_INDEX_MAX_DOCS,
    KEYWORD_INDEX_MAX_TOKENS,
    KEYWORD_MAX_QUERY_TOKENS,
    KEYWORD_MIN_QUERY_TOKENS,
    KEYWORD_REFRESH_INTERVAL,
    KEYWORD_SEARCH_ENABLED,
)

logger = logging.getLogger(__name__)

_SCROLL_PAGE_SIZE = 1000


class KeywordResult(TypedDict):
    id: str | int
    payload: dict[str, Any]
    bm25_score: float


class KeywordIndex:
    def __init__(self, refresh_interval: int = KEYWORD_REFRESH_INTERVAL):
        self._lock = threading.Lock()
        self._refresh_interval = refresh_interval
        self._docs: list[list[str]] = []
        self._ids: list[str | int] = []
        self._bm25: BM25Okapi | None = None
        self.known_filenames: set[str] = set()
        self.doc_count = 0
        self.token_count = 0
        self.last_build_seconds = 0.0
        self.disabled_reason: str | None = None
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
        docs, ids, filenames = [], [], set()
        doc_count = 0
        token_count = 0
        disabled_reason = None
        offset = None
        client = get_qdrant_client()
        try:
            if not client.collection_exists(COLLECTION):
                self._replace_index(docs, ids, filenames)
                logger.info("KeywordIndex built: collection %s does not exist yet", COLLECTION)
                return
        except Exception as e:
            self._replace_index(docs, ids, filenames)
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
                tokens = text.lower().split()
                doc_count += 1
                token_count += len(tokens)
                if filename:
                    filenames.add(filename)
                if not KEYWORD_SEARCH_ENABLED:
                    disabled_reason = "KEYWORD_SEARCH_ENABLED=false"
                    continue
                if doc_count > KEYWORD_INDEX_MAX_DOCS:
                    disabled_reason = (
                        f"doc count {doc_count} exceeds KEYWORD_INDEX_MAX_DOCS="
                        f"{KEYWORD_INDEX_MAX_DOCS}"
                    )
                    docs, ids = [], []
                    continue
                if token_count > KEYWORD_INDEX_MAX_TOKENS:
                    disabled_reason = (
                        f"token count {token_count} exceeds KEYWORD_INDEX_MAX_TOKENS="
                        f"{KEYWORD_INDEX_MAX_TOKENS}"
                    )
                    docs, ids = [], []
                    continue
                if disabled_reason is None:
                    docs.append(tokens)
                    ids.append(p.id)
            if next_offset is None:
                break
            offset = next_offset
        if disabled_reason is not None:
            docs, ids, bm25 = [], [], None
        else:
            bm25 = BM25Okapi(docs) if docs else None
        elapsed = time.monotonic() - t0
        self._replace_index(
            docs,
            ids,
            filenames,
            bm25,
            doc_count=doc_count,
            token_count=token_count,
            elapsed=elapsed,
            disabled_reason=disabled_reason,
        )
        if disabled_reason is not None:
            logger.warning(
                "KeywordIndex disabled: %s; scanned %d docs/%d tokens in %.2fs",
                disabled_reason,
                doc_count,
                token_count,
                elapsed,
            )
        else:
            logger.info(
                "KeywordIndex built: %d docs/%d tokens in %.2fs",
                doc_count,
                token_count,
                elapsed,
            )

    def _replace_index(
        self,
        docs: list[list[str]],
        ids: list[str | int],
        filenames: set[str],
        bm25: BM25Okapi | None = None,
        doc_count: int | None = None,
        token_count: int | None = None,
        elapsed: float | None = None,
        disabled_reason: str | None = None,
    ) -> None:
        with self._lock:
            self._docs, self._ids, self._bm25 = docs, ids, bm25
            self.known_filenames = filenames
            self.doc_count = len(docs) if doc_count is None else doc_count
            self.token_count = sum(len(doc) for doc in docs) if token_count is None else token_count
            self.last_build_seconds = 0.0 if elapsed is None else elapsed
            self.disabled_reason = disabled_reason

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

    def search(self, query: str, limit: int = 10) -> list[KeywordResult]:
        with self._lock:
            bm25, ids = self._bm25, self._ids
        if bm25 is None:
            return []
        tokens = query.lower().split()[:KEYWORD_MAX_QUERY_TOKENS]
        if len(tokens) < KEYWORD_MIN_QUERY_TOKENS:
            return []
        scores = bm25.get_scores(tokens)
        pairs = [(s, pid) for s, pid in zip(scores, ids, strict=False) if s > 0]
        ranked = heapq.nlargest(limit, pairs, key=lambda x: x[0])
        if not ranked:
            return []
        top_ids = [pid for _, pid in ranked]
        points = get_qdrant_client().retrieve(
            collection_name=COLLECTION,
            ids=top_ids,
            with_payload=True,
            with_vectors=False,
        )
        payload_by_id = {p.id: p.payload for p in points}
        return [
            {"id": pid, "payload": payload_by_id.get(pid, {}), "bm25_score": score}
            for score, pid in ranked
        ]
