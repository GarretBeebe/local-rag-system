# Code Review Findings (grade-it)

Last updated: 2026-05-23

**All items resolved.** Backlog is empty.

---

## Recently Resolved (2026-05-23)

| # | Finding | Fix |
|---|---|---|
| P1 | Non-streaming semaphore released before timed-out worker exits (`web/api_server.py`) | Added `asyncio.shield()` so the done callback (semaphore release) fires only when `ask()` truly exits in the thread |
| P1 | Stale vectors left when previously-indexed file becomes unindexable (`indexer/watcher.py`) | `_index_if_changed` now calls `remove_indexed_document` when `index_file` returns SKIPPED and `prev_hash is not None` |
| P2 | `exclude_dirs` not applied to initial scan (`indexer/watcher.py`) | `initial_scan` now uses `_iter_schedulable_dirs` to prune excluded dirs, matching the observer scheduling path |
| P2 | Shared global `requests.Session` across worker threads (`api/ollama_client.py`) | Replaced module-global session with `threading.local()` — one session per worker thread |
| P2 | `KeywordIndex` refresh threads leak across lifespan restarts (`api/keyword_index.py`, `api/retrieval.py`) | Added `threading.Event` stop flag and `stop()` method; `retrieval.shutdown()` called from FastAPI lifespan cleanup |
| P2 | `_extract_filename` false-matches URLs (`api/retrieval.py`) | Extension validated against `ALLOWED_EXTENSIONS`; fuzzy-match `n` and `cutoff` extracted to named constants |
| P2 | `MAX_CHUNK_CHARS` / `MAX_MD_CHUNK` not env-exposed (`settings.py`) | `MAX_CHUNK_CHARS` now backed by `os.environ.get`; `MAX_MD_CHUNK` aliased to it |
| P2 | `load_config()` and `validate_required_mounts()` call `sys.exit` directly (`indexer/watcher.py`) | Both raise `RuntimeError`; `main()` catches and exits with code 1 |
| P3 | `sendMessage()` mixes transport, SSE parsing, rendering, and UI state (`web/static/app.js`) | Extracted `_lockUi()`, `_unlockUi()`, `_iterSSEDeltas()`; `thinking.remove()` consolidated to `finally` via `isConnected` guard |

---

## Previously Resolved (2026-05-22, pruned same day)

Items fixed before this batch:
- Streaming semaphore leak on executor scheduling failure
- Delete-before-upsert data loss (now uses `index_version` staging)
- Reset collection leaves fingerprints stale
- `purge_ignored --apply` fails open when no watch roots accessible
- Retrieval outage silently degrades into uncited answer (`RetrievalError` + mode-aware handling)
- Python chunker drops module-level code (gap spans now emitted)
- Partial embedding failures corrupt chunk metadata (whole-doc fail-fast)
- `_rag_stream_response()` overloaded (split into `_start_stream_worker`, `_watch_disconnect`, `_stream_queue_events`)
- `IndexWorker._run()` mixed lifecycle with policy (`_index_if_changed` extracted)
- `seen: set` missing type parameter
- Timing variables reused for start time and elapsed

## Previously Resolved (before 2026-05-22)

11 additional items fixed across earlier sessions — see git log for details.
