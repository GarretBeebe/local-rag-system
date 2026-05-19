# Idle Load Findings For Maintainability Branch

## Summary

The `maintainability` branch improves code organization and several retrieval/indexing correctness issues, but it does not materially fix the idle fan/load problem.

The primary idle-load risk remains the filesystem watcher:

- `rag-watcher` still runs `python -m indexer.watcher`.
- `indexer/watcher.py` still uses `PollingObserver(timeout=30)`.
- `config/watcher_config.container.yaml` still recursively watches `/watch/Code` and several broad `/watch/Nextcloud/...` folders.
- Startup still performs a recursive `root.glob("**/*")` initial scan.
- `docker-compose.yml` still bind-mounts `${CODE_PATH}` to `/watch/Code`.

On Docker Desktop/WSL bind mounts, polling broad host-mounted trees means the system is not truly idle even when no query is being processed. File churn in watched paths can also trigger delete/reindex bursts and Ollama embedding calls.

## What The Branch Improves

- The watcher now updates file fingerprints only when `index_file()` reports an indexed outcome. This improves correctness after skipped or failed indexing.
- Retrieval startup is cleaner: reranker and keyword-index initialization moved out of import time and into API lifespan startup.
- `KEYWORD_REFRESH_INTERVAL` is configurable.
- Qdrant client creation is lazy.

These are useful maintainability improvements, but they do not address the core background CPU source.

## Remaining Idle-Load Sources

1. **Recursive polling watcher**
   - The watcher still polls every 30 seconds.
   - The default watch scope still includes `/watch/Code`.
   - Ignore patterns prevent indexing many files, but broad recursive polling still has to traverse/observe large directory trees.

2. **Repeated reindex bursts**
   - Multiple create/modify/delete events for the same path can still enqueue repeated work.
   - When a changed file is indexable, each indexing pass generates embeddings through Ollama.

3. **API startup warmup**
   - `web/api_server.py` still warms the LLM, embedding model, and reranker on API startup.
   - This can spin CPU/GPU while the user perceives the service as idle.

4. **Periodic BM25 rebuild**
   - `api/keyword_index.py` still rebuilds the BM25 index on a timer.
   - The interval is configurable, but the default remains 300 seconds.

## Recommended Changes

### 1. Make Live Watching Optional

Add a watcher mode setting:

```python
WATCHER_MODE = os.environ.get("WATCHER_MODE", "manual")
```

Supported modes:

- `manual`: run startup cleanup and initial scan, drain indexing, then exit.
- `live`: run startup cleanup and initial scan, then start filesystem watching.

Default should be `manual` so normal API idle does not include continuous filesystem polling.

In `indexer/watcher.py`, after `initial_scan(...)`, return early for manual mode:

```python
if WATCHER_MODE == "manual":
    worker.stop()
    return
```

### 2. Remove `/watch/Code` From Default Watch Scope

Remove this default entry from `config/watcher_config.container.yaml`:

```yaml
- path: "/watch/Code"
  recursive: true
```

If code indexing is needed, prefer targeted paths:

```yaml
- path: "/watch/Code/rag-system/context"
  recursive: true
```

or a dedicated knowledge folder:

```yaml
- path: "/watch/Code/knowledge"
  recursive: true
```

### 3. Make Polling Interval Configurable

Add:

```python
WATCH_POLL_INTERVAL_SECONDS = int(os.environ.get("WATCH_POLL_INTERVAL_SECONDS", "300"))
```

Use it in `indexer/watcher.py`:

```python
observer = PollingObserver(timeout=WATCH_POLL_INTERVAL_SECONDS)
```

For Docker Desktop/WSL bind mounts, a 300-second default is a better live-mode baseline than 30 seconds. Users who need near-real-time indexing can opt into 30 seconds.

### 4. Add Watcher Debounce/Deduping

The watcher should avoid indexing the same path repeatedly within a short window.

Recommended behavior:

- Normalize the path.
- Keep a `pending_paths` set.
- If a path is already pending, do not enqueue it again.
- Optionally wait 2-5 seconds before indexing so save bursts collapse into one pass.
- Remove the path from `pending_paths` after the worker finishes.

This reduces repeated delete/reindex bursts from editor saves, sync tools, and filesystem metadata churn.

### 5. Make API Warmup Opt-In

Add:

```python
WARM_MODELS_ON_STARTUP = os.environ.get("WARM_MODELS_ON_STARTUP", "false").lower() == "true"
```

Only call `_warm_models()` when this is enabled. Default should be `false`.

Warmup improves first-query latency, but it can cause visible idle load on startup, especially when the LLM warmup hits Ollama.

### 6. Reduce BM25 Refresh Frequency

Change the default:

```python
KEYWORD_REFRESH_INTERVAL = int(os.environ.get("KEYWORD_REFRESH_INTERVAL", "1800"))
```

Longer term, replace periodic full rebuilds with an event-driven mechanism:

- Watcher writes an index-change marker or SQLite event.
- API checks the marker/event table.
- BM25 rebuilds only when indexed content actually changed.

### 7. Update Docker Defaults

Add defaults to the API/watcher services as applicable:

```yaml
WATCHER_MODE: ${WATCHER_MODE:-manual}
WATCH_POLL_INTERVAL_SECONDS: ${WATCH_POLL_INTERVAL_SECONDS:-300}
WARM_MODELS_ON_STARTUP: ${WARM_MODELS_ON_STARTUP:-false}
KEYWORD_REFRESH_INTERVAL: ${KEYWORD_REFRESH_INTERVAL:-1800}
```

Consider putting the watcher behind a Compose profile:

```yaml
profiles:
  - indexing
```

Then normal startup runs API and Qdrant only, while indexing is explicit:

```bash
docker compose --profile indexing up watcher
```

## Expected Result

After these changes, idle behavior should be closer to genuinely idle:

- API and Qdrant remain running with low background CPU.
- Ollama does not warm models unless explicitly requested.
- BM25 rebuilds less often or only when needed.
- Broad recursive polling of `/watch/Code` no longer happens by default.
- Indexing remains available manually or through opt-in live watching.
