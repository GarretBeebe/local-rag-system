# Priority 3 Implementation Spec

Source findings:
- `context/improvements/review-backlog/grade-it-findings.md`

This spec covers cleanup and refactor work that should wait until Priority 1 and Priority 2 correctness fixes are complete.

## Scope

Implement these cleanup items:

1. Move or localize the shared `timed` helper.
2. Split overloaded streaming and indexing worker functions into focused helpers.
3. Clean up small style and typing items from the Priority 3 table.

## 1. `timed` Helper Coupling

### Problem

`api.query_rag` imports `timed` from `api.retrieval`, coupling query generation to a retrieval module implementation detail.

### Files

- `api/retrieval.py`
- `api/query_rag.py`
- Optional new file: `common/timing.py`

### Implementation Options

Preferred:

- Create `common/timing.py`.
- Move `timed(label: str)` there.
- Move or pass `RAG_TIMING` dependency cleanly.

Example:

```python
from contextlib import contextmanager
import logging
import time

from settings import RAG_TIMING

logger = logging.getLogger(__name__)

@contextmanager
def timed(label: str):
    if not RAG_TIMING:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        logger.debug("%s: %.3fs", label, time.perf_counter() - start)
```

Concern:

- If `common.timing` uses its own logger, log records will come from `common.timing`, not the caller module.

Alternative:

- Keep separate local `timed()` helpers in each module. This duplicates a few lines but avoids cross-module coupling and logger ambiguity.

Recommendation:

- Use `common/timing.py` only if caller logger identity is not important.
- Otherwise duplicate the helper locally and keep it private.

### Tests

Existing tests should continue passing. No dedicated tests required unless behavior changes.

Acceptance:

- `api/query_rag.py` no longer imports `timed` from `api.retrieval`.

## 2. Split `_rag_stream_response()`

### Problem

`web/api_server.py:_rag_stream_response()` handles executor scheduling, semaphore ownership, thread-to-async queue bridging, disconnect polling, cancellation, timeout behavior, error rendering, SSE formatting, and stream termination.

### Files

- `web/api_server.py`
- Tests for streaming behavior

### Refactor Plan

Do this after the Priority 1 semaphore fix is in place.

Extract helpers:

1. `_start_stream_worker(...) -> tuple[asyncio.Queue, threading.Event, Future]`
   - Owns queue creation, cancel event creation, executor scheduling, and semaphore callback.

2. `_watch_disconnect(request, cancel_event) -> None`
   - Owns disconnect polling and cancellation.

3. `_stream_queue_events(queue, cancel_event, request_id, created, model) -> AsyncIterator[str]`
   - Owns timeout, exception-to-SSE mapping, and content chunk emission.

4. `_finish_stream(request_id, created, model) -> AsyncIterator[str]` or inline finish helper
   - Emits final stop chunk and `data: [DONE]`.

Keep public route behavior unchanged.

### Tests

Add or preserve tests for:

- Happy-path streaming yields chunks and final done marker.
- Generator exception yields generation error chunk and final done marker.
- Queue timeout yields timeout chunk.
- Disconnect sets cancel event.
- Semaphore is released on normal completion and scheduling failure.

Acceptance:

- Smaller helpers are independently testable.
- Stream output remains OpenAI-compatible.

## 3. Split `IndexWorker._run()`

### Problem

`IndexWorker._run()` owns queue lifecycle and indexing policy. That makes indexing decisions hard to test without thread/queue behavior.

### Files

- `indexer/watcher.py`
- Tests for indexing worker policy

### Refactor Plan

Extract:

```python
def _index_if_changed(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    file_hash = sha256_file(p)
    prev_hash = get_hash(path)
    if prev_hash == file_hash:
        return
    outcome = index_file(p)
    if outcome == "indexed":
        upsert_hash(path, file_hash)
    ...
```

If useful, define an enum:

```python
class IndexDecision(str, Enum):
    MISSING = "missing"
    UNCHANGED = "unchanged"
    INDEXED = "indexed"
    SKIPPED = "skipped"
    FAILED = "failed"
```

Keep `_run()` responsible for:

- `queue.get()`
- sentinel handling
- calling `_index_if_changed()`
- `task_done()`

### Tests

Add tests for `_index_if_changed()` with monkeypatched dependencies:

- Missing path does nothing.
- Same hash skips indexing.
- `"indexed"` updates fingerprint.
- `"skipped"` does not update fingerprint.
- `"failed"` does not update fingerprint.
- Exception is logged and does not kill worker.

Acceptance:

- Indexing state transitions are testable without starting a thread.

## 4. Priority 3 Table Items

### `seen` Type Parameter

File: `api/retrieval.py`

Change:

```python
seen: set[str | int] = set()
```

### Route Alias Wrappers

File: `web/api_server.py`

Current wrapper routes:

- `models_alias()`
- `chat_alias()`

Use multi-path registration if FastAPI route metadata remains acceptable:

```python
@app.get("/v1/models")
@app.get("/models")
def models():
    ...
```

For chat:

```python
@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat(...):
    ...
```

Verify generated OpenAPI schema remains acceptable.

### Mixed Middleware Styles

File: `web/api_server.py`

Current code uses both function middleware and `BaseHTTPMiddleware`.

Pick one style:

- Prefer function middleware for both auth/rate-limit and security headers, because it is already used for request state and early returns.
- Or keep as-is if changing this complicates behavior. This is cleanup, not correctness.

### `sha256_file` Uses `open()`

File: `indexer/watcher.py`

Change:

```python
with path.open("rb") as f:
```

### Polling Interval Setting

Files:

- `settings.py`
- `indexer/watcher.py`

Add:

```python
WATCHER_POLL_INTERVAL_SECONDS = float(os.environ.get("WATCHER_POLL_INTERVAL_SECONDS", "30"))
```

Use:

```python
observer = PollingObserver(timeout=WATCHER_POLL_INTERVAL_SECONDS)
```

### Timing Variable Names

File: `ingest/index_documents.py`

Replace reused variables:

```python
read_started = time.monotonic()
...
read_elapsed = time.monotonic() - read_started
```

Apply to read, chunk, embed, and upsert timings.

## Verification

Run:

```sh
pytest -m "not integration"
ruff check .
```

If integration services are available:

```sh
pytest tests/integration/test_reindexing.py
```
