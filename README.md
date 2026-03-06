# Local AI / RAG System

A **self‑hosted AI knowledge system** designed to run entirely on a
local machine or private network.\
It combines **local LLM inference, document chunking, hybrid retrieval,
reranking, and vector search** to enable semantic search and AI‑assisted
answers over locally indexed documents.

This project is designed for **private AI infrastructure** where
documents, models, and embeddings remain fully under the operator's
control.

------------------------------------------------------------------------

# Overview

This system enables:

-   Running local large language models
-   Generating embeddings for semantic search
-   Storing vectors in a local vector database
-   Incremental indexing via SHA-256 file fingerprinting
-   Hybrid retrieval (vector + keyword)
-   Cross‑encoder reranking
-   Filesystem watching for automatic re‑indexing

------------------------------------------------------------------------

# Query Pipeline

``` mermaid
flowchart TD

User --> Query[RAG Query Pipeline]

Query --> Embed[Query Embedding]
Embed --> Hybrid

Hybrid --> VectorSearch
Hybrid --> KeywordSearch

VectorSearch --> Merge
KeywordSearch --> Merge

Merge --> MMR[MMR Diversification]
MMR --> Rerank[Cross Encoder Reranking]

Rerank --> Prompt[Context + Question]
Prompt --> LLM[Local LLM]

LLM --> Response[Answer with Citations]
```

------------------------------------------------------------------------

# Ingestion Pipeline

``` mermaid
flowchart TD

Filesystem --> InitScan[Initial Scan on Startup]
Filesystem --> FSEvents[Filesystem Events watchdog]

InitScan --> Filter[Extension + Ignore Filter]
FSEvents --> Filter

Filter --> Queue[Index Worker Queue]
Queue --> HashCheck{Hash Changed?}

HashCheck -- No --> Skip[Skip]
HashCheck -- Yes --> Chunking[Document Chunking]
Chunking --> Embed[Embedding Generation]
Embed --> VectorStore[Qdrant Vector DB]
VectorStore --> UpdateHash[Update Fingerprint DB]

FSEvents -- Delete Event --> DeleteVectors[Delete Vectors from Qdrant]
DeleteVectors --> DeleteHash[Delete from Fingerprint DB]
```

The ingestion pipeline supports:

-   incremental indexing
-   automatic updates
-   deletion tracking
-   file fingerprinting

------------------------------------------------------------------------

# Project Structure

    rag-system/
    ├── api/
    │   ├── query_rag.py
    │   ├── retrieval.py
    │   └── keyword_index.py
    │
    ├── ingest/
    │   ├── chunkers.py
    │   ├── index_documents.py
    │   ├── reset_collection.py
    │
    ├── indexer/
    │   ├── watcher.py
    │   └── fingerprint_store.py
    │
    ├── config/
    │   └── watcher_config.yaml
    │
    ├── vector-db/
    │   └── qdrant/
    │
    ├── data/
    │   └── fingerprints.sqlite3
    │
    ├── web/
    │   └── api_server.py
    │
    ├── install.sh
    ├── settings.py
    └── README.md

------------------------------------------------------------------------

# Requirements

Operating System

-   Linux (recommended)
-   macOS

Software

-   Docker
-   Python 3.10+
-   Ollama
-   sqlite3

------------------------------------------------------------------------

# System Package Installation

Ubuntu / Debian

    sudo apt update
    sudo apt install docker.io docker-compose sqlite3

macOS

    brew install sqlite

sqlite3 stores **file fingerprints** used to detect file changes.

------------------------------------------------------------------------

# Bootstrap Installation

A bootstrap installer is included.

    ./install.sh

------------------------------------------------------------------------

# Manual Installation

Install Python dependencies

    pip install -e .

Start Qdrant

    cd vector-db/qdrant
    docker compose up -d

Pull models

    ollama pull nomic-embed-text
    ollama pull llama3.1:8b
    ollama pull qwen2.5:14b
    ollama pull qwen2.5-coder:14b

------------------------------------------------------------------------

# Models

Three Ollama models are configured in `settings.py`:

| Variable | Model | Purpose |
| --- | --- | --- |
| `GEN_MODEL` | `llama3.1:8b` | General chat and Q&A |
| `REASON_MODEL` | `qwen2.5:14b` | Reasoning and analysis |
| `CODE_MODEL` | `qwen2.5-coder:14b` | Code-related queries |

The RAG pipeline and API server use `GEN_MODEL` by default. To switch
to a different model, update the import in `api/query_rag.py`:

```python
from settings import CODE_MODEL as GEN_MODEL   # for code queries
from settings import REASON_MODEL as GEN_MODEL  # for reasoning tasks
```

------------------------------------------------------------------------

# Document Ingestion

Manual indexing

    python ingest/index_documents.py

Reset collection

    python ingest/reset_collection.py

------------------------------------------------------------------------

# Filesystem Watcher

Configuration file

    config/watcher_config.yaml

Example

    watch_paths:
      - path: ~/Nextcloud
      - path: ~/Code

    allowed_extensions:
      - .md
      - .txt
      - .py

    ignore_patterns:
      - .git
      - node_modules

------------------------------------------------------------------------

# Running the Watcher as a Service

Create systemd service

    sudo nano /etc/systemd/system/rag-watcher.service

    [Unit]
    Description=Local RAG Document Watcher
    After=network.target

    [Service]
    User=garret
    WorkingDirectory=/home/garret/Code/rag-system
    ExecStart=/home/garret/Code/rag-system/.venv/bin/python indexer/watcher.py
    Restart=always
    RestartSec=5

    [Install]
    WantedBy=multi-user.target

Enable service

    sudo systemctl daemon-reload
    sudo systemctl enable rag-watcher
    sudo systemctl start rag-watcher

Logs

    journalctl -u rag-watcher -f

------------------------------------------------------------------------

# Performance Notes

Current pipeline stages:

1.  Vector recall
2.  Keyword recall
3.  MMR diversification
4.  Cross‑encoder reranking

Typical improvements:

| Technique | Improvement |
| --- | --- |
| batch embeddings | 10–30x indexing speed |
| repository chunking | better code retrieval |
| caching embeddings | reduces recomputation |
| larger embedding model | higher semantic accuracy |

------------------------------------------------------------------------

# Developer Guide

## Adding New Chunking Strategies

Chunkers live in:

    ingest/chunkers.py

Example extension:

    def chunk_json(text):
        ...

Register in dispatcher:

    if suffix == ".json":
        return chunk_json(text)

------------------------------------------------------------------------

## Adding a Retrieval Strategy

Edit

    api/retrieval.py

Examples:

-   hybrid retrieval
-   graph retrieval
-   reranking models

------------------------------------------------------------------------

## Adding New Index Sources

Modify

    config/watcher_config.yaml

Example:

    watch_paths:
      - path: ~/Research

------------------------------------------------------------------------

# Security Model

Local‑first architecture:

-   models run locally
-   vector database local
-   documents never leave machine

------------------------------------------------------------------------

# Example Use Cases

-   personal knowledge base
-   engineering documentation search
-   AI assisted research
-   codebase exploration

------------------------------------------------------------------------

# Future Enhancements

Possible improvements:

-   web interface
-   multi‑agent workflows
-   repository semantic graphs
-   distributed indexing

------------------------------------------------------------------------

# License

MIT License
