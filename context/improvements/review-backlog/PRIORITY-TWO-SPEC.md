# Priority 2 Implementation Spec

Source findings:
- `context/improvements/review-backlog/GRADE-IT-FINDINGS.md`
- `context/improvements/retrieval-performance/RETRIEVAL-FAILURE-HANDLING.md`

This spec covers the second implementation batch: correctness improvements, request semantics, maintainability fixes, and operational configuration cleanup. These should follow the Priority 1 fixes.

## Scope

Implement these fixes:

1. Put RAG semaphore acquisition under timeout and initialize executor/concurrency defensively.
2. Extract the chat question from the latest user message.
3. Preserve module-level Python code during chunking.
4. Make partial embedding failures either fail the document or renumber stored chunks.
5. Move hardcoded operational timeouts into settings.
6. Remove import-time logging configuration from cleanup/purge scripts.
7. Later design-backed fix: distinguish retrieval failure from empty results.

## 1. RAG Semaphore Timeout and Defensive Initialization

### Problem

`web/api_server.py:_run_rag_with_timeout()` waits on `_RAG_CONCURRENCY.acquire()` before starting the request timeout. Also, `_RAG_EXECUTOR` and `_RAG_CONCURRENCY` are module-level annotations with no initial value, so code that calls RAG helpers before lifespan startup gets `NameError`.

### Files

- `web/api_server.py`
- Tests in API/server test module

### Implementation

Initialize globals:

```python
_RAG_EXECUTOR: ThreadPoolExecutor | None = None
_RAG_CONCURRENCY: asyncio.Semaphore | None = None
```

Add helpers:

```python
def _get_rag_executor() -> ThreadPoolExecutor:
    if _RAG_EXECUTOR is None:
        raise RuntimeError("RAG executor has not been initialized")
    return _RAG_EXECUTOR

def _get_rag_concurrency() -> asyncio.Semaphore:
    if _RAG_CONCURRENCY is None:
        raise RuntimeError("RAG concurrency limiter has not been initialized")
    return _RAG_CONCURRENCY
```

Use those helpers in streaming and non-streaming paths.

Wrap semaphore acquire in timeout:

```python
try:
    await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
except TimeoutError:
    raise HTTPException(
        status_code=504,
        detail="RAG pipeline timed out waiting for capacity.",
    ) from None
```

Decide whether the timeout budget should include both queue wait and generation. Prefer one total budget:

- Record `started = time.monotonic()`.
- After acquire, compute `remaining = timeout - (time.monotonic() - started)`.
- If `remaining <= 0`, release and return 504.
- Use `remaining` for `asyncio.wait_for(future, timeout=remaining)`.

### Tests

Add tests for:

- Calling helper before lifespan raises `RuntimeError`, not `NameError`.
- Capacity wait times out when semaphore cannot be acquired.
- Semaphore is released if timeout occurs after scheduling.

Acceptance:

- RAG request timeout includes waiting for concurrency capacity.
- Startup-order failures are descriptive.

## 2. Chat Question Extraction Uses Latest User Message

### Problem

`web/schemas.py:extract_question_from_messages()` uses the last message regardless of role. OpenAI-compatible request histories can end with `assistant`, `system`, or `tool` messages.

### Files

- `web/schemas.py`
- `tests/test_request_validation.py`

### Implementation

Change extraction to scan from the end:

```python
for message in reversed(messages):
    if message.role == "user":
        content = message.content
        break
else:
    raise HTTPException(status_code=400, detail="Chat request must include a user message")
```

Keep existing structured-content behavior for the selected user message.

Clarify validation:

- Empty selected user message still raises `Last user message content is empty`.
- Oversized selected question still raises size-limit error.

### Tests

Add tests:

- User message followed by assistant message extracts the user message.
- No user message raises 400.
- Last user structured content still works.
- Empty latest user message raises 400 even if earlier user messages had text.

Acceptance:

- Retrieval/generation is based on the latest user message only.

## 3. Preserve Module-Level Python Code During Chunking

### Problem

`ingest/chunkers.py:chunk_python()` indexes only top-level functions/classes when any definitions exist. Imports, constants, module setup, and script-level statements disappear from the vector store.

### Files

- `ingest/chunkers.py`
- `tests/test_chunking.py`

### Implementation

Use AST node line numbers to preserve all top-level source spans:

1. Parse the file.
2. Build a sorted list of top-level nodes with `lineno` and `end_lineno`.
3. Emit chunks for gaps between definition nodes when the gap contains non-whitespace source.
4. Emit function/class chunks as today.
5. For oversized chunks, delegate to `chunk_text()`.
6. Preserve source order.

Simpler acceptable approach:

- Treat every top-level AST node as a chunk candidate, not only definitions.
- Merge adjacent small non-definition nodes into a preamble/setup chunk to avoid tiny chunks.

Requirements:

- Imports and constants before the first function must be indexed.
- Assignment or setup code between functions must be indexed.
- `if __name__ == "__main__":` blocks must be indexed.
- Files with invalid syntax still fall back to `chunk_text()`.

