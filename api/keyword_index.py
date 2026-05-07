"""
BM25 keyword index over the full Qdrant document collection.

KeywordIndex loads all stored chunks at startup and builds an in-memory
BM25 index for fast keyword-based recall. Used alongside vector search
in the hybrid retrieval pipeline.
"""

import heapq
from typing import Any

from rank_bm25 import BM25Okapi

from settings import COLLECTION, qdrant_client


class KeywordIndex:

    def __init__(self):
        self.docs = []
        self.meta = []
        self.ids = []

        offset = None
        while True:
            points, next_offset = qdrant_client.scroll(
                collection_name=COLLECTION,
                limit=1000,
                offset=offset,
                with_payload=True,
            )
            for p in points:
                tokens = p.payload["text"].lower().split()
                self.docs.append(tokens)
                self.meta.append(p.payload)
                self.ids.append(p.id)
            if next_offset is None:
                break
            offset = next_offset

        self.bm25 = BM25Okapi(self.docs)

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        tokens = query.lower().split()
        scores = self.bm25.get_scores(tokens)
        pairs = ((s, pid, m) for s, pid, m in zip(scores, self.ids, self.meta) if s > 0)
        ranked = heapq.nlargest(limit, pairs, key=lambda x: x[0])
        return [{"id": pid, "payload": payload, "bm25_score": score} for score, pid, payload in ranked]
