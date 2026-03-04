from rank_bm25 import BM25Okapi
from qdrant_client import QdrantClient

COLLECTION = "documents"

client = QdrantClient(host="localhost", port=6333)


class KeywordIndex:

    def __init__(self):
        self.docs = []
        self.meta = []

        points = client.scroll(
            collection_name=COLLECTION,
            limit=10000,
            with_payload=True
        )[0]

        for p in points:
            text = p.payload["text"]
            tokens = text.lower().split()

            self.docs.append(tokens)
            self.meta.append(p.payload)

        self.bm25 = BM25Okapi(self.docs)

    def search(self, query, limit=10):

        tokens = query.lower().split()

        scores = self.bm25.get_scores(tokens)

        ranked = sorted(
            zip(scores, self.meta),
            reverse=True,
            key=lambda x: x[0]
        )

        results = []

        for score, payload in ranked[:limit]:
            results.append({
                "payload": payload,
                "bm25_score": score
            })

        return results
