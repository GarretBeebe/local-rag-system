# Operations Guide

This document describes how to run and maintain the RAG system.

## Starting Infrastructure

Typical workflow:

1.  Start vector database
2.  Verify container health
3.  Confirm storage volumes

## Indexing Documents

1.  Place files in the documents directory
2.  Run the ingestion pipeline

This will chunk, embed, and store vectors.

## Running Queries

Queries retrieve context and generate answers using the local LLM.

## Debugging Retrieval

Helpful checks:

-   vector DB health
-   stored vector counts
-   metadata integrity
-   embedding generation

## Updating Models

Models can be replaced with stronger alternatives depending on hardware
capability.

## Maintenance

Recommended tasks:

-   periodic re-indexing
-   vector DB backups
-   monitoring retrieval quality
