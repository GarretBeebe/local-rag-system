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

## Why Hybrid Search

Vector search struggles with:

-   filenames
-   identifiers
-   exact configuration values

Keyword search fills these gaps.

## Diversification (MMR)

Removes redundant results and increases topical diversity.

## Reranking

Cross-encoder models score (question, chunk) pairs and reorder results
by relevance.

## Final Context Selection

Top-ranked chunks are passed to the language model.
