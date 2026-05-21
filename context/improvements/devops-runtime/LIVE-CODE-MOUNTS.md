# Live Code Mounts for Development

## Problem

Every code change requires a full `docker compose build` + container restart to take effect.
The `rag-system:latest` image bakes source code in at build time, so even a one-line edit
in `api/retrieval.py` means waiting through an image rebuild.

## Proposed Change

Add a bind mount of the repo source over `/app` in the `api` and `watcher` services, and
enable uvicorn's `--reload` flag for the API so file saves trigger an automatic hot restart.

```yaml
api:
  command: uvicorn web.api_server:app --host 0.0.0.0 --port 8000 --reload
  volumes:
    - /path/to/Code/rag-system:/app   # live source — must be Linux-native path
    - rag-data:/app/data
    - hf-cache:/root/.cache/huggingface

watcher:
  volumes:
    - /path/to/Code/rag-system:/app   # live source
    - rag-data:/app/data
    - hf-cache:/root/.cache/huggingface
    - ${NEXTCLOUD_PATH}:/watch/Nextcloud:ro
    - ${CODE_PATH}:/watch/Code:ro
```

Python dependencies (installed into the image's site-packages) survive the mount because
`/app` and `/usr/local/lib/python3.11/site-packages` are separate paths.

## Constraints

- **Use the Linux-native path** (`/path/to/Code/rag-system`), not the Windows FS path
  (`/path/to/Code/rag-system`). The WSL→Windows bridge mounts as an empty
  directory inside the container.
- **New dependencies still require a rebuild.** If `pyproject.toml` changes, run
  `docker compose build` once so site-packages are updated in the image layer.
- **Watcher doesn't auto-reload.** File changes are live in the container filesystem but
  the watcher process must be restarted (`docker compose restart watcher`) to pick them up.
- **`--reload` adds a file watcher inside the API container.** Negligible overhead at this
  scale, but worth noting for production use.

## When to Implement

Implement when iterating frequently on API code. Revert (or use a separate
`docker-compose.override.yml`) before any production-stability audit so the
deployed image is always the built artifact, not a live mount.
