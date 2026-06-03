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

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = Path(
    os.environ.get("CONFIG_PATH", str(PROJECT_ROOT / "config" / "watcher_config.container.yaml"))
)
DOCS_PATH = PROJECT_ROOT / "documents"

QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
COLLECTION = os.environ.get("QDRANT_COLLECTION", "documents")
VECTOR_SIZE = int(os.environ.get("VECTOR_SIZE", "768"))

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# "strict"    — answer only from retrieved context; refuse if nothing found
# "augmented" — use context when found, fall back to model knowledge otherwise
RAG_MODE = os.environ.get("RAG_MODE", "augmented")
MMR_ENABLED = os.environ.get("MMR_ENABLED", "true").lower() != "false"
RAG_TIMING = os.environ.get("RAG_TIMING", "").lower() in ("1", "true")
API_KEY = os.environ.get("API_KEY", "")
SESSION_EXPIRY_HOURS = int(os.environ.get("SESSION_EXPIRY_HOURS", "8"))
ALLOW_INSECURE_LOCALONLY = os.environ.get("ALLOW_INSECURE_LOCALONLY", "").lower() in ("1", "true")
_raw_trusted_proxies = os.environ.get("TRUSTED_PROXY_IPS", "")
TRUSTED_PROXY_IPS: set[str] = {ip.strip() for ip in _raw_trusted_proxies.split(",") if ip.strip()}
_raw_cors_origins = os.environ.get("CORS_ORIGINS", "")
CORS_ORIGINS = [o.strip() for o in _raw_cors_origins.split(",") if o.strip()]
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
GEN_MODEL = os.environ.get("GEN_MODEL", "qwen2.5:14b")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

ALLOWED_EXTENSIONS = {
    ".md", ".txt", ".py", ".json", ".yaml", ".yml", ".toml",
    ".ts", ".tsx", ".js", ".jsx",
}

RAG_INTERNAL_TOKEN: str | None = os.environ.get("RAG_INTERNAL_TOKEN") or None

MAX_FILE_SIZE = 1_000_000
MAX_EMBED_CHARS = 6000

# Ollama generation
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "16384"))

# API concurrency and rate limiting
RAG_EXECUTOR_WORKERS = int(os.environ.get("RAG_EXECUTOR_WORKERS", "4"))
RAG_CONCURRENCY_LIMIT = int(os.environ.get("RAG_CONCURRENCY_LIMIT", "4"))
GENERATION_CONCURRENCY_LIMIT = int(os.environ.get("GENERATION_CONCURRENCY_LIMIT", "1"))
RATE_WINDOW_SECONDS = float(os.environ.get("RATE_WINDOW_SECONDS", "60.0"))
RATE_MAX_REQUESTS = int(os.environ.get("RATE_MAX_REQUESTS", "30"))
RATE_MAX_LOGIN_REQUESTS = int(os.environ.get("RATE_MAX_LOGIN_REQUESTS", "10"))
STREAM_TIMEOUT_SECONDS = float(os.environ.get("STREAM_TIMEOUT_SECONDS", "120.0"))
RAG_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("RAG_REQUEST_TIMEOUT_SECONDS", "240.0"))
OLLAMA_GENERATE_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_GENERATE_TIMEOUT_SECONDS", "120.0"))
OLLAMA_EMBED_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_EMBED_TIMEOUT_SECONDS", "60.0"))
OLLAMA_MODEL_LIST_TIMEOUT_SECONDS = float(
    os.environ.get("OLLAMA_MODEL_LIST_TIMEOUT_SECONDS", "5.0")
)
OLLAMA_WARMUP_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_WARMUP_TIMEOUT_SECONDS", "60.0"))
OLLAMA_MAX_RETRIES = int(os.environ.get("OLLAMA_MAX_RETRIES", "2"))
OLLAMA_RETRY_DELAY_SECONDS = float(os.environ.get("OLLAMA_RETRY_DELAY_SECONDS", "1.0"))
WARM_MODELS_ON_STARTUP = os.environ.get("WARM_MODELS_ON_STARTUP", "").lower() in ("1", "true")
WATCHER_POLL_INTERVAL_SECONDS = float(os.environ.get("WATCHER_POLL_INTERVAL_SECONDS", "30"))

