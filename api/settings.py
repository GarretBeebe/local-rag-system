from qdrant_client import QdrantClient

QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
COLLECTION = "documents"

OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
GEN_MODEL = "qwen2.5-coder:14b"
RERANK_MODEL = "BAAI/bge-reranker-base"

qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
