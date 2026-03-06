"""
Project-level settings: all shared constants and infrastructure clients.

Values are hardcoded here as defaults. Override by setting environment
variables before running, or by editing this file directly.

Path resolution lives here so no other module needs __file__ manipulation:
    CONFIG_PATH — watcher config yaml
    DOCS_PATH   — manual batch-indexing document directory
"""

from pathlib import Path

from qdrant_client import QdrantClient

PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH = PROJECT_ROOT / "config" / "watcher_config.yaml"
DOCS_PATH = PROJECT_ROOT / "documents"

QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
COLLECTION = "documents"
VECTOR_SIZE = 768

OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
GEN_MODEL = "llama3.1:8b"
REASON_MODEL = "qwen2.5:14b"
CODE_MODEL = "qwen2.5-coder:14b"
RERANK_MODEL = "BAAI/bge-reranker-base"

ALLOWED_EXTENSIONS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml"}

qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