### Tests

Add tests:

- File with imports/constants plus functions includes imports/constants chunk.
- File with assignment between two functions includes that assignment.
- File with main guard includes main guard text.
- Order of chunks follows source order.
- Oversized module-level block is split.

Acceptance:

- No source text is dropped solely because top-level definitions exist.

## 4. Partial Embedding Failure Metadata

### Problem

`ingest/index_documents.py:_embed_chunks()` skips chunks that fail embedding but keeps original chunk indexes and sets `chunk_total` to the number of successful points. Citations can become inconsistent.

### Files

- `ingest/index_documents.py`
- Unit or integration tests for indexing behavior

### Recommended Behavior

Fail the whole document on any embedding failure.

Rationale:

- It avoids partial, misleading document representation.
- It preserves old vectors if combined with the Priority 1 safe-reindex fix.
- It keeps citation metadata simple.

Implementation:

- Change `_embed_chunks()` to raise or return a failure marker on first embedding error.
- In `index_file()`, catch embedding failure and return `"failed"`.
- Do not call `_upsert_chunks()` when any chunk fails.
- Do not update fingerprints.

Alternative if partial indexing is required:

- Collect successful chunks.
- Renumber `chunk_index` from `0..len(points)-1`.
- Set `chunk_total = len(points)`.
- Log skipped original indexes.

Prefer fail-whole-document unless product requirements demand partial results.

### Tests

Add tests:

- If embedding one chunk raises, `index_file()` returns `"failed"`.
- No upsert is attempted after partial embedding failure.
- Fingerprint is not updated.
- Existing vectors remain queryable after failed reindex once Priority 1 safe-reindex is implemented.

Acceptance:

- Stored chunk metadata cannot report impossible sequences.

## 5. Settings-Backed Operational Timeouts

### Problem

Timeouts are hardcoded in multiple files:

- `api/ollama_client.py:64,76` uses `120.0`
- `api/embed.py:40` uses `60`
- `web/api_server.py:263` uses `5.0`
- `web/api_server.py:330` uses `60`

### Files

- `settings.py`
- `api/ollama_client.py`
- `api/embed.py`
- `web/api_server.py`
- README or operations docs if environment variables are documented

### Implementation

Add settings:

```python
OLLAMA_GENERATE_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_GENERATE_TIMEOUT_SECONDS", "120.0"))
OLLAMA_EMBED_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_EMBED_TIMEOUT_SECONDS", "60.0"))
OLLAMA_MODEL_LIST_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_MODEL_LIST_TIMEOUT_SECONDS", "5.0"))
OLLAMA_WARMUP_TIMEOUT_SECONDS = float(os.environ.get("OLLAMA_WARMUP_TIMEOUT_SECONDS", "60.0"))
```

Use these constants at call sites.

Keep function-level timeout parameters where useful, but set their defaults from settings:

```python
def generate(prompt: str, model: str, timeout: float = OLLAMA_GENERATE_TIMEOUT_SECONDS) -> str:
```

### Tests

Add tests or monkeypatches that verify:

- `embed()` passes `OLLAMA_EMBED_TIMEOUT_SECONDS` to `ollama_client.post`.
- `models()` passes model-list timeout.
- Warmup passes warmup timeout.

Acceptance:

- No operational timeout literals remain at those call sites.

## 6. Remove Import-Time Logging Configuration

### Problem

`ingest/cleanup_stale.py` and `ingest/purge_ignored.py` call `logging.basicConfig()` at import time. `cleanup_stale.py` is imported by the watcher, so it can affect root logger configuration during normal service startup.

### Files

- `ingest/cleanup_stale.py`
- `ingest/purge_ignored.py`

### Implementation

- Add `logger = logging.getLogger(__name__)` in each module.
- Remove module-level `logging.basicConfig(...)`.
- Configure logging inside `main()` only:

```python
def main() -> None:
    logging.basicConfig(...)
    ...
```

- Replace `logging.info/error/warning` calls in library functions with `logger.info/error/warning`.

### Tests

Optional but useful:

- Importing the modules does not call `logging.basicConfig`.
- Library functions log through module logger.

Acceptance:

- Importing cleanup modules has no root-logger side effects.

## 7. Retrieval Failure Handling

This is intentionally last in Priority 2 because it touches retrieval semantics and response behavior.

Use `context/improvements/retrieval-performance/RETRIEVAL-FAILURE-HANDLING.md` as the detailed design source.

High-level requirements:

- Introduce typed retrieval failure signal.
- Qdrant/vector recall failure propagates.
- BM25 keyword failure remains degraded-but-recoverable.
- Strict mode refuses with retrieval-unavailable wording.
- Augmented mode can fall back to model-only but must surface a degraded-answer notice.
- Tests must cover strict/augmented behavior for no context versus retrieval failure.

## Verification

Run:

```sh
pytest -m "not integration"
```

If Qdrant/Ollama test services are available, also run:

```sh
pytest tests/integration/test_reindexing.py
```
