# Local AI / RAG System

A **self-hosted AI knowledge system** designed to run entirely on a
local machine or private network. It combines **local LLM inference,
document chunking, hybrid retrieval, reranking, and vector search** to
enable semantic search and AI-assisted answers over locally indexed
documents.

This project is intended for **private environments, developer
experimentation, and personal AI infrastructure** where data remains
fully under the operator's control.

------------------------------------------------------------------------

# Overview

This system enables:

-   Running local large language models (LLMs)
-   Generating embeddings for semantic search
-   Storing vectors in a local vector database
-   Automatically chunking and indexing documents
-   Performing **hybrid retrieval (vector + keyword search)**
-   Reranking retrieved documents for higher accuracy
-   Producing answers augmented with document references

All components operate within a **private infrastructure environment**.

------------------------------------------------------------------------

# System Architecture

User\
│\
│ query\
▼\
RAG Query Pipeline\
│\
├── Query embedding → Local embedding model\
│\
├── Hybrid retrieval\
│ ├── Vector search → Vector database\
│ └── Keyword search → BM25 index\
│\
├── Diversification (MMR)\
│\
├── Cross-encoder reranking\
│\
└── Context + question → Local LLM\
│\
▼\
Response with references

This architecture follows modern **multi-stage RAG retrieval pipelines**
used in production AI systems.

------------------------------------------------------------------------

# Host Environment

The system runs on a **local compute host inside a private network**.

Typical environment characteristics:

  Component                Role
  ------------------------ ------------------------------------
  Local compute host       Runs inference and data services
  Container runtime        Manages infrastructure services
  Private network access   Restricts system exposure
  Local storage            Persists vector data and documents

All services communicate internally within the host environment.

------------------------------------------------------------------------

# Installed Models

Example local models that may be used in this system:

  Model Type         Purpose
  ------------------ -----------------------------------
  General LLM        Natural language reasoning
  Code-focused LLM   Technical and programming queries
  Embedding model    Vector generation for retrieval
  Reranker model     Cross-encoder relevance scoring

Typical configuration:

Embedding model → generates vectors for documents and queries\
Generation model → produces final responses\
Reranker model → improves retrieval accuracy

Specific models may vary depending on hardware capability.

------------------------------------------------------------------------

# Project Structure

```text
rag-system/
├── api/
│   ├── query_rag.py          # RAG query pipeline and LLM generation
│   ├── retrieval.py          # Hybrid retrieval, MMR, and reranking
│   └── keyword_index.py      # BM25 keyword index
├── ingest/
│   ├── index_documents.py    # Document chunking and vector ingestion
│   ├── reset_collection.py   # Wipe the Qdrant collection
│   └── test_rag.py           # Basic end-to-end test
├── indexer/
│   ├── __init__.py
│   └── watcher.py            # Filesystem watcher for auto-indexing
├── config/
│   └── watcher_config.yaml   # Watch paths, extensions, ignore patterns
├── documents/                # Indexed knowledge sources
├── requirements.txt          # Python dependencies
├── install.sh                # Dependency install script
└── README.md
```

Documents placed in the **documents directory** become part of the
searchable knowledge base once processed by the ingestion pipeline.

------------------------------------------------------------------------

# Requirements

Typical system requirements:

- Linux-based host
- Container runtime (Docker or equivalent)
- Python 3.10+
- Local LLM runtime (e.g. [Ollama](https://ollama.com))
- Qdrant vector database

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

# Installation

1. Clone the repository and navigate to the project directory.

2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

Or use the provided script:

```bash
bash install.sh
```

1. Start the Qdrant vector database (via Docker):

```bash
docker run -p 6333:6333 qdrant/qdrant
```

1. Pull the required Ollama models:

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5-coder:14b
```

The system assumes the ability to run local models and containerized
services.

------------------------------------------------------------------------

# Starting Infrastructure Services

Vector database services are typically started via container
orchestration.

Example workflow:

1.  Start vector database
2.  Verify service health
3.  Confirm storage volume initialization

After startup, the vector database will be ready to accept document
embeddings.

------------------------------------------------------------------------

# Document Ingestion Pipeline

Documents are processed through a multi-stage ingestion pipeline.

filesystem\
↓\
document loading\
↓\
text chunking\
↓\
embedding generation\
↓\
vector storage

### Chunking

Documents are split into overlapping chunks to improve retrieval
quality.

Example parameters:

chunk_size: \~500 tokens\
chunk_overlap: \~100 tokens

Each chunk is stored with metadata including:

-   document ID
-   filename
-   file path
-   chunk index
-   total chunks

This metadata allows reconstruction of original documents and traceable
citations.

------------------------------------------------------------------------

# Hybrid Retrieval

The system uses **hybrid search** combining semantic and keyword
retrieval.

### Vector Search

Uses semantic embeddings stored in the vector database to retrieve
conceptually related chunks.

### Keyword Search

Uses a lightweight **BM25 keyword index** to retrieve documents
containing exact terms, identifiers, or configuration keys.

Hybrid recall improves results for:

-   code
-   configuration files
-   logs
-   structured documentation

------------------------------------------------------------------------

# Retrieval Pipeline

Queries follow a multi-stage retrieval pipeline.

user query\
↓\
query embedding\
↓\
hybrid recall\
├─ vector similarity search\
└─ keyword search (BM25)\
↓\
MMR diversification\
↓\
cross-encoder reranking\
↓\
top ranked chunks

------------------------------------------------------------------------

# Reranking

Retrieved candidates are reranked using a **cross-encoder model**.

This model scores pairs of:

(question, document chunk)

The highest scoring chunks are selected as context for the LLM.

Reranking significantly improves answer accuracy compared to raw vector
search.

------------------------------------------------------------------------

# Query Workflow

The final RAG query process:

User question\
↓\
Embedding generation\
↓\
Hybrid retrieval\
↓\
Diversification (MMR)\
↓\
Cross-encoder reranking\
↓\
Prompt construction\
↓\
Local LLM generation\
↓\
Answer with citations

------------------------------------------------------------------------

# Example Use Cases

Typical scenarios for a local RAG system include:

-   Searching internal documentation
-   Querying engineering notes or research material
-   Exploring indexed knowledge sources
-   Investigating configuration files or logs
-   Experimenting with retrieval-augmented AI workflows

The system can be adapted for many knowledge retrieval tasks.

------------------------------------------------------------------------

# Security Model

This project is designed with **local-first security** in mind.

Key characteristics:

-   Private infrastructure deployment
-   No mandatory external services
-   Full local control of models and data
-   Optional network restrictions depending on environment

All data and embeddings remain under local control.

------------------------------------------------------------------------

# Future Enhancements

Potential areas for expansion include:

### Automated Indexing

-   automatic filesystem monitoring
-   background document ingestion
-   incremental indexing

### User Interfaces

-   chat interface
-   command-line interface
-   messaging platform integrations

### Advanced Retrieval

-   contextual compression
-   query expansion
-   repository indexing

### Agent Workflows

-   agent orchestration frameworks
-   multi-step reasoning pipelines
-   task automation

------------------------------------------------------------------------

# Goals

The objective of this project is to provide a foundation for:

-   Local AI experimentation
-   Retrieval-augmented generation research
-   Self-hosted AI infrastructure
-   Private knowledge systems

The architecture is intentionally **modular**, allowing components such
as retrieval methods, models, and indexing pipelines to evolve
independently.

------------------------------------------------------------------------

# License

MIT License
