# Installer Plan

## Goal

Create a first-run installer that reduces local deployment to one guided command.
The installer should configure secrets, watch paths, model downloads, container
deployment, and basic health checks without requiring manual edits to
`.env`, `docker-compose.yml`, or `config/watcher_config.container.yaml`.

## Recommended Shape

Implement the installer as `scripts/install.py`.

Python is a better fit than shell because the installer needs reliable path
validation, cross-platform behavior, YAML generation, secret generation, and
interactive prompts. The script should be idempotent: rerunning it should update
generated local files without destroying unrelated user choices.

Generated files should be excluded from Git:

- `.env`
- `docker-compose.override.yml`
- `config/watcher_config.local.yaml`

## User Workflow

```bash
python scripts/install.py
```

The script should:

1. Check prerequisites.
2. Prompt for runtime choices.
3. Generate local config files.
4. Pull required Ollama models.
5. Build and start Docker containers.
6. Verify the API health endpoint.
7. Print next operational commands.

## Prerequisite Checks

Validate these before writing config or starting containers:

- Docker is installed and reachable.
- Docker Compose is available through `docker compose`.
- Ollama is installed or reachable at the configured base URL.
- Ollama responds on `http://localhost:11434` by default.
- Python can generate secure random secrets.
- Host watch paths exist and are directories.

Optional checks:

- Warn if available disk space appears too low for model downloads.
- Warn if port `8000` is already in use.
- Warn if watch paths are empty.

## Prompted Values

Prompt for:

- Watch directories, allowing one or more host paths.
- Friendly container-side names for each watch directory.
- Generation model, default `qwen2.5:14b`.
- Embedding model, default `nomic-embed-text`.
- RAG mode, default matching project preference.
- Optional HuggingFace token.
- Optional CORS origins.

Generate automatically unless the user chooses to preserve existing values:

- `QDRANT_API_KEY`
- `API_KEY`
- `JWT_SECRET`

## `.env` Generation

If `.env` does not exist, create it from `.env.example` and fill required
values. If `.env` already exists, update only keys the installer owns or that
the user explicitly chooses to replace.

Required generated values:

```env
QDRANT_API_KEY=<generated>
API_KEY=<generated>
JWT_SECRET=<generated>
RAG_MODE=<selected>
HF_TOKEN=<optional>
CORS_ORIGINS=<optional>
GEN_MODEL=<selected>
EMBED_MODEL=<selected>
```

Keep legacy `NEXTCLOUD_PATH` and `CODE_PATH` support if the base compose file
continues to reference them, but prefer generated override mounts for new
installations.

## Watch Path Configuration

The current `docker-compose.yml` hardcodes two watch mounts:

```yaml
- ${NEXTCLOUD_PATH}:/watch/Nextcloud:ro
- ${CODE_PATH}:/watch/Code:ro
```

That is fine for the current local setup, but a general installer should support
any number of watch directories. Generate `docker-compose.override.yml` instead
of editing the base compose file.

Example generated override:

```yaml
services:
  watcher:
    volumes:
      - ./config/watcher_config.local.yaml:/app/config/watcher_config.local.yaml:ro
      - /home/garret/Code:/watch/Code:ro
      - /home/garret/Nextcloud:/watch/Nextcloud:ro
    environment:
      CONFIG_PATH: /app/config/watcher_config.local.yaml
```

Generate `config/watcher_config.local.yaml` with matching container paths:

```yaml
required_mounts:
  - path: /watch/Code
    require_non_empty: true
  - path: /watch/Nextcloud
    require_non_empty: true

watch_paths:
  - path: /watch/Code
    recursive: true
    exclude_dirs:
      - .git
      - .venv
      - node_modules
  - path: /watch/Nextcloud
    recursive: true
```

Reuse the existing ignore and extension policies from
`config/watcher_config.container.yaml` unless the user explicitly customizes
them.

## Model Downloads

Use Ollama for local model downloads:

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5:14b
```

The embedding model should always be pulled. The generation model should default
to `qwen2.5:14b` but remain configurable.

The reranker is HuggingFace-based:

```text
cross-encoder/ms-marco-MiniLM-L-6-v2
```

Do not try to pull it through Ollama. It will download through the Python
runtime/HuggingFace cache when the reranker is first used. If `HF_TOKEN` is
provided, pass it through `.env` to reduce HuggingFace rate-limit risk.

## Container Deployment

After config and models are ready, run:

```bash
docker compose up -d --build
```

Then verify:

```bash
docker compose ps
curl -fsS http://localhost:8000/healthz
```

The installer should wait for the API health check to pass instead of checking
only once immediately after startup.

## Post-Install Output

Print:

- API URL: `http://localhost:8000`
- Health URL: `http://localhost:8000/healthz`
- How to follow logs:
  - `docker compose logs -f api`
  - `docker compose logs -f watcher`
- How to add a UI user:
  - `docker exec -it rag-api python manage_users.py add <username>`
- Which host paths are mounted and their container-side paths.
- Which models were pulled.

## Idempotency Rules

The installer should be safe to rerun.

- Do not overwrite `.env` without preserving unknown keys.
- Do not overwrite generated watch config unless it was generated by the
  installer or the user confirms replacement.
- Validate existing `docker-compose.override.yml` before replacing it.
- Use clear backups for replaced files, such as
  `docker-compose.override.yml.bak`.

## Tests

Add focused tests for pure functions in the installer:

- `.env` merge and preserve behavior.
- Secret generation shape.
- Host path to container path normalization.
- `docker-compose.override.yml` rendering.
- `watcher_config.local.yaml` rendering.
- Existing config preservation behavior.

Avoid tests that require Docker or Ollama for the unit suite. Runtime checks can
remain manual or be covered by an optional smoke script.

## Implementation Steps

1. Add `.gitignore` entries for generated local files.
2. Add `scripts/install.py` with dry-run and non-interactive helpers.
3. Add rendering helpers for `.env`, compose override, and watcher config.
4. Add model-pull and container-start command wrappers.
5. Add health-check polling.
6. Add tests for config generation.
7. Update `README.md` with a quick-install section.

## Open Questions

- Should the default RAG mode be `strict` to match the current compose default,
  or `augmented` to match current local usage?
- Should the installer offer to create the first web UI user, or only print the
  `manage_users.py` command?
- Should generated watch path names be auto-derived from directory names, or
  explicitly prompted to avoid collisions?
- Should the base compose file remove `NEXTCLOUD_PATH` and `CODE_PATH` mounts
  once the override-based installer exists?
