# Retrieval Pipeline

Modern RAG systems use multi-stage retrieval to improve answer quality.

## Retrieval Stages

1.  Hybrid recall
2.  Diversification
3.  Reranking
4.  Context selection

## Hybrid Recall

Combines:

Vector search (semantic)\
Keyword search (BM25)

### Vector Search

Finds conceptually related chunks.

### Keyword Search

Finds exact tokens such as identifiers or configuration keys.

BM25 runs inside Qdrant: every chunk carries a sparse vector that Qdrant builds
from its text at index time (`common/qdrant.py`), so keyword search is always in
sync with the index. Text is split on word characters before Qdrant tokenizes
it, which keeps identifiers like `retrieve_best` whole.

## Why Hybrid Search

Vector search struggles with:

-   filenames
-   identifiers
-   exact configuration values

Keyword search fills these gaps.

## Rank Fusion

Reciprocal Rank Fusion merges the vector and keyword result lists: a chunk
ranked well by both rises to the top. Chunks with identical text (the same
file indexed twice) count once, so repeats don't take several context slots.
The top `RERANK_K` fused candidates go to the reranker.

## Reranking

Cross-encoder models score (question, chunk) pairs and reorder results
by relevance.

## Final Context Selection

Top-ranked chunks are passed to the language model.
