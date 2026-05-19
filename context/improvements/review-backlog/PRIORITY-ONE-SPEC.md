# Priority 1 Implementation Spec

Source findings:
- `context/improvements/review-backlog/GRADE-IT-FINDINGS.md`
- `context/improvements/retrieval-performance/RETRIEVAL-FAILURE-HANDLING.md` for later retrieval-failure design only

This spec covers the first implementation batch. These changes address correctness, security, availability, and data-integrity risks that should be handled before the broader cleanup work.

## Scope

Implement these five fixes:

1. Fix streaming semaphore release on executor scheduling failure.
2. Stop deleting old vectors before replacement upsert succeeds.
3. Stop trusting arbitrary `X-Forwarded-For` for rate-limit identity.
4. Make collection reset clear or invalidate fingerprint state.
5. Make `purge_ignored --apply` fail closed when no watch roots are accessible.

Do not include Priority 2 refactors in this batch unless required for a Priority 1 fix.

## 1. Streaming Semaphore Release

### Problem

`web/api_server.py:_rag_stream_response()` acquires `_RAG_CONCURRENCY`, then calls `loop.run_in_executor()`. If scheduling raises before `future` exists and before `add_done_callback()` is attached, the semaphore is never released.

### Files

- `web/api_server.py`
- Tests should be added near existing API/server tests. If no dedicated server test file exists, create `tests/test_api_server_concurrency.py`.

### Implementation

Mirror the non-streaming pattern already used in `_run_rag_with_timeout()`:

- Initialize `future = None` before acquiring/scheduling.
- Acquire `_RAG_CONCURRENCY`.
- Schedule `_run` in the executor.
- Attach a done callback that releases the semaphore.
- Add an outer `except BaseException` guard.
- If scheduling failed before `future` was assigned, release the semaphore in the guard.
- Preserve existing stream behavior and final `data: [DONE]` behavior.

Expected shape:

```python
await _RAG_CONCURRENCY.acquire()
future = None
try:
    future = loop.run_in_executor(_RAG_EXECUTOR, _run)
    future.add_done_callback(lambda _f: _RAG_CONCURRENCY.release())
except BaseException:
    if future is None:
        _RAG_CONCURRENCY.release()
    raise
```

Use the actual surrounding function structure; avoid duplicating release paths.

### Tests

Add a unit test that:

- Replaces `_RAG_CONCURRENCY` with a semaphore of size 1.
- Replaces `_RAG_EXECUTOR` or monkeypatches `loop.run_in_executor()` to raise.
- Starts consumption of `_rag_stream_response()`.
- Asserts the exception propagates.
- Asserts the semaphore slot is released after the failure.

Acceptance:

- A scheduling failure cannot permanently reduce the concurrency limit.
- Existing streaming happy-path tests, if present, still pass.

## 2. Index Replacement Without Delete-Before-Upsert Data Loss

### Problem

`ingest/index_documents.py:_upsert_chunks()` deletes existing vectors for a filepath before upserting replacement points. If delete succeeds and upsert fails, the document disappears from the index until a later successful reindex.

### Files

- `ingest/index_documents.py`
- `api/retrieval.py` if active-version filtering is chosen
- `ingest/cleanup_stale.py` and `ingest/purge_ignored.py` only if payload schema changes affect deletion
- `tests/integration/test_reindexing.py`

### Recommended Design

Use versioned document records with an active marker.

Current payload already includes:

- `document_id`
- `filename`
- `filepath`
- `chunk_index`
- `chunk_total`

Add:

- `index_version`: unique string per indexing attempt, for example `uuid.uuid4()`
- `active`: boolean

New indexing flow:

1. Generate new points with `active=False` and a new `index_version`.
2. Upsert all new points.
3. Mark new version active or otherwise make it queryable.
4. Delete old vectors for the same filepath where `index_version != new_version`.

Qdrant update mechanics must be selected based on client support already available in the installed `qdrant-client`. Prefer a simple implementation with existing APIs:

- Option A: Upsert new points with `active=True`, then delete old points by filepath excluding the new point IDs.
- Option B: Upsert new points with `active=False`, set payload `active=True` for new point IDs, then delete old points by filepath and old version.

If Qdrant filtering cannot express "filepath equals X and id not in new_ids" cleanly, use `index_version` filtering and delete old versions only after the new version is queryable.

Retrieval must query only active points if inactive staging records can exist:

- Add `FieldCondition(key="active", match=MatchValue(value=True))` to the Qdrant filter.
- Ensure unversioned legacy points remain retrievable during migration, or run a migration/reset that gives all current points `active=True`.

The simplest safe first step is:

1. Upsert new points with `active=True` and `index_version`.
2. Delete old points by filepath and not matching the new `index_version`, if supported.
3. If "not matching" is not supported, fetch old point IDs before upsert, then delete those old IDs after upsert succeeds.

Do not delete current vectors until replacement vectors are confirmed written.

### Tests

Extend `tests/integration/test_reindexing.py`:

- Existing reindex test must still prove stale chunks do not accumulate.
- Add a test where old vectors exist, replacement upsert is forced to fail, and old vectors remain present.
- Add a test that retrieval/counting does not include inactive staging points if staging is used.
- Add a test that fingerprint is not updated when replacement upsert fails.

Acceptance:

