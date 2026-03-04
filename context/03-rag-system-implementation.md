# Local AI RAG System -- Implementation Guide

This document describes how to extend the existing **Ollama + Caddy +
VPN AI gateway** into a full **Retrieval Augmented Generation (RAG)
knowledge system**.

The goal is to allow local models to answer questions using your own
documentation, repositories, and homelab configuration files.

------------------------------------------------------------------------

# System Overview

The RAG system sits alongside the existing Ollama inference server.

    Phone / Laptop
          ↓
    VPN
          ↓
    Caddy Gateway
          ↓
    RAG API Layer
          ↓
    Ollama Models
          ↓
    Vector Database
          ↓
    Document Sources

------------------------------------------------------------------------

# Goals

The system should:

-   Index personal documentation
-   Index Git repositories
-   Index homelab configuration
-   Index PDFs and design docs
-   Allow natural language search
-   Return grounded answers using local models

Example queries:

    Where is my Nextcloud docker-compose file?
    Summarize my Splunk architecture documentation.
    Show my Caddy configuration.

------------------------------------------------------------------------

# Recommended Stack

## LLM Inference

Already running:

    Ollama

Models:

  Model               Purpose
  ------------------- --------------------
  llama3.1:8b         general chat
  qwen2.5:14b         reasoning
  qwen2.5-coder:14b   code understanding

------------------------------------------------------------------------

## Embedding Model

Recommended Ollama embedding model:

    nomic-embed-text

Install:

    ollama pull nomic-embed-text

------------------------------------------------------------------------

## Vector Database

Recommended options:

### Qdrant (recommended)

Advantages:

-   very fast
-   simple API
-   great Python support
-   good for homelabs

Install with Docker:

    docker run -p 6333:6333 qdrant/qdrant

------------------------------------------------------------------------

# Document Sources

The ingestion pipeline should index:

### Engineering Documentation

-   Splunk architecture docs
-   engineering design documents
-   ADRs
-   architecture diagrams

### Homelab Configuration

-   Docker compose files
-   Caddyfiles
-   infrastructure scripts
-   server configuration

### Repositories

Example sources:

    ~/repos
    ~/projects

### Nextcloud Files

Possible ingestion sources:

    /nextcloud/data

### PDFs

Examples:

-   architecture docs
-   design specs
-   internal notes

------------------------------------------------------------------------

# Directory Layout

Example layout for the RAG system:

    ai-stack/
    │
    ├─ rag/
    │  ├─ ingest/
    │  ├─ embeddings/
    │  ├─ vector_db/
    │  └─ api/
    │
    ├─ documents/
    │  ├─ splunk_docs/
    │  ├─ homelab/
    │  ├─ repos/
    │  └─ pdfs/
    │
    └─ docker-compose.yml

------------------------------------------------------------------------

# Ingestion Pipeline

The ingestion process follows these steps:

    documents
       ↓
    chunk text
       ↓
    generate embeddings
       ↓
    store in vector DB

Example pipeline:

    documents → chunks → embeddings → vector database

------------------------------------------------------------------------

# Example Python Ingestion Script

    from langchain.document_loaders import DirectoryLoader
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    from langchain.embeddings import OllamaEmbeddings
    from langchain.vectorstores import Qdrant

    loader = DirectoryLoader("./documents")
    docs = loader.load()

    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_documents(docs)

    embeddings = OllamaEmbeddings(model="nomic-embed-text")

    vector_db = Qdrant.from_documents(
        chunks,
        embeddings,
        url="http://localhost:6333"
    )

------------------------------------------------------------------------

# Query Pipeline

User query flow:

    user question
         ↓
    embedding generated
         ↓
    vector search
         ↓
    relevant documents retrieved
         ↓
    context passed to LLM
         ↓
    final answer generated

------------------------------------------------------------------------

# Example Query

    question = "Where is my Nextcloud docker compose file?"

Pipeline:

1.  embed the question
2.  retrieve similar documents
3.  send context + question to the model

------------------------------------------------------------------------

# Agent Layer (Future)

Once RAG is working, an **agent layer** can be added.

Capabilities may include:

-   infrastructure troubleshooting
-   codebase search
-   configuration analysis
-   documentation summarization

------------------------------------------------------------------------

# Security Model

The RAG system inherits the security of the AI gateway.

Access allowed only via:

-   VPN
-   home network

Benefits:

-   no public inference endpoint
-   no public vector database
-   no exposed API keys

------------------------------------------------------------------------

# Operational Commands

Start vector database:

    docker start qdrant

Verify Ollama:

    ollama list

Test embedding model:

    ollama run nomic-embed-text

------------------------------------------------------------------------

# Future Improvements

Possible upgrades:

### Hybrid Search

Combine:

-   vector search
-   keyword search

### Metadata Indexing

Add metadata such as:

-   repo name
-   file path
-   service name

### Automated Ingestion

Automatically index:

-   new git commits
-   new Nextcloud files
-   updated docs

------------------------------------------------------------------------

# Final Result

When complete, the system becomes a **personal AI engineering
assistant** capable of answering questions about your entire technical
environment.

    Phone
      ↓
    VPN
      ↓
    AI Gateway
      ↓
    Ollama
      ↓
    Vector DB
      ↓
    Personal Knowledge Base
