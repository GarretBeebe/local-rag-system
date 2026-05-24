# Performance Improvement Backlog

Last updated: 2026-05-24

Findings from a runtime performance/memory audit. Items are ordered by practical impact.
The two high-priority items (keyword index payload memory, unbounded streaming queue) have been resolved.

---

## Medium Priority

### 1. Ollama thread-local sessions never closed

**File:** `api/ollama_client.py:30-33`

`_get_session()` creates a `requests.Session` per executor thread, stored in `threading.local()`. Sessions are never explicitly closed, so the TCP connections to Ollama live for the lifetime of each worker thread. With a fixed pool of 4 workers this is 4 unclosed sessions — minor in practice, but if Ollama restarts the stale connections will produce errors until the worker thread itself is replaced.

**Fix direction:** Register an `atexit` handler or add a `close_sessions()` function callable from the API lifespan cleanup that iterates `_get_rag_executor()._threads` and closes their sessions. Alternatively, wrap session use in a try/finally that closes on exception.

---

### 2. `ensure_collection()` called per file during indexing

**File:** `ingest/index_documents.py:47-54`, called at `:129`

Every file indexed by the watcher triggers a `client.collection_exists()` round-trip to Qdrant. The collection is almost never absent after the first run. This is a redundant network call on every indexing event.

**Fix direction:** Cache the result with a module-level flag (`_collection_ensured = False`) that is set on first successful call and reset only on explicit teardown or collection deletion.

---

### 3. Semaphore potential double-release

**File:** `web/api_server.py:273, 275`

`_submit_rag_job()` has two release paths for the same semaphore:
- Line 273: inside a `BaseException` handler (fires if `run_in_executor` raises)
- Line 275: the done callback on the returned future (fires when the future completes)

If `run_in_executor` raises (line 270) and the future is still marked done by the executor, both paths fire. The semaphore counter drifts up permanently, eventually disabling the concurrency limit. The `BaseException` catch here is intentional (to catch `asyncio.CancelledError` during shutdown), so the fix must be careful.

**Fix direction:** In the exception handler, cancel the future before releasing (preventing the done callback from firing), or use a flag to ensure only one path releases.

---

## Low Priority

### 4. Synchronous file hashing blocks the watcher event thread

**File:** `indexer/watcher.py:63-68`

`sha256_file()` reads the file synchronously inside the watcher's event handler thread. For files near the 1MB limit, this blocks other filesystem events from being processed until hashing completes.

**Fix direction:** Offload hashing to the `IndexWorker` thread (it already runs file processing) rather than the event handler thread, so the observer remains responsive.

---

### 5. MMR `list.remove()` is O(n) per iteration

**File:** `api/retrieval.py:179`

`remaining.remove(best)` in `mmr_select()` is O(n), making the loop O(n²) overall. With `RECALL_K=15`, n is tiny (at most 15 iterations over a list of ≤15 items), so this is not measurable in practice. Worth fixing if RECALL_K ever grows significantly.

**Fix direction:** Replace `remaining` list with a set of indices, or swap-remove (`remaining[i] = remaining[-1]; remaining.pop()`).

---

### 6. Disconnect polling at 0.5s per active stream

**File:** `web/api_server.py:71, 309`

`_watch_disconnect()` wakes up every 0.5 seconds per active stream to check `request.is_disconnected()`. For 1-2 concurrent users this is negligible. At scale (10+ concurrent streams) it generates unnecessary async wake-ups and context switches.

**Fix direction:** Increase `_DISCONNECT_POLL_SECONDS` to 2.0 for lower overhead with acceptable disconnect detection latency, or explore using Starlette's `Request.is_disconnected()` in an event-driven pattern if the framework exposes one.

---

### 7. Rate limiter buckets are unbounded dicts

**File:** `web/rate_limit.py:9-10`

`_rate_buckets` and `_login_rate_buckets` grow by one entry per unique client IP that makes a request. The sweep task cleans them up periodically, but between sweeps the dicts hold one entry per recent unique IP. At personal-use traffic levels (handful of IPs) this is harmless.

**Fix direction:** Add a `maxlen` cap or use an `OrderedDict` LRU strategy to bound growth if the API is ever exposed to public traffic.
