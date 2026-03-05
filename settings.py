"""
Project-level settings: all shared constants and infrastructure clients.

Single source of truth for Qdrant connection details, Ollama model names,
and allowed file extensions. Used by api/, ingest/, and indexer/ modules.

Each sub-module adds the project root to sys.path before importing:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from settings import ...
"""

from qdrant_client import QdrantClient

QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
COLLECTION = "documents"
VECTOR_SIZE = 768

OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
GEN_MODEL = "qwen2.5-coder:14b"
RERANK_MODEL = "BAAI/bge-reranker-base"

ALLOWED_EXTENSIONS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml"}

qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