- A failed replacement upsert leaves the previous indexed version queryable.
- A successful reindex leaves exactly one active version for the filepath.
- Fingerprint updates only after the new active version is in place.

## 3. Trusted Client IP Resolution

### Problem

`web/api_server.py:security_middleware()` uses the first `X-Forwarded-For` value for rate-limit identity on every request. Direct clients can spoof this header and bypass general and login limits.

### Files

- `settings.py`
- `web/api_server.py`
- Tests in a new or existing API middleware test file

### Implementation

Add explicit trusted-proxy configuration:

- `TRUSTED_PROXY_IPS`: comma-separated env var, default empty
- Optional: `TRUST_FORWARDED_HEADERS`: boolean env var, default false

Add helper:

```python
def resolve_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in TRUSTED_PROXY_IPS:
        forwarded = request.headers.get("X-Forwarded-For", "")
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return peer
```

Use the helper in `security_middleware()` for both login and general rate limits.

Do not trust forwarded headers when:

- `request.client` is missing
- the peer IP is not trusted
- the header is empty or malformed

### Tests

Add tests that:

- Direct request with spoofed `X-Forwarded-For` still uses `request.client.host`.
- Trusted proxy request uses first forwarded IP.
- Login rate limit and general rate limit both use the resolved identity.

Acceptance:

- Spoofed forwarded headers cannot bypass rate limits by default.
- Existing local development behavior still works without configuration.

## 4. Reset Collection Must Reset Fingerprints

### Problem

`ingest/reset_collection.py` deletes the Qdrant collection but leaves `data/fingerprints.sqlite3` intact. The watcher then skips unchanged files as already indexed even though their vectors no longer exist.

### Files

- `ingest/reset_collection.py`
- `indexer/fingerprint_store.py`
- Tests for fingerprint reset behavior

### Implementation

Add a fingerprint-store operation:

```python
def clear_hashes() -> None:
    conn = _store.conn
    with conn:
        conn.execute("DELETE FROM fingerprints")
```

Update `reset_collection.py`:

- Initialize the fingerprint DB before clearing.
- Delete the collection if it exists.
- Clear fingerprints by default, because vectors and fingerprints are coupled state.
- Print/log an explicit message that both vector collection and fingerprints were reset.

If a vectors-only reset is still useful, add an explicit `--vectors-only` flag. The default command must reset both states safely.

### Tests

Add unit tests for `clear_hashes()`:

- Insert two hashes.
- Call `clear_hashes()`.
- Assert `list_all_paths()` is empty.

Add a reset script test with monkeypatched Qdrant client:

- Simulate collection exists.
- Assert `delete_collection(COLLECTION)` is called.
- Assert fingerprint hashes are cleared.

Acceptance:

- Running reset cannot leave stale fingerprints that prevent reindexing.
- The command output makes the state reset clear.

## 5. `purge_ignored --apply` Must Fail Closed

### Problem

`ingest/purge_ignored.py:find_ignored_paths()` skips root containment when `accessible_roots` is empty. If the config is wrong or mounts are unavailable, `--apply` can evaluate every tracked fingerprint globally.

### Files

- `ingest/purge_ignored.py`
- Tests for purge behavior

### Implementation

Separate dry-run discovery from destructive apply safety:

- Keep `find_ignored_paths(config)` focused on finding candidates under accessible roots.
- If configured watch paths exist but none are accessible, raise a descriptive exception before returning candidates.
- In `purge_ignored(config_path, apply=True)`, fail before deleting if no accessible roots are available.
- If a global purge is truly needed, require an explicit CLI flag such as `--allow-global` and document the risk.

Recommended minimal behavior:

```python
class NoAccessibleWatchRootsError(RuntimeError):
    pass
```

In `find_ignored_paths()`:

- Read `watch_paths` from config.
- If `watch_paths` is non-empty and `_iter_accessible_roots(config)` returns empty, raise `NoAccessibleWatchRootsError`.

In `main()`:

- Catch the exception.
- Log the error.
- Exit non-zero.

### Tests

Add tests that:

- Config has watch paths, none exist: `purge_ignored(..., apply=True)` raises and does not call `delete_document` or `delete_hash`.
- Dry run also fails closed, or at minimum clearly reports no accessible roots; prefer failing closed for both dry run and apply.
- Config with one accessible root only evaluates tracked files under that root.
- Config with no `watch_paths` is treated as invalid for purge unless an explicit global override is provided.

Acceptance:

- `--apply` cannot delete outside intended accessible roots when mounts/config are wrong.
- Failure message tells the operator which config roots were unavailable.

## Execution Order

Implement in this order:

1. Trusted client IP resolution. Small, security-sensitive, easy to test.
2. Streaming semaphore release. Small availability fix.
3. Purge fail-closed behavior. Prevents destructive mistakes.
4. Reset collection and fingerprints together. Prevents stale state.
5. Safe reindex replacement. Highest data-flow complexity; do after the simpler destructive-state fixes.

## Verification

Run:

```sh
pytest tests/test_auth.py tests/test_rate_limit.py tests/test_request_validation.py tests/test_paths.py
pytest tests/integration/test_reindexing.py
```

If integration services are unavailable, document that the integration suite was skipped and run all non-integration tests:

```sh
pytest -m "not integration"
```
