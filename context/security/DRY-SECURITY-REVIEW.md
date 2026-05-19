# DRY and Security Review

Date: 2026-05-13

Scope:
- Reviewed the main API, ingestion, watcher, auth, and web UI paths.
- Focused on issues worth fixing in this codebase now.
- Skipped lower-value rabbit holes like dependency CVEs, browser hardening edge cases, and theoretical model-prompt abuse unless there was a direct code smell behind them.

## High-priority findings

### 1. Deleted files can remain searchable because path handling is inconsistent

Why this matters:
- This is the most important issue I found.
- A file deleted from disk can fail to be deleted from Qdrant, which means stale or sensitive content may remain retrievable after the source file is gone.

Where:
- [ingest/index_documents.py](/Users/garret/Code/rag-system/ingest/index_documents.py:101) stores `filepath` as `str(path.resolve())`
- [ingest/index_documents.py](/Users/garret/Code/rag-system/ingest/index_documents.py:122) deletes by raw `str(filepath)`
- [indexer/watcher.py](/Users/garret/Code/rag-system/indexer/watcher.py:125) passes `event.src_path` directly on delete
- [indexer/fingerprint_store.py](/Users/garret/Code/rag-system/indexer/fingerprint_store.py:17) already normalizes paths for the fingerprint DB

Why it happens:
- Indexed payloads use resolved absolute paths.
- Delete events use whatever string the watcher emits.
- The fingerprint store already solved this with `_normalize()`, but Qdrant deletion did not reuse that logic.

Recommendation:
- Introduce one shared path-normalization helper and use it for:
  - stored fingerprint paths
  - Qdrant payload `filepath`
  - `delete_document()`
  - watcher enqueue/delete paths

This is both a DRY cleanup and a real security/privacy fix.

## Medium-priority findings

### 2. The API is easy to run without auth, and permissive CORS is the default

Why this matters:
- The codebase treats unauthenticated mode as acceptable by default.
- That is fine for strictly local development, but it is too easy to carry into a non-local deployment by accident.

Where:
- [settings.py](/Users/garret/Code/rag-system/settings.py:36) defaults `API_KEY` to empty
- [settings.py](/Users/garret/Code/rag-system/settings.py:37) defaults `JWT_SECRET` to empty
- [settings.py](/Users/garret/Code/rag-system/settings.py:39) defaults `CORS_ORIGINS` to `*`
- [web/api_server.py](/Users/garret/Code/rag-system/web/api_server.py:86) only logs a warning when auth is disabled
- [.env.example](/Users/garret/Code/rag-system/.env.example:15) and [.env.example](/Users/garret/Code/rag-system/.env.example:20) preserve the same insecure defaults

What I would change:
- Keep the current behavior for explicit local-dev mode only.
- Add a `DEV_MODE` or `ALLOW_INSECURE_LOCALONLY` flag and fail closed otherwise.
- Make `CORS_ORIGINS=*` opt-in, not the default.

This is not a code-execution bug. It is an operational security footgun.

### 3. Chat requests have no meaningful size bound, so a client can force expensive work

Why this matters:
- The login request has field length caps.
- The chat request does not cap message size, total payload size, or prompt size before work is dispatched into the RAG/LLM stack.
- A single client can send very large requests and tie up embedding, retrieval, reranking, and generation resources.

Where:
- [web/api_server.py](/Users/garret/Code/rag-system/web/api_server.py:161) defines `ChatRequest` with no size limits
- [web/api_server.py](/Users/garret/Code/rag-system/web/api_server.py:172) only checks that the last message is non-empty
- [web/api_server.py](/Users/garret/Code/rag-system/web/api_server.py:215) and [web/api_server.py](/Users/garret/Code/rag-system/web/api_server.py:263) push work into the executor once accepted

Recommendation:
- Cap question length at the API boundary.
- Reject oversized structured-message payloads before extraction.
- Consider a smaller per-IP rate limit for `/auth/login` and separate concurrency limits for generation endpoints.

This is a practical DoS issue, not just neatness.

## DRY opportunities worth doing

### 1. Duplicate SQLite store setup should be factored out

Where:
- [web/user_store.py](/Users/garret/Code/rag-system/web/user_store.py:18)
- [indexer/fingerprint_store.py](/Users/garret/Code/rag-system/indexer/fingerprint_store.py:22)

What is duplicated:
- thread-local connection handling
- `data/` directory creation
- `PRAGMA journal_mode=WAL`
- `PRAGMA synchronous=NORMAL`

Recommendation:
- Extract a small shared SQLite helper that opens a thread-local connection for a given DB path.

Why this is worth it:
- It reduces maintenance drift.
- It also makes the path-normalization fix easier to apply consistently.

### 2. File/path eligibility logic is spread across watcher and ingestion

Where:
- [ingest/index_documents.py](/Users/garret/Code/rag-system/ingest/index_documents.py:53)
- [indexer/watcher.py](/Users/garret/Code/rag-system/indexer/watcher.py:97)
- [indexer/watcher.py](/Users/garret/Code/rag-system/indexer/watcher.py:104)
- [ingest/cleanup_stale.py](/Users/garret/Code/rag-system/ingest/cleanup_stale.py:26)

Recommendation:
- Centralize:
  - canonical path normalization
  - allowed-extension checks
  - ignore-pattern checks
  - possibly file-size gating

Why this is worth it:
- The current split is exactly how the stale-delete bug slipped in.
- This is a good DRY target because it affects correctness, not just style.

### 3. `ask()` and `ask_stream_sync()` duplicate the same control flow

Where:
- [api/query_rag.py](/Users/garret/Code/rag-system/api/query_rag.py:74)
- [api/query_rag.py](/Users/garret/Code/rag-system/api/query_rag.py:94)

What is duplicated:
- retrieve chunks
- handle the "no context" branch
- build the prompt
- append sources

Recommendation:
- Split the shared pre-generation phase into a helper that returns one of:
  - direct question passthrough
  - no-context reply
  - prompt plus formatted sources

Why this is worth it:
- It is a clean simplification with low risk.
- It will make future behavior changes less error-prone across streaming and non-streaming paths.

## Lower-priority notes

### JWTs are stored in `localStorage`

Where:
- [web/index.html](/Users/garret/Code/rag-system/web/index.html:280)
- [web/index.html](/Users/garret/Code/rag-system/web/index.html:319)

Assessment:
- This is not the first thing I would fix here.
- The UI already sanitizes assistant HTML and sets a CSP, which reduces immediate exposure.
- If the web UI becomes a more serious product surface, move auth to `HttpOnly` cookies and avoid script-readable bearer storage.

## Recommended order of work

1. Fix path normalization once and use it everywhere vectors or fingerprints are stored/deleted.
2. Add hard request-size limits to chat endpoints.
3. Tighten auth/CORS defaults so insecure mode is explicit.
4. Extract shared SQLite and path-filter helpers.
5. Refactor `ask()` and `ask_stream_sync()` onto one shared preparation path.

## Not worth prioritizing right now

- Replacing the in-memory rate limiter with Redis or another distributed store.
- Deep CSP tightening while inline scripts remain in the single-file UI.
- Over-optimizing small alias endpoints like `/models` and `/chat/completions`.
- Theoretical prompt-injection defenses beyond the existing retrieval/prompt structure.
