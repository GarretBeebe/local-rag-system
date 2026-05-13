# System Architecture

This document describes the architecture of the local
Retrieval-Augmented Generation (RAG) system.

The goal of the architecture is to provide a fully local AI knowledge
system capable of retrieving relevant context from indexed documents and
generating accurate answers using local language models.

## High-Level Architecture

User\
│\
│ question\
▼

Query Service\
│\
├── Query embedding\
│\
├── Hybrid retrieval\
│ ├── Vector search (semantic)\
│ └── Keyword search (BM25)\
│\
├── Diversification (MMR)\
│\
├── Cross-encoder reranking\
│\
└── Context + question → Local LLM

▼

Answer with citations

## Core Components

### Local LLM Runtime

Responsible for interpreting questions and generating responses using
retrieved context.

### Embedding Model

Transforms queries and document chunks into vectors enabling semantic
search.

### Vector Database

Stores embeddings and metadata for document chunks enabling fast
similarity search.

### Retrieval Pipeline

Stages:

1.  Hybrid Recall (vector + keyword)
2.  Diversification (MMR)
3.  Cross-encoder reranking

## Data Flow

### Ingestion

filesystem → loader → chunker → embedding → vector DB

### Query

query → embedding → hybrid retrieval → MMR → reranking → prompt → LLM

## Design Principles

Local-first operation\
Modularity\
Hardware flexibility\
Extensibility\
Privacy
