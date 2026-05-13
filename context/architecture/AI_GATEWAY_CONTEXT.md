# Local AI Gateway -- Context Summary

## Purpose

This document summarizes the setup and decisions made while configuring
a secure local AI inference server using Ollama, Caddy, and Chatbox.

The system allows mobile and desktop clients to access locally hosted
LLMs while keeping the inference server protected behind a VPN.

------------------------------------------------------------------------

## Hardware Environment

### AI Server

Ubuntu machine

CPU: Intel i7\
GPU: NVIDIA GeForce GTX 1050 Mobile\
RAM: 32 GB

Installed software: - Ollama - Docker - Caddy reverse proxy

------------------------------------------------------------------------

## Installed Models

  Model               Purpose
  ------------------- --------------
  llama3.1:8b         General chat
  qwen2.5:14b         Reasoning
  qwen2.5-coder:14b   Coding

Example verification command:

    curl http://localhost:11434/api/tags

------------------------------------------------------------------------

## Network Layout

    Phone / Laptop
          ↓
    VPN
          ↓
    Home Network
          ↓
    Caddy Reverse Proxy
          ↓
    Ubuntu AI Server
          ↓
    Ollama
          ↓
    Models

Host mapping:

  Host            IP              Purpose
  --------------- --------------- ---------------------
  Raspberry Pi    192.168.68.70   Caddy gateway
  Ubuntu server   192.168.68.85   Ollama model server

------------------------------------------------------------------------

## Domain Configuration

Domain:

    ai.spoonscloud.duckdns.org

DNS resolution handled internally through Pi‑hole.

------------------------------------------------------------------------

## Security Model

The final design removed public exposure of the inference endpoint.

Access is allowed only when:

-   connected to the home network
-   connected through VPN

Benefits:

-   no public AI endpoint
-   minimal attack surface
-   no API keys required

------------------------------------------------------------------------

## Caddy Gateway Configuration

    ai.spoonscloud.duckdns.org {

        reverse_proxy 192.168.68.85:11434 {
            flush_interval -1
        }

    }

The `flush_interval -1` option ensures streaming responses from Ollama
work properly.

------------------------------------------------------------------------

## Client Configuration

### Chatbox Mobile

Provider:

    Ollama

API Host:

    https://ai.spoonscloud.duckdns.org

Chatbox automatically calls:

    /api/tags

to discover available models.

------------------------------------------------------------------------

## Example Requests

List models:

    curl https://ai.spoonscloud.duckdns.org/api/tags

Generate text:

    curl http://localhost:11434/api/generate -d '{
      "model": "llama3.1:8b",
      "prompt": "Explain vector embeddings"
    }'

------------------------------------------------------------------------

## System Status

Working components:

-   Ollama model server
-   Caddy reverse proxy
-   VPN-protected access
-   Chatbox mobile connectivity
-   Streaming inference responses

The AI server is fully operational.
