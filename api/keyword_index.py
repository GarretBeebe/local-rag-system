"""
BM25 keyword index over the full Qdrant document collection.

KeywordIndex loads all stored chunks at startup and builds an in-memory
BM25 index for fast keyword-based recall. Used alongside vector search
in the hybrid retrieval pipeline.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rank_bm25 import BM25Okapi
from settings import COLLECTION, qdrant_client


class KeywordIndex:

    def __init__(self):
        self.docs = []
        self.meta = []

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
            if next_offset is None:
                break
            offset = next_offset

        self.bm25 = BM25Okapi(self.docs)

    def search(self, query: str, limit: int = 10):
        tokens = query.lower().split()
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(zip(scores, self.meta), reverse=True, key=lambda x: x[0])
        return [{"payload": payload, "bm25_score": score} for score, payload in ranked[:limit]]