# Retrieval pipeline
RECALL_K = int(os.environ.get("RECALL_K", "15"))
MMR_K = int(os.environ.get("MMR_K", "12"))
FINAL_K = int(os.environ.get("FINAL_K", "4"))
MMR_LAMBDA_MULT = float(os.environ.get("MMR_LAMBDA_MULT", "0.7"))
KEYWORD_REFRESH_INTERVAL = int(os.environ.get("KEYWORD_REFRESH_INTERVAL", "30"))
KEYWORD_SEARCH_ENABLED = os.environ.get("KEYWORD_SEARCH_ENABLED", "true").lower() != "false"
KEYWORD_INDEX_MAX_DOCS = int(os.environ.get("KEYWORD_INDEX_MAX_DOCS", "100000"))
KEYWORD_INDEX_MAX_TOKENS = int(os.environ.get("KEYWORD_INDEX_MAX_TOKENS", "5000000"))
KEYWORD_MIN_QUERY_TOKENS = int(os.environ.get("KEYWORD_MIN_QUERY_TOKENS", "1"))
KEYWORD_MAX_QUERY_TOKENS = int(os.environ.get("KEYWORD_MAX_QUERY_TOKENS", "32"))
MAX_CHUNK_CHARS = int(os.environ.get("MAX_CHUNK_CHARS", "2000"))
MAX_MD_CHUNK = MAX_CHUNK_CHARS  # intentionally aliased — both chunkers enforce the same limit
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "100"))
MAX_CHAT_MESSAGES = int(os.environ.get("MAX_CHAT_MESSAGES", "200"))
MAX_CHAT_CONTENT_ITEMS = int(os.environ.get("MAX_CHAT_CONTENT_ITEMS", "32"))
MAX_CHAT_MESSAGE_CHARS = int(os.environ.get("MAX_CHAT_MESSAGE_CHARS", "8000"))
MAX_CHAT_TOTAL_CHARS = int(os.environ.get("MAX_CHAT_TOTAL_CHARS", "120000"))
MAX_CHAT_QUESTION_CHARS = int(os.environ.get("MAX_CHAT_QUESTION_CHARS", "12000"))
MAX_MODEL_NAME_CHARS = int(os.environ.get("MAX_MODEL_NAME_CHARS", "128"))


def _require_positive(name: str, value: int | float) -> None:
    if value <= 0:
        raise ValueError(f"settings: {name} must be > 0, got {value}")


