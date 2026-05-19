# Code Review Findings (grade-it)

Generated: 2026-05-18

---

## Priority 1 — Must Fix

### Semaphore leak in streaming path
**File:** `web/api_server.py:216-218`

`_rag_stream_response` acquires `_RAG_CONCURRENCY` but has no guard if `loop.run_in_executor` raises. The non-streaming path (`_run_rag_with_timeout`) correctly handles this with a `try/except BaseException: if future is None: release()` guard. The streaming path has no equivalent — a shutdown-race or executor error permanently decrements the semaphore and starves the server of capacity.

**Fix:** Add the same `future = None` / `except BaseException` guard used in `_run_rag_with_timeout`.

---

### Delete-before-upsert can remove indexed documents on transient failure
**File:** `ingest/index_documents.py:104-119`

`_upsert_chunks()` deletes existing vectors for a file before the replacement upsert succeeds. If Qdrant accepts the delete and the later upsert fails, the document is absent from the index until a future successful re-index. The comment at `ingest/index_documents.py:114` confirms this data-loss window is known behavior.

**Fix:** Upsert the new document version first, then delete the previous version only after the replacement succeeds. Use a versioned `document_id`, an `active` payload flag, or another staging/swap mechanism so transient upsert failures leave the old index entries serving.

---

### Retrieval outage silently degrades into uncited augmented answer
**File:** `api/retrieval.py:130-132`, `api/query_rag.py:100-104`

`qdrant_recall()` catches every Qdrant exception, logs it, and returns `[]`. In augmented mode, `_prepare_query()` treats empty retrieval as no context and sends the raw user question to the model. That turns an infrastructure outage into a normal-looking non-RAG answer with no explicit degraded-service signal.

**Fix:** Propagate retrieval infrastructure failures as a typed error or return an explicit failure state. API callers should report retrieval failure/degraded service instead of falling back to uncited model-only generation.

---

### Rate limits trust spoofable `X-Forwarded-For`
**File:** `web/api_server.py:117-120`

`security_middleware()` uses the first `X-Forwarded-For` value as the rate-limit identity for every request. A direct client can rotate this header and bypass both general API and login rate limits.

**Fix:** Only honor forwarded headers when the request comes from a trusted proxy that overwrites them. Otherwise use `request.client.host`. Add a helper like `resolve_client_ip(request)` with explicit trusted-proxy configuration.

---

### Reset collection leaves fingerprints stale
**File:** `ingest/reset_collection.py:11-15`, `indexer/watcher.py:86-88`

`reset_collection.py` deletes the Qdrant collection but leaves `data/fingerprints.sqlite3` intact. After reset, unchanged files still have matching fingerprints, so the watcher skips them as already indexed even though their vectors no longer exist. The collection can stay empty until files change or fingerprints are manually cleared.

**Fix:** Clear or invalidate the fingerprint store when the collection is reset, or add an explicit reset mode that drops both Qdrant vectors and fingerprint state together.

---

### `purge_ignored --apply` fails open when no watch roots are accessible
**File:** `ingest/purge_ignored.py:49-62`

`find_ignored_paths()` only applies the root containment guard when `accessible_roots` is non-empty. If the config is wrong or all watch roots are unavailable, `accessible_roots` becomes `[]`, and `--apply` evaluates every tracked fingerprint globally. That can delete indexed entries outside the intended mounted roots.

**Fix:** Fail closed when no configured watch roots are accessible. Require at least one accessible root before applying deletes, or require an explicit override for global purges.

---

## Priority 2 — Should Fix

### Fragile "Answer:" stripping corrupts valid responses
**File:** `api/query_rag.py:120-121`

```python
if "Answer:" in answer:
    answer = answer.split("Answer:", 1)[1].strip()
```

Any response legitimately containing the substring `"Answer:"` is silently truncated. Models that don't echo prompts produce clean output and this branch never fires. Models that do echo the full prompt template would echo the entire preamble, not just `"Answer:"`. The heuristic is both lossy for non-echoing models and insufficient for echoing ones.

**Fix:** Remove this stripping entirely, or detect a full prompt echo by checking for the full prompt prefix instead of splitting on a common substring.

---

### `_extract_filename` false-matches URLs and hides fuzzy-match policy
**File:** `api/retrieval.py:86-96`

`_FILENAME_RE = re.compile(r"\b([\w.-]+\.[a-zA-Z]{2,5})\b")` matches domain-like text such as `example.com`, package-like tokens, and other dotted strings that are not necessarily indexed filenames. A false match can cause the Qdrant query filter to narrow to an unintended filename. The fuzzy-match behavior also hardcodes `n=1` and `cutoff=0.75`, which materially affects retrieval false positives and false negatives but is hidden inside the helper.

**Fix:** Validate the matched candidate's extension against `ALLOWED_EXTENSIONS` before accepting, and move the fuzzy-match limit/cutoff into named constants or settings-backed tuning:
```python
if Path(candidate).suffix.lower() not in settings.ALLOWED_EXTENSIONS:
    return None
```

---

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

### Cleanup/purge scripts call `logging.basicConfig` at import time
**File:** `ingest/cleanup_stale.py:17,37,41`, `ingest/purge_ignored.py:25`

`logging.basicConfig` is called at module level in both cleanup scripts. Because `cleanup_stale` is imported by `indexer/watcher.py` (line 28), this can reconfigure the root logger before the watcher's own `basicConfig` runs. Inside `cleanup_stale()`, messages go to the root logger (`logging.info(...)`) rather than a named logger, bypassing any per-module filtering.

