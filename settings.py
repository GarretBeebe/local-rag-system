"""
Project-level settings: all shared constants and infrastructure clients.

Values can be overridden via environment variables (useful for Docker deployments).
Defaults assume a bare-metal/local install with all services on localhost.

Path resolution lives here so no other module needs __file__ manipulation:
    CONFIG_PATH — watcher config yaml
    DOCS_PATH   — manual batch-indexing document directory
"""

import os
from pathlib import Path

from qdrant_client import QdrantClient

PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH = Path(
    os.environ.get("CONFIG_PATH", str(PROJECT_ROOT / "config" / "watcher_config.yaml"))
)
DOCS_PATH = PROJECT_ROOT / "documents"

QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
COLLECTION = "documents"
VECTOR_SIZE = 768

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# "strict"    — answer only from retrieved context; refuse if nothing found
# "augmented" — use context when found, fall back to model knowledge otherwise
RAG_MODE = os.environ.get("RAG_MODE", "augmented")
MMR_ENABLED = os.environ.get("MMR_ENABLED", "true").lower() != "false"
RAG_TIMING = os.environ.get("RAG_TIMING", "").lower() in ("1", "true")
API_KEY = os.environ.get("API_KEY", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "8"))
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",")]
EMBED_MODEL = "nomic-embed-text"
GEN_MODEL = "qwen2.5:14b"
RERANK_MODEL = "BAAI/bge-reranker-base"

ALLOWED_EXTENSIONS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml"}

MAX_FILE_SIZE = 1_000_000
MAX_EMBED_CHARS = 6000
MAX_CHUNK_CHARS = 2000
MAX_MD_CHUNK = 2000

qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
