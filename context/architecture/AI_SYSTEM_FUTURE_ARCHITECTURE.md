# Local AI System -- Future Architecture

## Goal

Expand the current AI gateway into a personal knowledge system capable
of retrieving and reasoning over personal technical documentation.

------------------------------------------------------------------------

## Planned Components

### 1. Vector Database

Candidate technologies:

-   Qdrant
-   Chroma
-   Weaviate

Purpose:

-   store embeddings for documents
-   enable semantic search

------------------------------------------------------------------------

### 2. RAG (Retrieval Augmented Generation)

The RAG pipeline will index the following sources:

-   Splunk architecture documentation
-   engineering design documents
-   homelab configuration files
-   PDFs
-   Git repositories
-   Nextcloud contents

------------------------------------------------------------------------

## Future Architecture

    Phone / Laptop
          ↓
    VPN
          ↓
    AI Gateway (Caddy)
          ↓
    Agent / API Layer
          ↓
    Ollama Models
          ↓
    Vector Database
          ↓
    Document Sources

Document sources:

-   Splunk docs
-   homelab configs
-   PDFs
-   Git repos
-   Nextcloud

------------------------------------------------------------------------

## Example Future Queries

Examples of questions the system should answer:

    Where is my Nextcloud docker-compose file?
    Summarize my Splunk architecture notes.
    Show my Caddy configuration.

------------------------------------------------------------------------

## Benefits

-   Local AI inference
-   Personal knowledge retrieval
-   Engineering copilot capabilities
-   Secure VPN-only access

------------------------------------------------------------------------

## Next Development Steps

1.  Deploy a vector database (Qdrant recommended).
2.  Implement document ingestion pipeline.
3.  Embed documents using a local embedding model.
4.  Build a RAG query interface using Ollama.

This will convert the AI server into a full personal knowledge
assistant.
