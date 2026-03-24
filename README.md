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
    │       └── docker-compose.yml   ← standalone Qdrant only
    │
    ├── data/
    │   └── fingerprints.sqlite3
    │
    ├── web/
    │   └── api_server.py
    │
    ├── Dockerfile
    ├── docker-compose.yml           ← full stack (Qdrant + API + watcher)
    ├── .dockerignore
    ├── install.sh
    ├── settings.py
    └── README.md

------------------------------------------------------------------------

# Requirements

-   [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows, macOS, or Linux)

------------------------------------------------------------------------

# Docker for Windows

The full stack (Ollama, Qdrant, API server, filesystem watcher) can run in
Docker for Windows. Ollama runs as a container alongside the rest of the
stack — no separate Ollama install is required.

## Prerequisites

-   [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)
    with the WSL2 backend enabled

## 1. Configure the filesystem watcher

The watcher monitors directories for documents to index. You need to:

**a) Update `config/watcher_config.yaml`** to use container-side mount paths:

    watch_paths:
      - path: /mnt/nextcloud
        recursive: true
      - path: /mnt/code
        recursive: true

**b) Update the `watcher` volume mounts in `docker-compose.yml`** to map
your Windows directories to those container paths:

    volumes:
      - ./data:/app/data
      - C:/Users/YourName/Nextcloud:/mnt/nextcloud
      - C:/Users/YourName/Code:/mnt/code

Use forward slashes for Windows paths in Docker Compose.

## 3. Start the stack

    docker compose up -d

This starts four containers: `rag-ollama`, `rag-qdrant`, `rag-api`, and `rag-watcher`.

## 4. Pull Ollama models

Once the stack is running, pull the required models into the Ollama container:

    docker exec rag-ollama ollama pull nomic-embed-text
    docker exec rag-ollama ollama pull llama3.1:8b
    docker exec rag-ollama ollama pull qwen2.5:14b
    docker exec rag-ollama ollama pull qwen2.5-coder:14b

Models are stored in the `ollama-models` Docker named volume and persist across restarts.

## 5. Verify

    docker compose ps

All four services should show status `running`.

    curl http://localhost:8000/

Expected: `{"status":"rag-api running"}`

## Logs

    docker compose logs -f api
    docker compose logs -f watcher

## Stopping

    docker compose down

Vector database data persists in the `qdrant-storage` Docker named volume.
Fingerprint data persists in `./data/` on the host.

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

    docker exec rag-api python ingest/index_documents.py

Reset collection

    docker exec rag-api python ingest/reset_collection.py

------------------------------------------------------------------------

# Filesystem Watcher

Configuration file

    config/watcher_config.yaml

Use container-side mount paths in `watcher_config.yaml` (matching the volume mounts in `docker-compose.yml`):

    watch_paths:
      - path: /mnt/nextcloud
        recursive: true
      - path: /mnt/code
        recursive: true

    allowed_extensions:
      - .md
      - .txt
      - .py

    ignore_patterns:
      - .git
      - node_modules

------------------------------------------------------------------------

# API Server

`web/api_server.py` exposes an OpenAI-compatible REST API so any
OpenAI-compatible client can query the local knowledge base.

Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Health check |
| `GET` | `/v1/models` | List available models |
| `GET` | `/models` | Alias for `/v1/models` |
| `POST` | `/v1/chat/completions` | RAG-backed chat completion (supports `"stream": true`) |
| `POST` | `/chat/completions` | Alias for `/v1/chat/completions` |

------------------------------------------------------------------------

# Chat Clients

Any OpenAI-compatible chat client can connect to the API server.
Point the client at `http://<host>:8000` and select the model
`llama3.1:8b` (or whichever model is set as `GEN_MODEL`).

Recommended clients:

| Client | Notes |
| --- | --- |
| **Open WebUI** | Full-featured web UI, runs in Docker |
| **Chatbox** | Desktop app for macOS, Windows, Linux |
| **LangChain** | Programmatic access via `ChatOpenAI` |

Open WebUI quick start

    docker run -d -p 3000:8080 \
      -e OPENAI_API_BASE_URL=http://host.docker.internal:8000/v1 \
      -e OPENAI_API_KEY=local \
      ghcr.io/open-webui/open-webui:main

Chatbox configuration

-   API Mode: OpenAI API
-   API Host: `http://localhost:8000`
-   API Key: `local` (any non-empty value)
-   Model: `llama3.1:8b`

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
