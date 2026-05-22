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

## Review Notes

### Security

1. **Shell injection — HIGH**: All `ollama pull` and `docker compose` invocations
   must use list-form subprocess (`subprocess.run(['ollama', 'pull', model])`),
   never `shell=True` with user-supplied input. Model names and paths come from
   user prompts and must not reach the shell as a raw string.

2. **File permissions — MEDIUM**: After writing `.env`, set permissions to `0o600`.
   Backup files (`.bak`) also contain plaintext secrets and must receive the same
   treatment. Do not leave them world-readable.

3. **Secret generation — MEDIUM**: Use Python's `secrets` module
   (`secrets.token_hex(32)`), never `random`. Specify this explicitly in the
   implementation so it is not left to the implementer's discretion.

4. **Watch path validation — LOW**: Reject paths that are system roots or sensitive
   directories (`/`, `/etc`, `/proc`, `/sys`). User-supplied paths are written
   directly into YAML and Docker mounts; a basic blocklist prevents obvious mistakes.

### Memory

1. **No RAM check — HIGH**: The plan checks disk space (optionally) but not
   available RAM. `qwen2.5:14b` requires approximately 10 GB of RAM or VRAM at
   runtime. Add a required warning to the prerequisite check section. A hard abort
   is not necessary, but the warning should be prominent and not optional.

2. **HuggingFace cache**: `cross-encoder/ms-marco-MiniLM-L-6-v2` (~85 MB) downloads
   to `~/.cache/huggingface/` on first use, outside the project directory. Note this
   location and approximate size in the post-install output so users are not
   surprised by disk usage or a slow first query.

### CPU and Reliability

1. **Health-check polling timeout — MEDIUM**: "Wait for the health check to pass"
   is underspecified. Define concrete parameters: poll every 5 seconds, give up after
   120 seconds, exit non-zero if the check never passes. An unbounded loop here
   will hang the installer indefinitely if a container fails to start.

2. **Model pull progress**: `ollama pull qwen2.5:14b` can take 15+ minutes. Stream
   ollama's stdout directly rather than capturing it. Print a notice before starting
   so the user knows the installer has not stalled.

### Missing Prerequisites

1. **Python version**: Add `python >= 3.11` to the prerequisite check list. The
   project requires 3.11; checking only that Python is installed is insufficient.

2. **`--dry-run` flag**: The implementation steps mention dry-run helpers but the
   User Workflow section does not document the flag. Add a `--dry-run` example to
   the workflow section so it is part of the public interface from the start.

### Idempotency Gaps

1. **Qdrant key rotation**: If `QDRANT_API_KEY` is regenerated on reinstall while
   Qdrant is running, the container will reject the new key until restarted. If the
   volume already contains indexed data, the data remains accessible after restart,
   but the window between key regeneration and container restart is a silent failure
   mode. The idempotency rules should explicitly preserve `QDRANT_API_KEY` on
   reinstall unless the user explicitly requests rotation.

2. **Watch path name collision**: Two paths sharing the same basename (e.g.,
   `/home/user/Code` and `/mnt/backup/Code`) produce duplicate container mount
   names. The open question about name derivation must be resolved before
   implementation. Recommended: auto-derive a unique name from the full path
   (e.g., hash suffix or indexed fallback) rather than prompting the user every time.

3. **Legacy path dual-strategy**: Keeping `NEXTCLOUD_PATH`/`CODE_PATH` alongside
   the new override mechanism means two different mount strategies coexisting in the
   codebase. The open question about removing them should be resolved before coding
   starts. Leaving it open invites an incomplete implementation that handles neither
   case cleanly.

### JWT_SECRET — Resolved

`JWT_SECRET` has been removed. The system was migrated from JWT-backed cookies to
opaque session tokens stored in SQLite. Session tokens are generated with
`secrets.token_hex(32)`, stored in the `sessions` table alongside the user store,
and validated by DB lookup on each request. The `pyjwt` dependency has been removed.
The installer no longer needs to generate or document `JWT_SECRET`.
