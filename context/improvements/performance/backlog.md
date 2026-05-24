# Performance Improvement Backlog

Last updated: 2026-05-24

All items from the initial audit are resolved. See summaries below.

---

## Resolved

### 1. Ollama thread-local sessions never closed ✓
**Fixed 2026-05-24** — Added `close_session()` to `api/ollama_client.py`. Lifespan cleanup
now submits `close_session` to each executor worker before shutdown and waits for completion
(`shutdown(wait=True)`). Sessions are explicitly closed on server shutdown rather than relying on GC.

### 2. `ensure_collection()` called per file during indexing ✓
**Fixed 2026-05-24** — Added `_collection_ensured: bool` flag in `ingest/index_documents.py`.
`ensure_collection()` short-circuits after the first successful call. `reset_collection.py`
resets the flag after deleting the collection so re-creation works correctly after a reset.

### 3. Semaphore potential double-release — false positive ✓
**Verified 2026-05-24** — The two release paths in `_submit_rag_job()` are mutually exclusive:
the `BaseException` handler at line 273 fires only if `run_in_executor` raises (no future
returned, callback never attached); the done callback at line 275 fires only when the future
completes. No code change needed.

### 4. Synchronous file hashing blocks watcher — false positive ✓
**Verified 2026-05-24** — `sha256_file()` runs inside `IndexWorker._run()` (the background
worker thread), not the filesystem event handler. The event handler only enqueues events.
sha256 on ≤1MB also takes ~1ms — negligible. Backlog description was inaccurate.

### 5. MMR `list.remove()` is O(n) per iteration ✓
**Fixed 2026-05-24** — Replaced `remaining.remove(best)` with index-based swap-remove in
`api/retrieval.py:mmr_select()`. The max search is still O(n) (unavoidable), but removal
is now O(1). Practically unmeasurable at RECALL_K=15, but correct as a pattern.

### 6. Disconnect polling at 0.5s per active stream ✓
**Fixed 2026-05-24** — `_DISCONNECT_POLL_SECONDS` raised from `0.5` to `2.0` in
`web/api_server.py`. Cuts async wake-ups by 4× per stream; 2-second disconnect detection
latency is imperceptible to users.

### 7. Rate limiter buckets are unbounded dicts — false positive ✓
**Verified 2026-05-24** — `check_rate_limit()` already prunes expired timestamps for each IP
inline on every request (`active = [t for t in buckets.get(ip, []) if now - t < window]`).
The per-IP list is always kept to only active timestamps. The sweep task evicts IPs with empty
buckets once per window. No unbounded growth in practice.
