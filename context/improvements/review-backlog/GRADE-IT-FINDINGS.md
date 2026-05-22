# Code Review Findings (grade-it)

Last updated: 2026-05-22

---

## Priority 1 — Must Fix

### Semaphore leak in streaming path
**File:** `web/api_server.py:216-218`

`_rag_stream_response` acquires `_RAG_CONCURRENCY` but has no guard if `loop.run_in_executor` raises. The non-streaming path (`_run_rag_with_timeout`) correctly handles this with a `try/except BaseException: if future is None: release()` guard. The streaming path has no equivalent — a shutdown-race or executor error permanently decrements the semaphore and starves the server of capacity.

**Fix:** Add the same `future = None` / `except BaseException` guard used in `_run_rag_with_timeout`.

---

### Semaphore released before timed-out non-streaming worker exits
**File:** `web/api_server.py:227-232`

`_run_rag_with_timeout` schedules `ask()` in the executor and attaches
`future.add_done_callback(lambda _f: semaphore.release())`. When
`asyncio.wait_for` times out, it cancels the asyncio future wrapper and marks
it done — triggering the callback and releasing the semaphore — even though the
threadpool task cannot be cancelled and keeps running. Timed-out requests pile
up beyond `RAG_CONCURRENCY_LIMIT` and continue consuming Ollama/reranker
resources.

**Fix:** Shield the executor future from `wait_for` cancellation, or release
the semaphore from a wrapper that runs after `ask()` truly exits in the worker
thread.

---

### Delete-before-upsert can remove indexed documents on transient failure
**File:** `ingest/index_documents.py:104-119`

`_upsert_chunks()` deletes existing vectors for a file before the replacement upsert succeeds. If Qdrant accepts the delete and the later upsert fails, the document is absent from the index until a future successful re-index. The comment at `ingest/index_documents.py:114` confirms this data-loss window is known behavior.

**Fix:** Upsert the new document version first, then delete the previous version only after the replacement succeeds. Use a versioned `document_id`, an `active` payload flag, or another staging/swap mechanism so transient upsert failures leave the old index entries serving.

---

### Previously indexed files leave stale vectors when they become unindexable
**File:** `ingest/index_documents.py:141-151`, `indexer/watcher.py:88-90`

`index_documents.py` returns `SKIPPED` for oversized, unreadable, or empty
files without deleting existing Qdrant points for that path. The watcher then
leaves the fingerprint unchanged and does not remove the old document. If a
previously indexed file is replaced by an empty or unreadable version, the old
content remains queryable indefinitely.

**Fix:** Distinguish "never indexable" from "previously indexed, now
unindexable". Delete existing vectors for the normalized path when current
content should no longer be served.

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

### `exclude_dirs` applied to observer scheduling but not initial scan
**File:** `indexer/watcher.py:221-227`, `indexer/watcher.py:251-255`

The initial scan uses `root.glob("**/*")` and enqueues every file.
`exclude_dirs` is only applied when scheduling `PollingObserver` watches.
A directory excluded for polling can still be fully indexed on startup unless
the same patterns are duplicated in `ignore_patterns`. The duplication is
visible in `config/watcher_config.container.yaml` lines 33-58 and 77-106.

**Fix:** Apply the same `exclude_dirs` pruning to the initial scan traversal, or
pass per-watch-path `exclude_dirs` into the indexing filter so the two paths
share one source of truth.

---

### Shared global `requests.Session` used across worker threads
**File:** `api/ollama_client.py:17`

One module-global `requests.Session` is shared by concurrent RAG threadpool
workers (`web/api_server.py:111`, `227`, `275`). Concurrent `generate`,
`stream_generate`, and `embed` calls share the same session. While
`requests.Session` serializes on an internal lock and won't corrupt data, it
creates a contention point and is not designed for high-concurrency sharing.

**Fix:** Use `threading.local()` for one session per thread, create short-lived
sessions per call, or switch to `httpx` with an `AsyncClient` / explicit
connection pool.

---

### `KeywordIndex` refresh threads leak across lifespan restarts
**File:** `api/keyword_index.py:47-48`, `api/retrieval.py:62-69`

`retrieval.startup()` creates a new `KeywordIndex` and calls `start()` every
time the FastAPI lifespan starts. `KeywordIndex.start()` spawns a daemon
refresh thread with no stop mechanism and no guard against repeated calls. Test
clients, hot reloads, or lifecycle restarts can accumulate orphaned refresh
loops that continue scrolling Qdrant.

**Fix:** Give `KeywordIndex` a stop event and joinable thread. Expose a
`retrieval.shutdown()` function and call it from the FastAPI lifespan cleanup.

---

### Retrieval outage silently degrades into uncited augmented answer
**File:** `api/retrieval.py:130-132`, `api/query_rag.py:100-104`

`qdrant_recall()` catches every Qdrant exception, logs it, and returns `[]`. Downstream, `_prepare_query()` cannot distinguish between two fundamentally different situations that both produce an empty candidate list:

1. **Qdrant searched successfully and found nothing** — legitimate empty result; augmented fallback is correct behavior
2. **Qdrant threw an exception** — infrastructure failure; falling back silently masks the outage

