# Web Interface Plan

## What We're Building and Why

A first-party chat UI served directly from the existing `rag-api` container.
The goal is a simple, self-contained page that lets a browser user ask questions
against the RAG system without needing an external client (Open WebUI, Chatbox).
It should feel lightweight — not a full app framework.

## What Is Explicitly Out of Scope

- Persistent chat history across page reloads
- File upload / manual indexing via the UI (the watcher handles ingestion)
- A separate container or build step
- Any Node.js toolchain
- Self-service registration or password reset (provisioning is admin-only via CLI)
- RAG mode per-request switching (mode is server-side env var; omitted from v1)

## Recommended Approach

**Single `web/index.html` served from the existing FastAPI server.**

- No new service, no new Docker container, no build step.
- FastAPI's `StaticFiles` mounts the `web/` directory at `/ui`; the root
  endpoint (`GET /`) stays as-is so the existing health check is unaffected.
- The page is plain HTML + vanilla JS. No framework. The interface is a chat
  window — it does not need React.
- Streaming uses the browser's `fetch` + `ReadableStream` to consume the SSE
  chunks the server already emits. No polling, no WebSocket.
- Markdown rendering uses `marked.js` loaded from a CDN (one `<script>` tag)
  so citations and code blocks in answers render cleanly without a build step.
- The UI is fully responsive and works on both desktop and mobile browsers.
  Layout uses CSS flexbox with `100dvh` (dynamic viewport height) so the
  on-screen keyboard on iOS/Android does not break the input bar placement.

## Authentication

**Two parallel auth mechanisms in a single middleware check.**

| Client type | Mechanism | Per-request cost |
|---|---|---|
| Machine clients (Chatbox, scripts, Open WebUI) | `Authorization: Bearer <API_KEY>` | String equality — microseconds |
| Web UI users | `Authorization: Bearer <JWT>` | JWT signature verification — fast |

Web UI users exchange their password for a JWT once at login. bcrypt runs
**once per login session**, not once per request. Machine clients use the
static API key unchanged.

Either credential type grants access. Both can be enabled simultaneously.
If neither is configured, the server runs open (local dev behaviour preserved).

### Flow

```
Browser                          Server
  │                                │
  │  POST /auth/login              │
  │  {username, password}  ──────► │  bcrypt.verify() against DB hash — runs once
  │                                │  jwt.encode({sub: username,
  │  {token: <JWT>}        ◄──────  │              exp: now + 8h})
  │                                │
  │  store JWT in localStorage     │
  │                                │
  │  GET /v1/models                │
  │  Authorization: Bearer <JWT> ► │  jwt.decode() + check username still in DB
  │  200 OK                ◄──────  │
```

### User store (`data/user_store.py`)

User credentials are stored in `data/users.sqlite3`, persisted via the existing
`rag-data` Docker volume (already mounted at `/app/data` in the `api` container).
The module follows the same pattern as `indexer/fingerprint_store.py`: stdlib
`sqlite3`, thread-local connections, WAL mode.

```
users
├── username  TEXT PRIMARY KEY
├── password_hash  TEXT NOT NULL   ← bcrypt hash, never plaintext
└── created_at  REAL NOT NULL
```

Functions: `init_db()`, `get_hash(username) -> str | None`,
`upsert_user(username, password_hash)`, `delete_user(username)`,
`list_users() -> list[str]`.

### Server side (`web/api_server.py`)

**Middleware bypass** — extend to cover `/ui/` and the login endpoint:

```python
BYPASS_PATHS = {"/", "/auth/login"}

if request.url.path in BYPASS_PATHS or request.url.path.startswith("/ui/"):
    return await call_next(request)
```

**Bearer check** — API key exact match first, then JWT verification:

```python
if API_KEY or JWT_SECRET:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not _is_valid_token(token):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
```

```python
import jwt as pyjwt

def _is_valid_token(token: str) -> bool:
    if API_KEY and token == API_KEY:
        return True
    if JWT_SECRET:
        try:
            payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            return user_store.get_hash(payload.get("sub", "")) is not None
        except pyjwt.InvalidTokenError:
            return False
    return False
```

Checking the username against the DB after decode means removing a user
immediately revokes their JWT on the next request — no JWT blacklist needed.

**Login endpoint** (new — not covered by auth middleware):

```python
@app.post("/auth/login")
async def login(credentials: LoginRequest) -> dict[str, str]:
    stored = user_store.get_hash(credentials.username)
    valid = stored and await asyncio.to_thread(_bcrypt.verify, credentials.password, stored)
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = pyjwt.encode(
        {"sub": credentials.username, "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)},
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"token": token}
```

### `settings.py`

```python
JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "8"))
```

No `AUTH_USERS` env var — credentials live in the DB only.

### Client side (`web/index.html`)

Login form: username + password fields.

Flow:
1. On load, check `localStorage` for a stored JWT; if present attempt `GET /v1/models`.
2. If `401` or no JWT stored, show the login form.
3. On submit, `POST /auth/login` with credentials; store returned JWT in `localStorage`.
4. On success, show the chat UI.
5. On any subsequent `401` (expired or revoked token), clear `localStorage` and re-show the form.

### Credential management

Users are managed via `manage_users.py` at the repo root. Run it directly or
through the running container — no restart needed.

```bash
# Add a user (prompts for password, bcrypt-hashes it, writes to DB)
docker exec -it rag-api python manage_users.py add alice

# Remove a user (existing JWT invalidated on next request)
docker exec -it rag-api python manage_users.py remove alice

# List all usernames
docker exec -it rag-api python manage_users.py list
```

Generate `JWT_SECRET`:

