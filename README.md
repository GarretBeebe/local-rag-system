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
    │   ├── ollama_client.py     ← shared Ollama HTTP session
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
-   [Ollama](https://ollama.com) installed and running on the host

------------------------------------------------------------------------

# Docker Deployment

Qdrant, the API server, and the filesystem watcher run in Docker.
Ollama runs on the host for native GPU access — install it from
[ollama.com](https://ollama.com) and make sure it is running before
starting the stack. Works on Windows, macOS, and Linux.

## Prerequisites

-   [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS)
    or Docker Engine (Linux)
-   [Ollama](https://ollama.com) installed and running on the host

## 1. Configure the filesystem watcher

The watcher monitors directories for documents to index. You need to:

**a) Update `config/watcher_config.yaml`** to use container-side mount paths:

    watch_paths:
      - path: /mnt/nextcloud
        recursive: true
      - path: /mnt/code
        recursive: true

**b) Copy `.env.example` to `.env`** and set your host paths:

    # Windows
    NEXTCLOUD_PATH=C:/Users/YourName/Nextcloud
    CODE_PATH=C:/Users/YourName/Code

    # Linux / macOS
    NEXTCLOUD_PATH=/home/yourname/Nextcloud
    CODE_PATH=/home/yourname/Code

**c) Uncomment the volume mounts in `docker-compose.yml`** under the `watcher` service:

    volumes:
      - ./data:/app/data
      - ${NEXTCLOUD_PATH}:/mnt/nextcloud
      - ${CODE_PATH}:/mnt/code

## 3. Pull Ollama models

The embedding model is required. Pull it before starting the stack:

    ollama pull nomic-embed-text

Then pull whichever generation model(s) you want to use for chat and Q&A:

    ollama pull qwen2.5:14b        # default GEN_MODEL
    ollama pull llama3.1:8b
    ollama pull qwen2.5-coder:14b