In augmented mode, case 2 produces a normal-looking model-only answer with no signal to the user that RAG was unavailable. In strict mode, the behavior is the same as case 1 (returns `_NO_CONTEXT_REPLY`) even though the cause was a backend failure, not absent documents.

**Fix:** Distinguish retrieval failure from empty results with a typed failure signal. Handle strict mode as a service failure, and make augmented mode surface a degraded-answer notice if it falls back to model-only generation. See `context/improvements/retrieval-performance/RETRIEVAL-FAILURE-HANDLING.md` for the detailed design notes.

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

### `MAX_CHUNK_CHARS` and `MAX_MD_CHUNK` are identical and unexposed to env
**File:** `settings.py:80-81`

Both are hardcoded to `2000` with no env-var override, unlike every other tuning constant. They express the same policy (max chars per chunk) for different chunkers and should either be consolidated into one constant or at minimum both be env-overridable.

**Fix:** Merge into `MAX_CHUNK_CHARS` (or keep both but expose via `os.environ.get`).

---

### `load_config()` and `validate_required_mounts()` call `sys.exit` directly
**File:** `indexer/watcher.py:55,58,61,210,215`

Functions that exit are untestable as library code. `validate_required_mounts` is already tested in unit tests but the exit makes test isolation fragile.

**Fix:** Raise an exception (e.g. `RuntimeError`) in the functions; catch and `sys.exit` only in `main()`.

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

### Streaming response function mixes unrelated responsibilities
**File:** `web/api_server.py:323-368`

`_rag_stream_response()` handles executor scheduling, semaphore ownership, thread-to-async queue bridging, disconnect polling, cancellation, timeout behavior, error rendering, SSE formatting, and stream termination in one function. The existing semaphore leak came from this concentration of responsibilities.

**Fix:** Split scheduling/release, disconnect cancellation, queue draining, and SSE event emission into focused helpers.

---

### `IndexWorker._run()` mixes worker lifecycle with indexing policy
**File:** `indexer/watcher.py:74-101`

`IndexWorker._run()` owns queue consumption, sentinel handling, filesystem existence checks, hashing, fingerprint comparison, indexing, fingerprint updates, and error logging. That makes the indexing state transition hard to test independently from thread/queue behavior.

**Fix:** Extract a focused helper such as `_index_if_changed(path: str) -> IndexOutcome` and keep `_run()` responsible only for queue lifecycle.

---

## Priority 3 — Nice to Fix

| Issue | Location |
|---|---|
| `sendMessage()` mixes transport, SSE parsing, rendering, and UI state (111 lines) | `web/static/app.js:108` |
| `seen: set = set()` missing type parameter; should be `set[str \| int]` | `api/retrieval.py:219` |
| Timing variables (`t_read`, `t_chunk`, etc.) reused for both start time and elapsed | `ingest/index_documents.py:127-154` |

---

## Compact Prioritized Punch List

### Priority 1 — Must Fix First

1. Fix streaming semaphore release on executor scheduling failure — `web/api_server.py:216-218`
2. Fix non-streaming semaphore released before timed-out worker exits — `web/api_server.py:227-232`
3. Stop deleting old vectors before replacement upsert succeeds — `ingest/index_documents.py:104-119`
4. Delete stale vectors when a previously indexed file becomes unindexable — `ingest/index_documents.py:141`, `indexer/watcher.py:88`
5. Make collection reset clear or invalidate fingerprint state — `ingest/reset_collection.py:11-15`
6. Make `purge_ignored --apply` fail closed when no watch roots are accessible — `ingest/purge_ignored.py:49-62`

### Priority 2 — Should Fix Next

1. Apply `exclude_dirs` during initial scan, not only observer scheduling — `indexer/watcher.py:221`
2. Replace shared global `requests.Session` with thread-local or per-call sessions — `api/ollama_client.py:17`
3. Add lifecycle shutdown for `KeywordIndex` refresh threads — `api/keyword_index.py:47`
4. Introduce `RetrievalUnavailable`; distinguish retrieval failure from empty results — `api/retrieval.py:130-132`, `api/query_rag.py:100-104`
5. Validate `_extract_filename` result against `ALLOWED_EXTENSIONS`; move fuzzy-match policy to constants — `api/retrieval.py:86-96`
6. Merge `MAX_CHUNK_CHARS` / `MAX_MD_CHUNK` or expose both via env-var — `settings.py:80-81`
7. Raise instead of `sys.exit` in `load_config()` and `validate_required_mounts()` — `indexer/watcher.py`
8. Preserve module-level Python code during chunking — `ingest/chunkers.py:95-109`
9. Make partial embedding failures either fail the document or renumber stored chunks — `ingest/index_documents.py:75-101`
10. Split overloaded streaming function into focused helpers — `web/api_server.py:323-368`
11. Split `IndexWorker._run()` into queue lifecycle and indexing helper — `indexer/watcher.py:74-101`

### Priority 3 — Cleanup

1. Break `sendMessage()` into transport, SSE parsing, rendering, and UI-state helpers — `web/static/app.js:108`
2. Add type parameter to `seen: set` — `api/retrieval.py:219`
3. Rename timing variables to distinguish start-time captures from elapsed durations — `ingest/index_documents.py:127`
