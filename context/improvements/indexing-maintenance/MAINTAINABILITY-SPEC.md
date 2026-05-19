# Codebase Maintainability Technical Specification

This document is the implementation-level companion to `CODEBASE-MAINTAINABILITY-PLAN.md` in the same directory.
Each phase is grounded in specific file locations and concrete decisions. No code is written here.

---

## Phase 1: Fix Re-Indexing Semantics

**Problem (confirmed in code):**
- `index_file()` in `ingest/index_documents.py` creates new UUID4 point IDs and upserts without
  deleting existing vectors first. Changed files accumulate stale chunks in Qdrant.
- `index_file()` returns `None` for all outcomes: success, large-file skip, unreadable file,
  empty chunks, and all-chunks-failed. `watcher.py:83` calls `upsert_hash()` unconditionally
  after every `index_file()` call.
- `delete_document()` in `ingest/index_documents.py` already exists and uses `FieldCondition`
  with `MatchValue` on the `filepath` payload field — it should be called before upserting.

**Required changes:**

1. `ingest/index_documents.py` — `index_file()`:
   - Change return type from `None` to an explicit string literal:
     `Literal["indexed", "skipped", "failed"]`
   - Use a prepare-then-swap sequence to avoid data loss:
     1. Read, chunk, and embed all replacement chunks into memory first
     2. Only if all replacements are ready: call `delete_document(path)`, then upsert
     3. Never call `delete_document(path)` before replacements exist in memory
   - Qdrant operations are not transactional. Define and document an explicit failure policy:
     - If delete succeeds but upsert fails: log as `"failed"`, do not update fingerprint;
       document is now absent from the index until next successful re-index
     - If upsert partially succeeds: treat as `"failed"` and log which chunks were written;
       do not update fingerprint
   - Return `"skipped"` for: file too large, unreadable, empty chunks after stripping
   - Return `"failed"` if embedding produces no valid chunks (currently logs warning "No valid
     chunks to index" and returns `None`), or if any Qdrant operation fails
   - Return `"indexed"` only on successful delete + upsert
   - Fix `chunk_total` in payload: currently assigned from `len(chunks)` before embedding; some
     chunks may fail embedding, making the count wrong — assign after filtering

2. `indexer/watcher.py:83` — `IndexWorker._run()`:
   - Call `upsert_hash()` only when `index_file()` returns `"indexed"`
   - Log explicitly on `"skipped"` and `"failed"` outcomes

**Decision required before coding:**
If a re-index partially fails (some chunks embed, some don't), should the old document be
preserved or replaced with a partial index? Pick one and document it. Do not leave ambiguous.
The failure policy above assumes "absent until next success" — confirm this is acceptable before
coding.

**Regression tests (must exist before any other re-indexing refactor):**
- Index a file, modify it, re-index → Qdrant must contain exactly one version (filter by
  `filepath` payload field and count points)
- Fail all embeddings for a file → fingerprint hash must not be updated
- Successfully re-index after a previous failure → old chunks removed, new chunks present

---

## Phase 2: Improve Packaging and Build Reproducibility

**Problem (confirmed in code):**
- `pyproject.toml`: all 21 dependencies use floating latest; no dev dependencies declared (pytest,
  ruff already configured but not declared as a dep)
- `Dockerfile`: `COPY pyproject.toml .` followed immediately by `COPY . .` — any source file
  change invalidates the dependency layer
- Torch is installed from the PyTorch CPU index separately (`pip install torch --index-url ...`)
  before `pip install -e .`; a naive lock file will not capture the correct index URL

**Strategy decision (must be made before work starts):**
Choose one packaging approach:
- (a) `pip-compile` generating `requirements.txt` + separate `requirements-torch-cpu.txt`
- (b) `uv` with `uv.lock` and `[tool.uv.sources]` override to pin torch to the CPU index
- (c) Pin versions directly in `pyproject.toml` optional-dependencies (no lock file)

**Required changes:**

1. `pyproject.toml`:
   - Add `[project.optional-dependencies]` group `dev` with at minimum: `pytest`, `pytest-cov`
   - Ruff is already configured in `[tool.ruff]`; add it to dev deps
   - If using pip-compile: add usage instructions to README or Makefile

2. `Dockerfile`:
   - Reorder so dependency install comes before source copy:
     ```
     COPY pyproject.toml .
     RUN pip install torch --index-url ... && pip install -e .
     COPY . .
     ```
   - Do not install dev dependencies (`.[dev]`) in the runtime image. If local dev tooling
     inside the container is needed, use a separate `--target dev` build stage or a
     `docker-compose.override.yml` that mounts the source and runs `pip install -e ".[dev]"`.
   - Add `.dockerignore` excluding: `data/`, `*.sqlite3`, `__pycache__`, `.git`, `context/`

3. Lock file: generate and commit whichever format the strategy decision selects

---

## Phase 3: Add Real Automated Tests

**Problem (confirmed in code):**
- `ingest/test_rag.py` is a live smoke test that hits real Qdrant with no assertions; it will
  be discovered by pytest as a test file
- No `tests/` directory exists

**Required changes:**

1. Rename `ingest/test_rag.py` → `scripts/smoke_rag.py` and exclude from pytest discovery

2. Create `tests/` with unit tests (no Qdrant or Ollama required):
   - `test_chunking.py`: chunk count, overlap, empty input
   - `test_paths.py`: path normalization, ignore matching
   - `test_auth.py`: JWT creation, validation, expiry, invalid tokens
   - `test_retrieval.py`:
     - deduplication by ID (pure logic, no Qdrant)
     - MMR selection with known vectors (pure logic)
   - `test_request_validation.py`: size limits, malformed messages, structured content

3. Integration tests (require real Qdrant; marked `@pytest.mark.integration`):
   - **changed-file re-indexing leaves exactly one document version in Qdrant** ← highest
     priority; must be present before any retrieval or indexing refactor. These tests require
     live Qdrant to count points by `filepath` payload filter and belong here, not in unit tests.
   - **failed/empty re-index does not mark fingerprint as current**
   - Require real Qdrant and Ollama; skip automatically if unavailable
   - Run with `pytest -m integration`; unit-only run: `pytest -m "not integration"`

4. `pyproject.toml`:
   - Add `[tool.pytest.ini_options]` with `testpaths = ["tests"]`
   - Register `integration` marker

---

## Phase 4: Remove Import-Time Side Effects and Separate Runtime Clients

**Problem (confirmed in code):**

`settings.py` (line ~60-63):
- `qdrant_client = QdrantClient(...)` — module-level; constructing the client object is itself
  an import-time side effect, though the main network cost comes from the next point below

`api/retrieval.py` (lines 44-45):
- `reranker = CrossEncoder(RERANK_MODEL, device="cpu")` — blocks on model download at import
- `keyword_index = KeywordIndex()` — triggers `_build()` which scrolls all of Qdrant, and
  spawns a daemon refresh thread; this is the primary source of import-time network cost

`api/keyword_index.py` — `KeywordIndex.__init__()`:
- Calls `_build()` immediately (blocking full Qdrant scroll with `limit=1000` pagination)
- Spawns `_refresh_loop` daemon thread

These two problems are merged into one phase because fixing retrieval import side effects without
first removing the module-level `QdrantClient` from settings would leave an import-time client
construction in place even after moving `KeywordIndex` initialization.

**Required changes (in dependency order):**

1. `settings.py`:
   - Remove `qdrant_client = QdrantClient(...)` module-level instance
   - Replace with a factory function `get_qdrant_client() -> QdrantClient` or a `runtime.py`
     module with a lazy singleton
   - All modules that currently do `from settings import qdrant_client` switch to calling the
     factory

2. `api/keyword_index.py`:
   - Remove `_build()` call from `__init__`; remove thread spawn from `__init__`
   - Add explicit `start()` method that builds the index and begins the refresh loop
   - `search()` must return empty results (not crash) if `start()` has not been called yet

3. `api/retrieval.py`:
   - Remove module-level `CrossEncoder(...)` and `KeywordIndex()` instantiation
   - Move initialization into a `startup()` function or make each a lazy accessor
   - `keyword_index.start()` called from server lifespan, not at import

4. `web/api_server.py` lifespan:
   - Call `get_qdrant_client()`, `CrossEncoder(...)`, and `keyword_index.start()` during startup
   - This is already where `user_store` is initialized — add retrieval deps alongside it

**Files:** `settings.py`, `api/retrieval.py`, `api/keyword_index.py`, `web/api_server.py`,
`ingest/index_documents.py`, `indexer/fingerprint_store.py`

---

## Phase 5: Improve Keyword Index Scalability

**Problem (confirmed in code):**
- `KeywordIndex._build()` scrolls the entire Qdrant collection (paginated with `limit=1000`)
  on every refresh every 300s
- `_extract_filename()` in `retrieval.py` calls `list_all_paths()` from fingerprint store on
  every query — full SQLite table scan each call
- No logging of index size, rebuild duration, or refresh errors beyond a bare warning

**Cross-process constraint (must inform the design decision):**
The watcher and API run in separate Docker containers sharing `/app/data` on disk. They do not
share Python memory. Any approach that relies on in-memory state updated by the watcher process
will not be visible in the API process, and vice versa. This rules out:
- In-memory caches in `fingerprint_store.py` invalidated by watcher writes
- Feeding watcher add/remove events directly into `KeywordIndex` object state in the API process

**Design decision required before coding:**
Choose one cross-process-safe approach:
- (a) **Qdrant-derived periodic rebuild**: keep the current full-scroll refresh but add logging
  and move it out of startup blocking. This is the simplest option and avoids new infrastructure.
- (b) **SQLite event table**: watcher writes add/remove events to a shared SQLite table in
  `/app/data`; API process polls the table on each refresh cycle to apply incremental updates
  to the in-memory BM25 index without a full Qdrant scroll
- (c) **SQLite FTS5 as the keyword index**: replace BM25Okapi with SQLite FTS5 in a shared
  database; watcher writes to it directly; API reads from it — no separate rebuild cycle

Option (a) is lowest risk. Options (b) and (c) require careful SQLite WAL mode configuration
and read/write coordination across processes. Do not start without choosing one.

**Required changes (after design decision):**

1. `api/keyword_index.py`:
   - Add logging: document count, rebuild duration, Qdrant errors during refresh
   - If option (b): add SQLite event table polling during refresh cycle
   - If option (c): replace BM25Okapi with FTS5 queries against shared DB

2. `api/retrieval.py` — filename extraction:
   - `_extract_filename()` calls `list_all_paths()` (full SQLite table scan) on every query
   - Fix: cache the result inside `KeywordIndex` and refresh it on each rebuild cycle (not
     per-query). The cache lives in the API process only; it is repopulated from SQLite on each
     periodic refresh, which is safe since SQLite is the shared source of truth.

3. `indexer/fingerprint_store.py`:
   - Do not add an in-memory cache here — the watcher and API are separate processes and such a
     cache would be stale in the API. Instead, rely on the `KeywordIndex` refresh cycle
     (above) to repopulate filename state from SQLite at a controlled interval.

---

## Phase 6: Improve Ingestion Throughput

**Problem (confirmed in code):**
- `ingest/index_documents.py`: chunks are embedded one at a time in a sequential loop; no
  batching
- No per-stage timing (read, chunk, embed, upsert)
- No file-level concurrency

**Required changes:**

1. `api/embed.py`:
   - Verify whether Ollama `/api/embeddings` supports batched input before implementing
   - If yes: add `embed_batch(texts: list[str]) -> list[list[float]]`
   - If no: document why and leave `embed()` as-is; do not add dead code

2. `ingest/index_documents.py`:
   - Add per-file timing logs broken down by stage: read, chunk, embed, upsert
   - Add configurable file-level concurrency reading from `INGEST_CONCURRENCY` setting

3. `settings.py`:
   - Add `INGEST_CONCURRENCY` (default 1 — conservative to avoid overwhelming Ollama)
   - Add `EMBED_BATCH_SIZE` (default 1 unless Ollama supports batch)

---

## Phase 7: Split the Web API Module

**Problem (confirmed in code):**
`web/api_server.py` mixes: auth logic (`_is_valid_token`, `/auth/login`), rate limiting
(`_check_rate_limit` + in-memory counter dict), request/response schemas (`LoginRequest`,
`ChatMessage`, `ChatRequest`), SSE/OpenAI formatting (`_make_stream_chunk`,
`_build_chat_response`, `_extract_question_from_messages`), and all route handlers.

Additionally: `ThreadPoolExecutor(max_workers=4)` and `asyncio.Semaphore(4)` are created at
module level (line 63-64), before the event loop exists. This works today due to deferred binding
but is fragile under reload or fork.

**Do not start this phase until Phase 3 tests exist** — this refactor cannot be safely validated
by manual inspection alone.

**Required changes:**

1. `web/auth.py` (new): `_is_valid_token()`, JWT creation from `/auth/login`, user store calls
2. `web/rate_limit.py` (new): `_check_rate_limit()` and its in-memory counter dict
3. `web/schemas.py` (new): `LoginRequest`, `ChatMessage`, `ChatRequest` Pydantic models
4. `web/openai_compat.py` (new): `_make_stream_chunk()`, `_build_chat_response()`,
   `_extract_question_from_messages()`
5. `web/api_server.py`: keep app creation, lifespan, middleware wiring, and route handlers only
   - Move executor and semaphore creation into lifespan, not module level

---

## Phase 8: Replace Loose Dicts With Typed Data Structures

**Problem (confirmed in code):**

`api/retrieval.py:32`:
```python
Chunk: TypeAlias = dict[str, Any]
```

Chunks carry dual score fields that coexist on the same dict:
- `"score"`: set by `qdrant_recall()` (Qdrant similarity) or `hybrid_recall()` (BM25)
- `"rerank_score"`: added by `rerank()` (cross-encoder); original `"score"` is NOT removed

Magic string key access scattered across `retrieval.py` and `query_rag.py`:
- `c["vector"]` in `mmr_select()` — direct access, crashes if key missing
- `c["payload"]["text"]`, `c.get("rerank_score", 0)`, `c["id"]` — mix of direct and defensive
- `p["text"]`, `p["chunk_index"]`, `p.get("filepath")` in `query_rag.py`

**Required changes:**

1. `api/retrieval.py`:
   - Replace `Chunk = dict[str, Any]` with a dataclass:
     ```python
     @dataclass
     class Chunk:
         id: str | int
         payload: dict[str, Any]
         score: float              # Qdrant similarity or BM25 score
         rerank_score: float | None = None
         vector: list[float] | None = None
     ```
   - Update `qdrant_recall()`, `hybrid_recall()`, `rerank()`, `mmr_select()`, `retrieve_best()`
     to construct and consume `Chunk` objects
   - `rerank()` sets `chunk.rerank_score` instead of dict mutation

2. `api/query_rag.py`:
   - Update `_resolve_source()`, `build_prompt()`, `_format_sources()` to use `Chunk` objects
   - Payload access stays as `chunk.payload["text"]` — Qdrant payload remains a dict at the
     boundary; do not try to type the nested payload

---

## Phase 9: Make Operational Constants Configurable

**Hardcoded values confirmed in code:**

| Constant | File | Current Value |
|----------|------|---------------|
| `num_ctx` | `api/ollama_client.py` `_generate_payload()` | 16384 |
| ThreadPoolExecutor `max_workers` | `web/api_server.py:63` | 4 |
| `asyncio.Semaphore` size | `web/api_server.py:64` | 4 |
| Rate limit window | `web/api_server.py` | 60s |
| Rate limit (general) | `web/api_server.py` | 30 req |
| Rate limit (login) | `web/api_server.py` | 10 req |
| Streaming timeout | `web/api_server.py` | 120s per chunk |
| `recall_k` | `api/retrieval.py` `retrieve_best()` | 15 |
| `mmr_k` | `api/retrieval.py` `retrieve_best()` | 12 |
| `final_k` | `api/retrieval.py` `retrieve_best()` | 4 |
| `lambda_mult` | `api/retrieval.py` `mmr_select()` | 0.7 |
| KeywordIndex refresh interval | `api/keyword_index.py` | 300s |

**Required changes:**

1. `settings.py`: add env vars for all constants above; keep current values as defaults
2. `api/ollama_client.py`: read `num_ctx` from settings
3. `web/api_server.py`: read executor size, semaphore size, rate limit params, stream timeout
   from settings
4. `api/retrieval.py`: read retrieval defaults (`recall_k`, `mmr_k`, `final_k`, `lambda_mult`)
   from settings if not already covered by the response-time plan
5. `api/keyword_index.py`: read refresh interval from settings

---

## Phase 10: Improve Error Handling and Observability

**Problems confirmed in code:**

`api/ollama_client.py`:
- `json.loads()` in `stream_generate()` has no try/except — malformed lines crash the iterator
- `.raise_for_status()` surfaces raw HTTP errors with no context about which service failed
- No retry logic for transient 5xx failures

`api/embed.py`:
- Silent text truncation at `MAX_EMBED_CHARS` with no log entry

`api/retrieval.py`:
- No exception handling around Qdrant calls; service failures propagate unhandled
- `zip(..., strict=False)` in `rerank()` silently ignores length mismatches

`ingest/index_documents.py`:
- `path.read_text(errors="ignore")` silently drops undecodable bytes with no warning

`web/api_server.py`:
- No request ID generated or threaded through logs and SSE error chunks

**Required changes:**

1. `api/ollama_client.py`:
   - Wrap `json.loads()` in `stream_generate()` with try/except; log and skip malformed lines
   - Wrap `.raise_for_status()` — raise a `RuntimeError` that includes URL and HTTP status
   - Add 1-2 retries with short delay for 5xx responses

2. `api/embed.py`:
   - Log a warning when text is truncated, including original length and the limit applied

3. `api/retrieval.py`:
   - Wrap `qdrant_recall()` and `keyword_index.search()` in try/except; log service name and
     error type; return empty list as fallback so the pipeline degrades gracefully
   - Replace `zip(..., strict=False)` in `rerank()` with an explicit length check and log

4. `web/api_server.py`:
   - Generate a request ID (`uuid4()` short hex) in middleware
   - Thread request ID through RAG execution logs and into SSE error chunk payloads

5. `ingest/index_documents.py`:
   - Log a warning when `errors="ignore"` is used, noting the file path and encoding assumption
