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
-   Windows 11

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

# Windows 11 Setup

## Prerequisites

| Requirement | Source | Notes |
| --- | --- | --- |
| Python 3.10+ | python.org | Check "Add to PATH" during install |
| Docker Desktop | docker.com | Enable WSL2 backend when prompted |
| Ollama | ollama.com/download | Installs as a Windows service automatically |

## First-Time Setup

Allow local PowerShell scripts to run (run once in an elevated terminal):

    Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

Install Python dependencies:

    pip install -e .

Pull Ollama models (same as Linux):

    ollama pull nomic-embed-text
    ollama pull llama3.1:8b
    ollama pull qwen2.5:14b
    ollama pull qwen2.5-coder:14b

## Starting Services

Open three separate PowerShell terminals and run one script in each:

    windows\Start-Qdrant.ps1
    windows\Start-Watcher.ps1
    windows\Start-ApiServer.ps1

Ctrl+C in any terminal stops that service.

## Auto-Start on Login (Windows Service Equivalent)

Run once in an elevated PowerShell window to register both services
with Windows Task Scheduler (starts automatically at each logon):

    windows\Install-Services.ps1

Manage tasks:

    # Stop
    Stop-ScheduledTask -TaskName "RAG-Watcher"
    Stop-ScheduledTask -TaskName "RAG-ApiServer"

    # Start manually
    Start-ScheduledTask -TaskName "RAG-Watcher"
    Start-ScheduledTask -TaskName "RAG-ApiServer"

    # Remove
    Unregister-ScheduledTask -TaskName "RAG-Watcher" -Confirm:$false
    Unregister-ScheduledTask -TaskName "RAG-ApiServer" -Confirm:$false

View registered tasks in the GUI: `taskschd.msc`

## Watch Path Configuration

The `~/` syntax in `watcher_config.yaml` resolves correctly on Windows
(`~` expands to `C:\Users\<username>`). For explicit Windows paths,
see the example config:

    config\watcher_config.windows.yaml

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

## Linux (systemd)

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

## Windows 11 (Task Scheduler)

See the **Windows 11 Setup → Auto-Start on Login** section above.

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

Start manually

    uvicorn web.api_server:app --host 0.0.0.0 --port 8000

------------------------------------------------------------------------

# Running the API Server as a Service

## Linux (systemd)

Create systemd service

    sudo nano /etc/systemd/system/rag-api.service

    [Unit]
    Description=Local RAG API Server
    After=network.target

    [Service]
    User=garret
    WorkingDirectory=/home/garret/Code/rag-system
    ExecStart=/home/garret/Code/rag-system/.venv/bin/uvicorn web.api_server:app --host 0.0.0.0 --port 8000
    Restart=always
    RestartSec=5

    [Install]
    WantedBy=multi-user.target

Enable service

    sudo systemctl daemon-reload
    sudo systemctl enable rag-api
    sudo systemctl start rag-api

Logs

    journalctl -u rag-api -f

## Windows 11 (Task Scheduler)

See the **Windows 11 Setup → Auto-Start on Login** section above.

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
