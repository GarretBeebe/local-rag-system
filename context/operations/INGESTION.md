# Document Ingestion Pipeline

This document describes how documents are processed and indexed.

## Ingestion Flow

documents → loader → chunking → embedding → vector storage

## Document Sources

Documents are typically placed in:

documents/

Supported formats:

-   Markdown
-   Plain text
-   Source code
-   JSON
-   YAML
-   Configuration files

## Chunking Strategy

Example parameters:

chunk_size = 500\
chunk_overlap = 100

Reasons:

-   improves semantic search accuracy
-   prevents context truncation
-   improves retrieval granularity

## Metadata Schema

Each stored chunk includes:

document_id\
filename\
filepath\
chunk_index\
chunk_total

This metadata enables citation and debugging of retrieval results.

## Embedding Generation

Chunks are converted into vectors using the embedding model.

## Vector Storage

Embeddings and metadata are stored in the vector database.