def _validate_settings() -> None:
    for name, value in {
        "QDRANT_PORT": QDRANT_PORT,
        "VECTOR_SIZE": VECTOR_SIZE,
        "SESSION_EXPIRY_HOURS": SESSION_EXPIRY_HOURS,
        "RAG_EXECUTOR_WORKERS": RAG_EXECUTOR_WORKERS,
        "RAG_CONCURRENCY_LIMIT": RAG_CONCURRENCY_LIMIT,
        "GENERATION_CONCURRENCY_LIMIT": GENERATION_CONCURRENCY_LIMIT,
        "RATE_MAX_REQUESTS": RATE_MAX_REQUESTS,
        "RATE_MAX_LOGIN_REQUESTS": RATE_MAX_LOGIN_REQUESTS,
        "OLLAMA_NUM_CTX": OLLAMA_NUM_CTX,
        "RECALL_K": RECALL_K,
        "MMR_K": MMR_K,
        "FINAL_K": FINAL_K,
        "KEYWORD_REFRESH_INTERVAL": KEYWORD_REFRESH_INTERVAL,
        "KEYWORD_INDEX_MAX_DOCS": KEYWORD_INDEX_MAX_DOCS,
        "KEYWORD_INDEX_MAX_TOKENS": KEYWORD_INDEX_MAX_TOKENS,
        "KEYWORD_MIN_QUERY_TOKENS": KEYWORD_MIN_QUERY_TOKENS,
        "KEYWORD_MAX_QUERY_TOKENS": KEYWORD_MAX_QUERY_TOKENS,
        "MAX_CHUNK_CHARS": MAX_CHUNK_CHARS,
        "CHUNK_SIZE": CHUNK_SIZE,
        "MAX_CHAT_MESSAGES": MAX_CHAT_MESSAGES,
        "MAX_CHAT_CONTENT_ITEMS": MAX_CHAT_CONTENT_ITEMS,
        "MAX_CHAT_MESSAGE_CHARS": MAX_CHAT_MESSAGE_CHARS,
        "MAX_CHAT_TOTAL_CHARS": MAX_CHAT_TOTAL_CHARS,
        "MAX_CHAT_QUESTION_CHARS": MAX_CHAT_QUESTION_CHARS,
        "MAX_MODEL_NAME_CHARS": MAX_MODEL_NAME_CHARS,
    }.items():
        _require_positive(name, value)
    for name, value in {
        "RATE_WINDOW_SECONDS": RATE_WINDOW_SECONDS,
        "STREAM_TIMEOUT_SECONDS": STREAM_TIMEOUT_SECONDS,
        "RAG_REQUEST_TIMEOUT_SECONDS": RAG_REQUEST_TIMEOUT_SECONDS,
        "OLLAMA_GENERATE_TIMEOUT_SECONDS": OLLAMA_GENERATE_TIMEOUT_SECONDS,
        "OLLAMA_EMBED_TIMEOUT_SECONDS": OLLAMA_EMBED_TIMEOUT_SECONDS,
        "OLLAMA_MODEL_LIST_TIMEOUT_SECONDS": OLLAMA_MODEL_LIST_TIMEOUT_SECONDS,
        "OLLAMA_WARMUP_TIMEOUT_SECONDS": OLLAMA_WARMUP_TIMEOUT_SECONDS,
        "WATCHER_POLL_INTERVAL_SECONDS": WATCHER_POLL_INTERVAL_SECONDS,
        "OLLAMA_RETRY_DELAY_SECONDS": OLLAMA_RETRY_DELAY_SECONDS,
    }.items():
        _require_positive(name, value)
    if OLLAMA_MAX_RETRIES < 0:
        raise ValueError(f"settings: OLLAMA_MAX_RETRIES must be >= 0, got {OLLAMA_MAX_RETRIES}")
    if CHUNK_OVERLAP < 0:
        raise ValueError(f"settings: CHUNK_OVERLAP must be >= 0, got {CHUNK_OVERLAP}")
    if CHUNK_OVERLAP >= CHUNK_SIZE:
        raise ValueError(
            f"settings: CHUNK_OVERLAP must be smaller than CHUNK_SIZE, "
            f"got {CHUNK_OVERLAP} >= {CHUNK_SIZE}"
        )
    if MAX_CHUNK_CHARS < CHUNK_SIZE:
        raise ValueError(
            f"settings: MAX_CHUNK_CHARS must be >= CHUNK_SIZE, got {MAX_CHUNK_CHARS} < {CHUNK_SIZE}"
        )
    if not 0.0 <= MMR_LAMBDA_MULT <= 1.0:
        raise ValueError(
            f"settings: MMR_LAMBDA_MULT must be between 0.0 and 1.0, got {MMR_LAMBDA_MULT}"
        )
    if FINAL_K > MMR_K:
        raise ValueError(f"settings: FINAL_K must be <= MMR_K, got {FINAL_K} > {MMR_K}")
    if MMR_K > RECALL_K:
        raise ValueError(f"settings: MMR_K must be <= RECALL_K, got {MMR_K} > {RECALL_K}")
    if KEYWORD_MIN_QUERY_TOKENS > KEYWORD_MAX_QUERY_TOKENS:
        raise ValueError(
            "settings: KEYWORD_MIN_QUERY_TOKENS must be <= KEYWORD_MAX_QUERY_TOKENS, "
            f"got {KEYWORD_MIN_QUERY_TOKENS} > {KEYWORD_MAX_QUERY_TOKENS}"
        )
    if MAX_CHAT_QUESTION_CHARS > MAX_CHAT_TOTAL_CHARS:
        raise ValueError(
            "settings: MAX_CHAT_QUESTION_CHARS must be <= MAX_CHAT_TOTAL_CHARS, "
            f"got {MAX_CHAT_QUESTION_CHARS} > {MAX_CHAT_TOTAL_CHARS}"
        )
    if RAG_MODE not in ("strict", "augmented"):
        raise ValueError(f"settings: RAG_MODE must be 'strict' or 'augmented', got {RAG_MODE!r}")


_validate_settings()