`purge_ignored.py` is currently a CLI script rather than a watcher import, so its module-level `basicConfig` has less blast radius, but it repeats the same import-time logging pattern.

**Fix:** Remove `basicConfig` from module level in both scripts. Configure logging only in CLI entrypoints, and replace `logging.info/error` calls with `logger = logging.getLogger(__name__)`.

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

### Python chunker drops module-level code when definitions exist
**File:** `ingest/chunkers.py:95-109`

`chunk_python()` only indexes top-level `FunctionDef`, `AsyncFunctionDef`, and `ClassDef` nodes. If a Python file contains any top-level definitions, imports, constants, module setup, script-level side effects, and other non-definition statements are omitted from the vector store entirely. The fallback to whole-text chunking only runs for files with no definitions.

**Fix:** Preserve non-definition top-level spans. Add a preamble chunk for imports/constants and chunk any statement ranges between definitions, or replace this with a structure-aware splitter that keeps the full source text represented.

---

### Partial embedding failures corrupt chunk metadata
**File:** `ingest/index_documents.py:75-101`

`_embed_chunks()` skips individual chunks when embedding fails, but keeps each surviving point's original `chunk_index` from the pre-filtered list and sets `chunk_total` to `len(points)` after filtering. If chunk 1 of 3 fails, citations can report impossible sequences such as `0/2` and `2/2`, making source references misleading.

**Fix:** Either fail the whole document on any embedding failure or renumber successfully embedded chunks after filtering so `chunk_index` and `chunk_total` describe the stored chunk set consistently.

---

### Embedding request timeout is a magic value not in settings
**File:** `api/embed.py:40`

`embed()` hardcodes `timeout=60` for the Ollama embedding request. This is operational policy and belongs with the other timeout constants in `settings.py`.

**Fix:** Add `EMBED_REQUEST_TIMEOUT_SECONDS` to `settings.py` with an env-var override and use it in `api/embed.py`.

---

### Chat question extraction ignores message role
**File:** `web/schemas.py:59-77`

`extract_question_from_messages()` always uses the last message, even if it is an `assistant`, `system`, or `tool` message. OpenAI-compatible chat requests can include trailing non-user messages, which would make the server retrieve and answer against the wrong text.

**Fix:** Extract from the latest `role == "user"` message, or reject the request if no user message is present.

---

### Streaming response function mixes unrelated responsibilities
**File:** `web/api_server.py:194-257`

`_rag_stream_response()` handles executor scheduling, semaphore ownership, thread-to-async queue bridging, disconnect polling, cancellation, timeout behavior, error rendering, SSE formatting, and stream termination in one 64-line function. The existing semaphore leak came from this concentration of responsibilities.

**Fix:** Split scheduling/release, disconnect cancellation, queue draining, and SSE event emission into focused helpers.

---

### Web API request timeouts are hardcoded
**File:** `web/api_server.py:263,330`

The model-list endpoint hardcodes `timeout=5.0`, and warmup hardcodes `timeout=60`. These are operational policy values and should be settings-backed like the other API/RAG timeouts.

**Fix:** Add settings-backed constants for model-list and warmup request timeouts.

---

### `IndexWorker._run()` mixes worker lifecycle with indexing policy
**File:** `indexer/watcher.py:74-101`

`IndexWorker._run()` owns queue consumption, sentinel handling, filesystem existence checks, hashing, fingerprint comparison, indexing, fingerprint updates, and error logging. That makes the indexing state transition hard to test independently from thread/queue behavior.

**Fix:** Extract a focused helper such as `_index_if_changed(path: str) -> IndexOutcome` and keep `_run()` responsible only for queue lifecycle.

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

---

## Compact Prioritized Punch List

### Priority 1 — Must Fix First

1. Fix streaming semaphore release on executor scheduling failure — `web/api_server.py:216-218`
2. Stop deleting old vectors before replacement upsert succeeds — `ingest/index_documents.py:104-119`
3. Propagate retrieval infrastructure failures instead of silently falling back to model-only answers — `api/retrieval.py:130-132`, `api/query_rag.py:100-104`
4. Stop trusting arbitrary `X-Forwarded-For` for rate-limit identity — `web/api_server.py:117-120`
5. Make collection reset clear or invalidate fingerprint state — `ingest/reset_collection.py:11-15`
6. Make `purge_ignored --apply` fail closed when no watch roots are accessible — `ingest/purge_ignored.py:49-62`

### Priority 2 — Should Fix Next

1. Put semaphore acquisition under timeout and initialize RAG executor/concurrency defensively — `web/api_server.py:70-71`, `web/api_server.py:169`
2. Fix chat request semantics by extracting from the latest user message — `web/schemas.py:59-77`
3. Preserve module-level Python code during chunking — `ingest/chunkers.py:95-109`
4. Make partial embedding failures either fail the document or renumber stored chunks — `ingest/index_documents.py:75-101`
5. Move hardcoded operational timeouts into settings — `api/ollama_client.py:64,76`, `api/embed.py:40`, `web/api_server.py:263,330`
6. Remove import-time logging configuration from cleanup/purge scripts — `ingest/cleanup_stale.py:17`, `ingest/purge_ignored.py:25`

### Priority 3 — Cleanup

1. Move or localize the shared `timed` helper — `api/retrieval.py:51-58`, `api/query_rag.py:24`
2. Split overloaded streaming and indexing worker functions into focused helpers — `web/api_server.py:194-257`, `indexer/watcher.py:74-101`
3. Clean up small style and typing items from the Priority 3 table above.
