# Document Ingestion Pipeline

This document describes how documents are processed and indexed.

## Ingestion Flow

documents → loader → chunking → embedding → vector storage

## Document Sources

The watcher indexes the folders listed in
`config/watcher_config.container.yaml` and keeps them in sync (it is the only
ingestion path).

Supported formats:

-   Markdown
-   Plain text
-   Source code
-   JSON
-   YAML
-   Configuration files

## Chunking Strategy

Chunks follow the document's structure:

-   Python: one segment per top-level function/class (decorators included)
-   Markdown: one segment per header section (`#` lines inside code fences
    are not headers)
-   Everything else: 500-character windows with 100 characters of overlap

Python and Markdown segments smaller than `CHUNK_SIZE` (500) are then packed
together with their neighbours, so a file's imports or a short section share
one chunk instead of producing many one-line chunks.

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

Chunks are converted into vectors using the embedding model, as-is. The
`search_query:` / `search_document:` prefixes recommended for nomic-embed-text
measured worse on this corpus (see `api/embed.py`). Changing embedding inputs or
the model requires a full reindex.

## Vector Storage

Embeddings and metadata are stored in the vector database.
