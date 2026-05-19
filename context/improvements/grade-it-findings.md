# Code Review Findings (grade-it)

Generated: 2026-05-18

---

## Priority 1 — Must Fix

### Semaphore leak in streaming path
**File:** `web/api_server.py:216-218`

`_rag_stream_response` acquires `_RAG_CONCURRENCY` but has no guard if `loop.run_in_executor` raises. The non-streaming path (`_run_rag_with_timeout`) correctly handles this with a `try/except BaseException: if future is None: release()` guard. The streaming path has no equivalent — a shutdown-race or executor error permanently decrements the semaphore and starves the server of capacity.

**Fix:** Add the same `future = None` / `except BaseException` guard used in `_run_rag_with_timeout`.

---

### Fragile "Answer:" stripping corrupts valid responses
**File:** `api/query_rag.py:120-121`

```python
if "Answer:" in answer:
    answer = answer.split("Answer:", 1)[1].strip()
```

Any response legitimately containing the substring `"Answer:"` is silently truncated. Models that don't echo prompts produce clean output and this branch never fires. Models that do echo the full prompt template would echo the entire preamble, not just `"Answer:"`. The heuristic is both lossy for non-echoing models and insufficient for echoing ones.

**Fix:** Remove this stripping entirely, or detect a full prompt echo (check if the response starts with the instruction block).

---

### `_extract_filename` false-matches abbreviations and URLs
**File:** `api/retrieval.py:86-96`

`_FILENAME_RE = re.compile(r"\b([\w.-]+\.[a-zA-Z]{2,5})\b")` matches `e.g.`, `i.e.`, `Fig.3`, domain names, and other non-filename patterns. A false match causes the Qdrant query filter to narrow to a non-existent filename, returning zero results where many should exist.

**Fix:** Validate the matched candidate's extension against `ALLOWED_EXTENSIONS` before accepting:
```python
if Path(candidate).suffix.lower() not in settings.ALLOWED_EXTENSIONS:
    return None
```

---

## Priority 2 — Should Fix

### RAG timeout doesn't cover semaphore wait
**File:** `web/api_server.py:169`

`await _RAG_CONCURRENCY.acquire()` has no timeout. If all concurrency slots are busy, requests queue here indefinitely — the `asyncio.wait_for(timeout=RAG_REQUEST_TIMEOUT_SECONDS)` clock doesn't start until after the acquire. A request can breach its stated timeout before the timer begins.

**Fix:** Use `asyncio.wait_for(_RAG_CONCURRENCY.acquire(), timeout=RAG_REQUEST_TIMEOUT_SECONDS)` or set a separate queue timeout.

---

### `_RAG_EXECUTOR` and `_RAG_CONCURRENCY` uninitialized at module scope
**File:** `web/api_server.py:70-71`

```python
_RAG_EXECUTOR: ThreadPoolExecutor
_RAG_CONCURRENCY: asyncio.Semaphore
```

These are bare type annotations with no default. Any code path that accesses them before `lifespan` runs (test imports, startup ordering issues) gets an unguarded `NameError`.

**Fix:** Initialize to `None` with `Optional` types; add a getter that raises a descriptive `RuntimeError` if called before startup.

---

### `LOGIN_RATE_MAX` is a pointless alias
**File:** `web/rate_limit.py:9,34`

```python
LOGIN_RATE_MAX = RATE_MAX_LOGIN_REQUESTS
```

This creates two names for the same value. The test (`test_rate_limit.py:83`) patches `LOGIN_RATE_MAX` while the settings name is `RATE_MAX_LOGIN_REQUESTS`. A future caller patching the settings name would bypass the test intercept.

**Fix:** Remove the alias; use `RATE_MAX_LOGIN_REQUESTS` directly in `check_login_rate_limit`.

---

### `MAX_CHUNK_CHARS` and `MAX_MD_CHUNK` are identical and unexposed to env
**File:** `settings.py:71-72`

Both are hardcoded to `2000` with no env-var override, unlike every other tuning constant. They express the same policy (max chars per chunk) for different chunkers and should either be consolidated into one constant or at minimum both be env-overridable.

**Fix:** Merge into `MAX_CHUNK_CHARS` (or keep both but expose via `os.environ.get`).

---

### `timed` is leaked as public API from `api.retrieval`
**File:** `api/retrieval.py:51-58`, `api/query_rag.py:24`

`timed` is a 7-line internal timing helper that `query_rag` imports from a sibling module, creating cross-module coupling on an implementation detail.

**Fix:** Move `timed` to `common/` (if reuse is intended) or copy the four relevant lines into `query_rag.py` and remove the export.

---

### `cleanup_stale.py` calls `logging.basicConfig` at import time and uses root logger
**File:** `ingest/cleanup_stale.py:17,37,41`

`logging.basicConfig` is called at module level. Because `cleanup_stale` is imported by `indexer/watcher.py` (line 28), this reconfigures the root logger before the watcher's own `basicConfig` runs. Inside `cleanup_stale()`, messages go to the root logger (`logging.info(...)`) rather than a named logger, bypassing any per-module filtering.

**Fix:** Remove `basicConfig` from module level; replace `logging.info/error` calls with `logger = logging.getLogger(__name__)`.

---

### `load_config()` and `validate_required_mounts()` call `sys.exit` directly
**File:** `indexer/watcher.py:43,46,170,175`

Functions that exit are untestable as library code. `validate_required_mounts` is already tested in unit tests but the exit makes test isolation fragile.

**Fix:** Raise an exception (e.g. `RuntimeError` or `SystemExit`) in the functions; catch and `sys.exit` only in `main()`.

---

### Ollama generation timeout is a magic value not in settings
**File:** `api/ollama_client.py:64,76`

`generate` and `stream_generate` default to `timeout=120.0`, which doesn't appear in `settings.py` alongside `RAG_REQUEST_TIMEOUT_SECONDS` and `STREAM_TIMEOUT_SECONDS`.

**Fix:** Add `OLLAMA_GENERATE_TIMEOUT_SECONDS` to `settings.py` with an env-var override.

---

## Priority 3 — Nice to Fix

| Issue | Location |
|---|---|
| `seen: set = set()` missing type parameter; should be `set[str \| int]` | `api/retrieval.py:219` |
| Route aliases (`models_alias`, `chat_alias`) are wrapper functions; use multi-path registration | `web/api_server.py:271-273, 292-294` |
| Mixed middleware styles: `@app.middleware("http")` for auth, `BaseHTTPMiddleware` for security headers | `web/api_server.py:108, 145-158` |
| `sha256_file` uses `open(path, "rb")` instead of `path.open("rb")` | `indexer/watcher.py:51` |
| `PollingObserver(timeout=30)` poll interval is hardcoded with no env override | `indexer/watcher.py:204` |
| Timing variables (`t_read`, `t_chunk`, etc.) reused for both start time and elapsed | `ingest/index_documents.py:127-154` |
