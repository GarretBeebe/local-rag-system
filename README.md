# Local AI / RAG System

A **self-hosted AI knowledge system** designed to run entirely on a local machine or private network. It combines **local LLM inference, document chunking, hybrid retrieval, reranking, and vector search** to enable semantic search and AI-assisted answers over locally indexed documents.

This project is intended for **private environments, developer experimentation, and personal AI infrastructure** where data remains fully under the operator's control.

------------------------------------------------------------------------

## Overview

This system enables:

- Running local large language models (LLMs)
- Generating embeddings for semantic search
- Storing vectors in a local vector database
- Automatically chunking and indexing documents
- Performing **hybrid retrieval (vector + keyword search)**
- Reranking retrieved documents for higher accuracy
- Producing answers augmented with document references
- **Filesystem watching** for automatic background re-indexing

All components operate within a **private infrastructure environment**.

------------------------------------------------------------------------

## System Architecture

```text
User
│
│ query
▼
RAG Query Pipeline
│
├── Query embedding → Local embedding model
│
├── Hybrid retrieval
│   ├── Vector search → Qdrant vector database
│   └── Keyword search → BM25 index
│
├── Diversification (MMR)
│
├── Cross-encoder reranking
│
└── Context + question → Local LLM
│
▼
Response with references
```

This architecture follows modern **multi-stage RAG retrieval pipelines** used in production AI systems.

------------------------------------------------------------------------

## Project Structure

```text
rag-system/
├── settings.py               # Shared constants and Qdrant client (project-wide)
├── api/
│   ├── query_rag.py          # RAG query pipeline and LLM generation
│   ├── retrieval.py          # Hybrid retrieval, MMR, and reranking
│   └── keyword_index.py      # BM25 keyword index
├── ingest/
│   ├── index_documents.py    # Document chunking and vector ingestion
│   ├── reset_collection.py   # Wipe the Qdrant collection
│   └── test_rag.py           # Basic end-to-end smoke test
├── indexer/
│   ├── __init__.py
│   └── watcher.py            # Filesystem watcher for auto-indexing
├── config/
│   └── watcher_config.yaml   # Watch paths, extensions, ignore patterns
├── vector-db/
│   └── qdrant/
│       ├── docker-compose.yml  # Qdrant container configuration
│       └── storage/            # Persisted vector data (gitignored)
├── documents/                # Indexed knowledge sources
├── pyproject.toml            # Project metadata and dependencies
├── install.sh                # Dependency install script
└── README.md
```

The paths watched for indexing are configured in `config/watcher_config.yaml`. Any file matching the allowed extensions in a configured watch path will be processed into the knowledge base.

------------------------------------------------------------------------

## Requirements

- macOS or Linux host
- Docker with Docker Compose
- Python 3.10+
- [Ollama](https://ollama.com) for local LLM and embedding inference

**Python dependencies:**

| Package | Purpose |
| --- | --- |
| `qdrant-client` | Vector database client |
| `sentence-transformers` | Cross-encoder reranking |
| `rank-bm25` | Keyword search index |
| `langchain-text-splitters` | Document chunking |
| `watchdog` | Filesystem monitoring |
| `pyyaml` | Config file parsing |
| `tqdm` | Ingestion progress display |
| `requests` | Ollama API communication |

------------------------------------------------------------------------

## Installation

**1. Install the project and all dependencies:**

```bash
pip install -e .
```

This installs all dependencies declared in `pyproject.toml` and registers the project root on `sys.path`, so all modules resolve each other cleanly without any path manipulation.

**2. Start the Qdrant vector database:**

```bash
cd vector-db/qdrant
docker compose up -d
```

This starts Qdrant on ports `6333` (HTTP) and `6334` (gRPC), with vector data persisted to `vector-db/qdrant/storage/`.

**3. Pull the required Ollama models:**

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:14b
```

------------------------------------------------------------------------

## Document Ingestion

Documents are processed through a multi-stage ingestion pipeline:

```text
filesystem
↓
document loading
↓
text chunking (RecursiveCharacterTextSplitter)
↓
embedding generation (Ollama)
↓
vector storage (Qdrant)
```

Run the ingestion pipeline manually:

```bash
python ingest/index_documents.py
```

To wipe and reset the collection:

```bash
python ingest/reset_collection.py
```

### Chunking

Documents are split into overlapping chunks to improve retrieval quality:

- `chunk_size`: ~500 tokens
- `chunk_overlap`: ~100 tokens

Each chunk is stored with metadata: document ID, filename, file path, chunk index, and total chunks. This enables traceable citations in answers.

------------------------------------------------------------------------

## Filesystem Watcher

The `indexer/watcher.py` module monitors configured directories for file changes and automatically re-indexes documents in the background.

Configure watch paths, file extensions, and ignore patterns in `config/watcher_config.yaml`.

------------------------------------------------------------------------

## Hybrid Retrieval

The system uses **hybrid search** combining semantic and keyword retrieval.

### Vector Search

Uses semantic embeddings stored in Qdrant to retrieve conceptually related chunks.

### Keyword Search

Uses a lightweight **BM25 keyword index** to retrieve documents containing exact terms, identifiers, or configuration keys.

Hybrid recall improves results for code, configuration files, logs, and structured documentation.

------------------------------------------------------------------------

## Retrieval Pipeline

```text
user query
↓
query embedding
↓
hybrid recall
├─ vector similarity search (Qdrant)
└─ keyword search (BM25)
↓
MMR diversification
↓
cross-encoder reranking
↓
top ranked chunks → LLM prompt
↓
answer with citations
```

------------------------------------------------------------------------

## Example Use Cases

- Searching internal documentation
- Querying engineering notes or research material
- Exploring indexed knowledge sources
- Investigating configuration files or logs
- Experimenting with retrieval-augmented AI workflows

------------------------------------------------------------------------

## Security Model

This project is designed with **local-first security** in mind:

- Private infrastructure deployment
- No mandatory external services
- Full local control of models and data
- Telemetry disabled in the Qdrant container configuration

All data and embeddings remain under local control.

------------------------------------------------------------------------

## Future Enhancements

### User Interfaces

- Chat interface
- Command-line interface
- Messaging platform integrations

### Advanced Retrieval

- Contextual compression
- Query expansion
- Repository indexing

### Agent Workflows

- Agent orchestration frameworks
- Multi-step reasoning pipelines
- Task automation

------------------------------------------------------------------------

## Goals

The objective of this project is to provide a foundation for:

- Local AI experimentation
- Retrieval-augmented generation research
- Self-hosted AI infrastructure
- Private knowledge systems

The architecture is intentionally **modular**, allowing components such as retrieval methods, models, and indexing pipelines to evolve independently.

------------------------------------------------------------------------

## License

MIT License
