# Security Backlog

Issues identified in the May 2026 security review that were not addressed in
`security/hardening` (PR #6). Ordered by severity.

---

## CRITICAL

### C-1 — Live secrets on disk, no rotation procedure

**File:** `.env`

**Issue:** Production `API_KEY` and `JWT_SECRET` are stored as plaintext in `.env` on
the working directory. If this directory is ever synced (Nextcloud), accidentally staged,
or the machine is compromised, both secrets are exposed. There is no documented rotation
runbook, and rotating either secret requires a manual container restart.

**Plan:**
1. Add `.env` to a gitignore audit — verify it is never committed or synced.
2. Write a rotation runbook in the README:
   - `API_KEY`: generate new value, update `.env`, `docker compose up -d api` — existing
     machine clients must update their key.
   - `JWT_SECRET`: generate new value, update `.env`, restart — all active JWTs are
     immediately invalidated (users re-login on next request).
3. Consider moving secrets to Docker secrets or a secrets manager if the deployment
   grows beyond a single machine.

---

## HIGH

### H-6 — Qdrant ports exposed in standalone compose file

**File:** `vector-db/qdrant/docker-compose.yml`, lines 10–11

**Issue:** The standalone Qdrant compose file maps ports `6333:6333` and `6334:6334`
to the host with no authentication. Anyone on the host network can read, modify, or
delete the entire vector database. The main `docker-compose.yml` correctly omits `ports:`
for Qdrant, but this file is a trap.

**Plan:**
1. Remove the `ports:` block from `vector-db/qdrant/docker-compose.yml` entirely, or
   replace it with a localhost-only binding (`127.0.0.1:6333:6333`).
2. Add a comment warning against running it in production.
3. Consider enabling Qdrant's built-in API key auth:
   `QDRANT__SERVICE__API_KEY=<secret>` in the environment and passing the key from
   `settings.py` to `qdrant_client`.

---

## MEDIUM

### M-2 — Blocking SQLite I/O on the async event loop

**File:** `web/api_server.py`, line 77

**Issue:** `_is_valid_token()` calls `user_store.get_hash()` synchronously from inside
`security_middleware`, which is an async context. This blocks the event loop for the
duration of every authenticated request's DB read. Under load this degrades throughput
for all concurrent connections.

**Plan:**
Wrap the DB call in `asyncio.to_thread`:
```python
username = payload.get("sub", "")
return await asyncio.to_thread(user_store.get_hash, username) is not None
```
This requires making `_is_valid_token` async and updating all call sites.
Alternatively: cache validated JWT subjects in an in-process dict with a short TTL
(e.g. 60 seconds) to eliminate per-request DB hits entirely, with cache invalidation
on user removal.

### M-6 — No JWT revocation mechanism

**File:** `web/api_server.py`

**Issue:** Removing a user from the DB invalidates their JWT on the next API call
(the username-in-DB check handles this). However, there is no way to invalidate a
specific token without removing the user entirely. A compromised or stolen token
remains valid until expiry (default 8 hours). Changing a user's password also does
not invalidate existing sessions.

**Plan (choose one based on need):**
- **Option A — Per-user `invalidated_before` timestamp** (recommended): Add an
  `invalidated_before REAL` column to the `users` table. Include an `iat` (issued-at)
  claim in the JWT. In `_is_valid_token`, reject tokens where `iat < invalidated_before`.
  To revoke all sessions for a user: update their `invalidated_before` to `now()`.
  This handles password changes and targeted revocation with no token blacklist.
- **Option B — JWT ID blacklist**: Store a set of revoked `jti` claims in memory or
  SQLite. Simpler to reason about but leaks memory unless pruned at expiry time.

---

## LOW

### L-1 — Single rate-limit bucket covers all endpoints equally

**File:** `web/api_server.py`, lines 49–50

**Issue:** All authenticated endpoints share one 30 req/min bucket per IP. A user
making legitimate streaming chat requests consumes the same allowance as an attacker
probing endpoints. The login endpoint, while no longer bypassed, shares the same
bucket as everything else.

**Plan:** Implement per-endpoint rate limits with a tighter login-specific limit:
```python
_LOGIN_RATE_MAX = 10  # attempts per window
```
Check `request.url.path == "/auth/login"` before the general check and apply the
tighter limit. This is additive — the general limit still applies to all other paths.

### L-2 — LLM model name not validated

**File:** `web/api_server.py`, line 138 (`ChatRequest.model`)

**Issue:** `req.model` is a free-form string passed directly to Ollama. An authenticated
user can request any model name, including ones not in the allowed list.

**Plan:** At request time, validate `req.model` against the list returned by
`GET /v1/models`. If the model is not in the list, return 400. Cache the allowed-model
list from Ollama at server startup (refresh periodically).

### L-3 — LLM answer content logged at INFO level

**File:** `web/api_server.py`, line 342

**Issue:** `logger.info("Answer: %s", answer[:200])` logs the first 200 characters of
every LLM response. If users query sensitive personal or business data from their
indexed documents, those answers appear in application logs.

**Plan:** Remove the log line entirely, or guard it with `logging.DEBUG`:
```python
if logger.isEnabledFor(logging.DEBUG):
    logger.debug("Answer: %s", answer[:200])
```

### L-4 — Unknown client IP collapses into shared rate-limit bucket

**File:** `web/api_server.py`, line 109

**Issue:** When `request.client` is `None` (behind certain proxies), the IP falls back
to `"unknown"`, placing all such requests in one shared bucket. A single noisy upstream
can exhaust the bucket for all other requests that also lack a client IP.

**Plan:** Reject requests with no client address rather than sharing a bucket:
```python
if not request.client:
    return JSONResponse(status_code=400, content={"detail": "Unable to determine client address"})
client_ip = request.client.host
```

### L-5 — No dependency lock file

**File:** `pyproject.toml`, `Dockerfile`

**Issue:** All dependencies are unpinned. The Dockerfile re-resolves versions on every
build. A supply-chain compromise of any dependency is silently picked up on the next
`docker compose build`.

**Plan:** Generate and commit a lock file:
```bash
pip install uv
uv lock   # creates uv.lock
```
Update the Dockerfile to install from the lock file:
```dockerfile
COPY uv.lock .
RUN uv sync --frozen
```

### L-6 — Container runs as root

**File:** `Dockerfile`

**Issue:** No `USER` instruction. All processes run as root inside the container.
A code execution vulnerability in any dependency gives root within the container.

**Plan:**
```dockerfile
RUN useradd -m -u 1001 appuser
RUN chown -R appuser:appuser /app
USER appuser
```
Add before the final `CMD`/entrypoint. Test that volume-mounted paths
(`/app/data`) have appropriate permissions from the host.

---

## INFO

### I-1 — Qdrant has no API key

**File:** `docker-compose.yml`

**Issue:** Qdrant supports `QDRANT__SERVICE__API_KEY` but it is not set. Any container
on the same Docker network can query or modify the vector database without authentication.

**Plan:** Generate a Qdrant API key and wire it through:
1. Add `QDRANT_API_KEY` to `.env` and `docker-compose.yml` for both `qdrant` and `api`.
2. Pass it to `QdrantClient` in `settings.py`: `QdrantClient(host=..., api_key=QDRANT_API_KEY)`.
3. Set `QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY}` in the qdrant environment.

### I-2 — Secrets loaded via raw `os.environ` with no redaction

**File:** `settings.py`

**Issue:** Secrets (`API_KEY`, `JWT_SECRET`) are loaded with `os.environ.get()`.
If any code accidentally logs the settings module or its attributes, secrets appear
in plain text.

**Plan:** Use `pydantic-settings` for automatic redaction:
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    api_key: SecretStr = ""
    jwt_secret: SecretStr = ""
    ...
```
`SecretStr` values display as `**********` in `repr()` and logs.

### I-3 — `~/Code` directory fully indexed, may include credential files

**File:** `config/watcher_config.yaml`

**Issue:** All files under `~/Code` with allowed extensions are indexed into the
vector database. This includes any secrets, tokens, or private keys embedded in
config files, `.env` files (`.env` is not in `allowed_extensions` but `.yaml`,
`.toml`, `.json` are), or scripts under that path. Any authenticated user can
retrieve this content via RAG queries.

**Plan:**
1. Review and tighten `ignore_patterns` in `watcher_config.yaml` to exclude common
   secrets locations: `.env`, `*secret*`, `*credential*`, `*token*`, `*key*`.
2. Consider whether `~/Code` as a whole should be indexed, or only specific
   subdirectories.
3. Add `.env` and `*.pem` to `ignore_patterns` as a baseline.
