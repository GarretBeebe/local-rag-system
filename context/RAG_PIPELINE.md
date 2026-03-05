# RAG Pipeline

This document describes the full Retrieval-Augmented Generation (RAG)
pipeline implemented in this system.

The pipeline converts raw documents into searchable embeddings,
retrieves relevant context for user queries, and generates answers using
a local language model.

------------------------------------------------------------------------

# Pipeline Overview

The system uses a multi-stage architecture:

user question ↓ query embedding ↓ hybrid retrieval ├─ vector similarity
search └─ BM25 keyword search ↓ MMR diversification ↓ cross-encoder
reranking ↓ context assembly ↓ LLM generation ↓ answer with citations

------------------------------------------------------------------------

# Stage 1 --- Document Processing

Documents placed in the `documents/` directory are processed by the
ingestion pipeline.

## Flow

filesystem ↓ document loader ↓ text chunker ↓ embedding generation ↓
vector database storage

## Chunking

Documents are split into overlapping segments to improve retrieval.

Example configuration:

chunk_size = 500\
chunk_overlap = 100

Chunking improves:

-   semantic recall
-   context precision
-   LLM reasoning quality

------------------------------------------------------------------------

# Stage 2 --- Embedding Generation

Each document chunk is converted into a numerical vector.

The same embedding model is used for:

-   document chunks
-   user queries

This ensures vectors exist in the same semantic space.

Example workflow:

text chunk ↓ embedding model ↓ 768‑dimension vector ↓ stored in vector
database

------------------------------------------------------------------------

# Stage 3 --- Vector Database Storage

Each stored record includes:

vector embedding\
chunk text\
metadata

Example metadata:

document_id\
filename\
filepath\
chunk_index\
chunk_total

This enables:

-   source citations
-   debugging retrieval
-   document reconstruction

------------------------------------------------------------------------

# Stage 4 --- Query Processing

When a user asks a question, the system begins retrieval.

## Query Flow

question ↓ embedding model ↓ query vector ↓ retrieval pipeline

------------------------------------------------------------------------

# Stage 5 --- Hybrid Retrieval

Hybrid retrieval combines semantic and lexical search.

## Vector Search

Semantic similarity search using embeddings.

Advantages:

-   finds conceptual matches
-   works with paraphrased text
-   captures semantic relationships

## Keyword Search

BM25 keyword search over stored chunks.

Advantages:

-   exact identifier matches
-   filenames
-   configuration keys
-   code tokens

Hybrid retrieval improves recall for technical documents.

------------------------------------------------------------------------

# Stage 6 --- Diversification (MMR)

Maximal Marginal Relevance (MMR) reduces redundancy.

Goals:

-   avoid repeated chunks
-   increase topic diversity
-   improve context coverage

Example:

vector results → filtered to remove near‑duplicates.

------------------------------------------------------------------------

# Stage 7 --- Reranking

Candidate chunks are reranked using a cross‑encoder model.

The model evaluates:

(question, chunk)

pairs and assigns a relevance score.

The highest scoring chunks are selected as context.

Reranking significantly improves answer quality compared to raw vector
search.

------------------------------------------------------------------------

# Stage 8 --- Prompt Construction

Selected chunks are assembled into a prompt.

Example format:

\[S1\] file.md (chunk 2/10)\
chunk text

\[S2\] config.yaml (chunk 4/12)\
chunk text

The prompt instructs the language model to:

-   answer using only provided context
-   cite sources
-   avoid hallucinations

------------------------------------------------------------------------

# Stage 9 --- Generation

The prompt and question are sent to the local language model.

The LLM:

-   reads the provided context
-   synthesizes an answer
-   references the cited sources

Output:

answer text\
source references

------------------------------------------------------------------------

# Full Pipeline Diagram

documents ↓ chunking ↓ embedding generation ↓ vector database

user question ↓ query embedding ↓ hybrid retrieval ↓ MMR diversification
↓ cross‑encoder reranking ↓ context assembly ↓ LLM generation ↓ answer

------------------------------------------------------------------------

# Pipeline Benefits

This architecture provides:

semantic search\
keyword search\
diversified context\
high‑accuracy reranking\
local LLM reasoning

The result is a **high‑quality local RAG system** comparable to modern
production retrieval pipelines.

------------------------------------------------------------------------

# Future Improvements

Possible pipeline enhancements include:

query rewriting\
multi‑query retrieval\
context compression\
repository indexing\
agent‑based workflows\
real‑time document indexing