Any model available in Ollama can be selected per-request; see the [Models](#models) section.

## 4. Start the stack

    docker compose up -d

This starts three containers: `rag-qdrant`, `rag-api`, and `rag-watcher`.

## 5. Verify

    docker compose ps

All three services should show status `running`.

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

Pull any models you want to use into Ollama — the API server exposes them
all via `/v1/models` and clients can select freely per request.

    curl http://localhost:8000/v1/models

`GEN_MODEL` in `settings.py` (default: `qwen2.5:14b`) is used only as a
fallback when Ollama cannot be reached at startup.

------------------------------------------------------------------------

# Query Modes

The query behavior is controlled by the `RAG_MODE` environment variable,
which defaults to `strict`.

| Mode | Behavior |
| --- | --- |
| `strict` | Answers only from retrieved context. If no relevant chunks are found, returns a "no context found" message rather than guessing. |
| `augmented` | Uses retrieved context when available and cites it. Supplements with the model's own knowledge when context is incomplete. Falls back to a direct model response if no context is found at all. |

Set the mode in your `.env` file:

    RAG_MODE=strict      # default — grounded answers with citations only
    RAG_MODE=augmented   # allows model to fill gaps with its own knowledge

**When to use each:**

- **`strict`** is the safer default for a personal knowledge base. Every
  answer traces back to an indexed document. The model will not invent
  details.
- **`augmented`** is useful when you want the model to remain helpful
  even on questions your documents don't fully cover. Be aware that
  answers may blend document content with the model's training data,
  making citations less authoritative.

The mode is forwarded into both the `api` and `watcher` containers via
`docker-compose.yml`. Changing it requires restarting the stack.

------------------------------------------------------------------------

# Performance Tuning

Two optional environment variables control retrieval behavior and
instrumentation. Set them in `.env` and restart the `api` container.

| Variable | Default | Description |
| --- | --- | --- |
| `RAG_TIMING` | `0` | Set to `1` to log per-stage timings (embed, recall, rerank, generate) on every request |
| `MMR_ENABLED` | `true` | Set to `false` to skip MMR diversification; Qdrant results are returned without vectors, reducing payload size and CPU work |

Enable timing to identify which pipeline stage dominates latency on your
hardware:

    RAG_TIMING=1 docker compose up -d api
    docker compose logs -f api   # look for embed/recall/rerank/generate lines

------------------------------------------------------------------------

# Document Ingestion

Manual indexing

    docker exec rag-api python ingest/index_documents.py

Reset collection

    docker exec rag-api python ingest/reset_collection.py

------------------------------------------------------------------------

# Filesystem Watcher

The watcher uses `watchdog`'s `PollingObserver`, which polls the
filesystem on a one-second interval. This ensures reliable detection of
new and modified files on all platforms, including WSL2-mounted Windows
paths (`/mnt/c/...`) where kernel inotify events are not delivered.

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
Point the client at `http://<host>:8000`. The model list is populated
dynamically from `GET /v1/models` — select any model already pulled in
Ollama.

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
-   Model: select from the list populated by `/v1/models`

------------------------------------------------------------------------

# Performance Notes

The query pipeline runs these stages in sequence:

1.  Query embedding (Ollama)
2.  Hybrid recall — Qdrant vector search + BM25 keyword search
3.  Deduplication by point ID
4.  MMR diversification (optional, see `MMR_ENABLED`)
5.  Cross-encoder reranking (CPU)
6.  Prompt assembly and LLM generation (Ollama, streamed)

Implemented latency improvements:

| Change | Effect |
| --- | --- |
| True Ollama streaming | First token delivered as generation starts, not after full completion |
| Shared HTTP session | TCP connections to Ollama reused across embed and generate calls |
| BM25 `heapq.nlargest` | Partial top-k sort replaces full O(n log n) sort on every query |
| Zero-score BM25 filter | Irrelevant keyword results excluded before reranking |
| Candidate deduplication | Vector and keyword overlap removed before cross-encoder |
| Reduced default candidate counts | recall\_k 30→15, mmr\_k 10→8, final\_k 6→4 |
| Optional MMR disable | `MMR_ENABLED=false` skips vector fetch and cosine work entirely |
| Per-stage timing | `RAG_TIMING=1` logs each stage's wall time for profiling |

------------------------------------------------------------------------

# Hardware Notes

> **These notes are specific to one hardware configuration (GMKtec NUCBox
> with AMD Radeon integrated graphics). Your GPU vendor, driver stack, and
> required steps will differ.**

## AMD Radeon iGPU on Windows (Vulkan backend)

By default, Ollama on Windows attempts to use AMD GPUs via the ROCm/HIP
backend. For discrete AMD GPUs (RX 6000/7000 series, etc.) this works
after installing the AMD HIP SDK. However, **AMD integrated GPUs (iGPUs)
found in Ryzen APUs are not supported by ROCm** — Ollama will detect the
device and then silently fall back to CPU.

The fix is to use Ollama's Vulkan backend instead. AMD has broad Vulkan
support across all GPU families including iGPUs.

### Steps

1.  Install the [AMD HIP SDK for Windows](https://www.amd.com/en/developer/rocm-hub/hip-sdk.html)
    (required even for the Vulkan path — Ollama's discovery process uses it)

2.  Set `OLLAMA_VULKAN=1` as a persistent Windows environment variable:

    ```powershell
    # In PowerShell (no admin required)
    [System.Environment]::SetEnvironmentVariable("OLLAMA_VULKAN", "1", "User")
    ```

3.  Fully quit and restart Ollama (right-click tray icon → Quit, then relaunch)

4.  Verify GPU is active:

    ```powershell
    ollama run llama3.1:8b --keepalive 2m
    # in a second terminal:
    ollama ps
    # should show: 100% GPU
    ```

### Why this works

Without `OLLAMA_VULKAN=1`, Ollama probes ROCm first and logs
`"filtering device which didn't fully initialize"` for iGPU targets like
`gfx1035`. With Vulkan enabled, the iGPU is enumerated correctly and all
available system memory shared with the GPU is visible to Ollama.

### Notes

-   AMD APUs use shared system RAM as VRAM. The amount visible to Ollama
    will be larger than the dedicated GPU memory reported by Windows
    (typically equal to total system RAM minus OS overhead).
-   This was tested on Ollama 0.23.1 with ROCm 7.1 on Windows 11.
    Future Ollama versions may add native iGPU support and make this
    unnecessary.
-   Other GPU vendors (NVIDIA, Intel Arc) have their own acceleration
    paths and do not need `OLLAMA_VULKAN=1`.

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