```bash
openssl rand -hex 32
```

Set in `.env`:

```
JWT_SECRET=<64-char hex string>
JWT_EXPIRY_HOURS=8   # optional, default 8
```

**Password changes** do not automatically invalidate existing JWTs (the token
only encodes the username). To force all sessions to re-authenticate, rotate
`JWT_SECRET` and restart the `api` container.

### New dependencies

Add to both `pyproject.toml` and the `Dockerfile` pip install block:
- `passlib[bcrypt]`
- `PyJWT`

`sqlite3` is stdlib — no new dependency.

### Security note

TLS is already in place via Caddy. Credentials and tokens never transit in
plaintext over the internet. For local access (`http://localhost:8000/ui/`),
traffic is on loopback — acceptable.

bcrypt hashes are stored only in `data/users.sqlite3` (a Docker-managed volume
on the host filesystem), not in environment variables. This avoids hash exposure
via `docker inspect` or process environment leakage.

## Files to Touch

| File | Change |
|------|--------|
| `data/user_store.py` | New — SQLite-backed user store following `fingerprint_store.py` pattern |
| `manage_users.py` | New — CLI for adding, removing, and listing users |
| `web/index.html` | New — username/password login form and chat UI (HTML + embedded CSS + JS) |
| `web/api_server.py` | Mount `StaticFiles` at `/ui`; add `POST /auth/login`; extend `security_middleware` to accept API key or JWT; add `_is_valid_token` helper; call `user_store.init_db()` in lifespan |
| `settings.py` | Add `JWT_SECRET`, `JWT_EXPIRY_HOURS` env vars |
| `pyproject.toml` | Add `passlib[bcrypt]` and `PyJWT` dependencies |
| `Dockerfile` | Add `passlib[bcrypt]` and `PyJWT` to pip install block |
| `.env.example` | Add `JWT_SECRET`, `JWT_EXPIRY_HOURS` with instructions; document `manage_users.py` |
| `docker-compose.yml` | Pass `JWT_SECRET`, `JWT_EXPIRY_HOURS` through to the `api` service |

## UI Layout

```
┌─────────────────────────────────────────────────┐
│  Local RAG                    Model: [dropdown▼] │
├─────────────────────────────────────────────────┤
│                                                 │
│  [assistant message, markdown rendered]         │
│                                                 │
│                        [user message]           │
│                                                 │
│  [streaming assistant response...]              │
│                                                 │
├─────────────────────────────────────────────────┤
│  [textarea]                          [Send ▶]   │
└─────────────────────────────────────────────────┘
```

- Model dropdown populated on load from `GET /v1/models`
- Textarea submits on Enter (Shift+Enter for newline)
- Assistant messages render markdown via `marked.js`; user messages render as plain text
- A "thinking…" state while the first token hasn't arrived yet
- Errors (timeout, pipeline failure) display inline in the message list
- Fully responsive — works on desktop and mobile browsers

## Alternatives Considered and Rejected

**Separate Next.js / React container** — adds a second service, a Node.js
build step, volume mounts, and a new port to expose. The interface is a chat
window with a dropdown; none of that complexity is warranted.

**Open WebUI** — already documented as the recommended external client. A
first-party UI is lighter and doesn't require the user to run a third-party
container. The two can coexist since they hit the same API.

**`AUTH_USERS` env var (bcrypt hashes in `.env`)** — appropriate for 1–3 users
but awkward to manage at scale: multiline env var formatting is fragile,
requires a container restart per change, and hashes are exposed via
`docker inspect`. SQLite in the existing `rag-data` volume is cleaner for
5+ users and requires no new infrastructure.

## Risks

- **Health check**: `docker-compose.yml` hits `GET /` with `curl -f`. The root
  endpoint returns JSON directly (no redirect), so this is unaffected.
- **CDN dependency**: `marked.js` loaded from a CDN means no internet = no
  markdown rendering. Acceptable for a local-first tool; text still displays,
  just unstyled. Mitigation: vendor the script into `web/` if needed.
- **Streaming in older browsers**: `fetch` + `ReadableStream` works in all
  modern browsers. Not a concern for a local tool.
- **Credentials in transit**: When accessed via `ai.spoonscloud.duckdns.org`,
  credentials transit over TLS (Caddy). When accessed via `localhost`, traffic
  is on loopback — acceptable.
- **JWT stored in localStorage**: Readable by JS on the page. Since there is
  no third-party JS (only the optional CDN script), the attack surface is
  minimal. Vendoring `marked.js` eliminates it entirely.

## Success Criteria

1. Navigating to `http://localhost:8000/ui/` opens the chat page without credentials
2. The model dropdown lists whatever models are in Ollama
3. Sending a question returns a streamed answer that renders as markdown
4. Errors (timeout, pipeline failure) display inline in the chat
5. No new containers, no build step, no new dependencies beyond `marked.js` (client) and `passlib[bcrypt]` + `PyJWT` (server)
6. Machine clients using `Authorization: Bearer <API_KEY>` continue to work unchanged
7. Web UI users can log in with username and password; `POST /auth/login` returns a JWT; invalid credentials return 401
8. JWT is stored in `localStorage` and sent as `Authorization: Bearer <jwt>` on subsequent requests — bcrypt runs once per session, not per request
9. After JWT expiry (default 8h), the next request returns 401 and the login form reappears
10. Revoking a user via `manage_users.py remove <username>` invalidates their JWT on the next request without restarting the container or affecting other users
11. With both `API_KEY` and `JWT_SECRET` unset: server runs open (local dev preserved)
12. `manage_users.py add/remove/list` works correctly both on the host and via `docker exec`
