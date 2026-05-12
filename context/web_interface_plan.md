# Web Interface Plan

## What We're Building and Why

A first-party chat UI served directly from the existing `rag-api` container.
The goal is a simple, self-contained page that lets a browser user ask questions
against the RAG system without needing an external client (Open WebUI, Chatbox).
It should feel lightweight — not a full app framework.

## What Is Explicitly Out of Scope

- Session management, JWTs, OAuth — Basic Auth is sufficient
- Persistent chat history across page reloads
- File upload / manual indexing via the UI (the watcher handles ingestion)
- A separate container or build step
- Any Node.js toolchain

## Recommended Approach

**Single `web/index.html` served from the existing FastAPI server.**

- No new service, no new Docker container, no build step.
- FastAPI's `StaticFiles` mounts the `web/` directory at `/ui`; the root
  redirect (`GET /`) stays as-is so the existing health check is unaffected.
- The page is plain HTML + vanilla JS. No framework. The interface is a chat
  window — it does not need React.
- Streaming uses the browser's `fetch` + `ReadableStream` to consume the SSE
  chunks the server already emits. No polling, no WebSocket.
- Markdown rendering uses `marked.js` loaded from a CDN (one `<script>` tag)
  so citations and code blocks in answers render cleanly without a build step.

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
  │  {username, password}  ──────► │  bcrypt.verify() — runs once
  │                                │  jwt.encode({sub: username,
  │  {token: <JWT>}        ◄──────  │              exp: now + 8h})
  │                                │
  │  store JWT in localStorage     │
  │                                │
  │  GET /v1/models                │
  │  Authorization: Bearer <JWT> ► │  jwt.decode() — fast signature check
  │  200 OK                ◄──────  │
```

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
            pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            return True
        except pyjwt.InvalidTokenError:
            return False
    return False
```

**Login endpoint** (new — not covered by auth middleware):

```python
@app.post("/auth/login")
async def login(credentials: LoginRequest) -> dict[str, str]:
    stored = AUTH_USERS.get(credentials.username)
    valid = stored and await asyncio.to_thread(bcrypt_ctx.verify, credentials.password, stored)
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
AUTH_USERS: dict[str, str] = {
    u.strip(): h.strip()
    for line in os.environ.get("AUTH_USERS", "").splitlines()
    if ":" in (line := line.strip())
    for u, h in [line.split(":", 1)]
}
JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "8"))
```

### Client side (`web/index.html`)

Login form: username + password fields.

Flow:
1. On load, check `localStorage` for a stored JWT; if present attempt `GET /v1/models`.
2. If `401` or no JWT stored, show the login form.
3. On submit, `POST /auth/login` with credentials; store returned JWT in `localStorage`.
4. On success, show the chat UI.
5. On any subsequent `401` (expired or revoked token), clear `localStorage` and re-show the form.

### Credential management

Hash passwords before adding to `AUTH_USERS`:

```bash
python -c "from passlib.hash import bcrypt; print(bcrypt.hash('yourpassword'))"
```

Set in `.env` (one `username:hash` per line):

```
AUTH_USERS=alice:$2b$12$...
  bob:$2b$12$...
```

Generate `JWT_SECRET`:

```bash
openssl rand -hex 32
```

To revoke a user: remove their line from `AUTH_USERS` and restart the `api`
container. Their current JWT will be rejected at next verification because
`_is_valid_token` checks the username is still in `AUTH_USERS` after decode.
The API key and other users are unaffected.

### New dependencies

Add to both `pyproject.toml` and the `Dockerfile` pip install block:
- `passlib[bcrypt]`
- `PyJWT`

### Security note

TLS is already in place via Caddy. Credentials and tokens never transit in
plaintext over the internet. For local access (`http://localhost:8000/ui/`),
traffic is on loopback — acceptable.

## Files to Touch

| File | Change |
|------|--------|
| `web/index.html` | New — username/password login form and chat UI (HTML + embedded CSS + JS) |
| `web/api_server.py` | Mount `StaticFiles` at `/ui`; add `POST /auth/login` endpoint; extend `security_middleware` to accept API key or JWT Bearer; add `_is_valid_token` helper |
| `settings.py` | Parse `AUTH_USERS`, `JWT_SECRET`, `JWT_EXPIRY_HOURS` env vars at startup |
| `pyproject.toml` | Add `passlib[bcrypt]` and `PyJWT` dependencies |
| `Dockerfile` | Add `passlib[bcrypt]` and `PyJWT` to pip install block |
| `.env.example` | Add `AUTH_USERS`, `JWT_SECRET`, `JWT_EXPIRY_HOURS` with instructions |
| `docker-compose.yml` | Pass `AUTH_USERS`, `JWT_SECRET`, `JWT_EXPIRY_HOURS` through to the `api` service |

## UI Layout

```
┌─────────────────────────────────────────────────┐
│  Local RAG                    Model: [dropdown▼] │
│                               Mode:  [dropdown▼] │
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
- Mode dropdown: `strict` / `augmented` (maps to `RAG_MODE` semantics, sent
  as a system message or query parameter — see open question below)
- Textarea submits on Enter (Shift+Enter for newline)
- Assistant messages render markdown; user messages render as plain text
- A spinner / "thinking…" state while the first token hasn't arrived yet

## Alternative Considered and Rejected

**Separate Next.js / React container** — adds a second service, a Node.js
build step, volume mounts, and a new port to expose. The interface is a chat
window with a dropdown; none of that complexity is warranted.

**Open WebUI** — already documented as the recommended external client. A
first-party UI is lighter and doesn't require the user to run a third-party
container. The two can coexist since they hit the same API.

## Auth UX Decisions (decide before implementing)

### Session persistence: `sessionStorage` vs `localStorage`

The plan currently stores the API key in `sessionStorage`, which clears when
the tab closes. This means re-entering the key every session.

**Option A — `sessionStorage` (current plan)**
- Key is gone when the tab closes
- Slightly more secure: no persistent credential in the browser
- More friction: paste the key every session

**Option B — `localStorage`**
- Key persists until explicitly cleared or rotated
- Enter once, works forever on that browser
- Better UX for a single-user personal tool with no shared devices

Recommendation: use `localStorage` — this is a personal single-user system,
persistent login is the right default.

### Auto-auth via `?key=` query parameter

Support `https://ai.spoonscloud.duckdns.org/ui/?key=<api-key>` so the page
can be bookmarked with the key embedded. On load:

1. Read `?key=` from the URL if present
2. Store it in `localStorage`
3. Strip the param from the URL with `history.replaceState` (keeps the
   address bar clean and prevents the key appearing in server logs or
   browser history after the first load)
4. Proceed directly to the chat UI — no login form shown

**Trade-off:** the key appears in the browser history on the very first load
before `replaceState` runs, and in any HTTP server logs if the request hits
a logging proxy before Caddy. Acceptable for a personal tool; avoids the
paste-on-every-device problem.

Recommendation: implement both `localStorage` persistence and `?key=`
auto-auth — they complement each other with no server-side changes required.

---

## Open Questions

1. **RAG mode per-request**: `RAG_MODE` is currently a server-side env var,
   not a per-request field. The UI dropdown for mode either (a) has no effect
   today and is informational only, (b) requires adding a `rag_mode` field to
   `ChatRequest` and threading it through the pipeline, or (c) is omitted from
   v1. Recommendation: omit it from v1 — shipping a working chat UI is the
   goal; mode switching can follow.

2. **Citation display**: The API embeds citations inline in the answer text as
   markdown. `marked.js` will render them. No special parsing needed unless we
   want to call them out visually (e.g., collapse into a "Sources" section).
   Defer to v1 — render the full answer as markdown and revisit.

3. **Error display**: The API returns 504 on timeout and 500 on pipeline error.
   The UI should display these as an inline error message rather than silently
   failing or logging to console only.

## Risks

- **Health check**: `docker-compose.yml` hits `GET /` with `curl -f`. Returning
  a redirect (302 → `/ui/`) still passes `curl -f`. Verified safe.
- **CDN dependency**: `marked.js` loaded from a CDN means no internet = no
  markdown rendering. Acceptable for a local-first tool; the text still
  displays, just unstyled. Mitigation: vendor the script into `web/` if needed.
- **Streaming in older browsers**: `fetch` + `ReadableStream` works in all
  modern browsers. Not a concern for a local tool.
- **API key in transit**: When accessed via `ai.spoonscloud.duckdns.org`,
  the key transits over TLS (Caddy). When accessed via `localhost`, it
  transits in plaintext — acceptable on loopback. No action needed.
- **`sessionStorage` key storage**: The API key stored in `sessionStorage`
  is cleared when the tab closes but is readable by any JS on the page.
  Since there is no third-party JS (only the optional CDN script), the
  attack surface is minimal. Vendoring `marked.js` eliminates it entirely.

## Success Criteria

1. Navigating to `http://localhost:8000/ui/` opens the chat page
2. The model dropdown lists whatever models are in Ollama
3. Sending a question returns a streamed answer that renders as markdown
4. Errors (timeout, pipeline failure) display inline in the chat
5. No new containers, no build step, no new dependencies beyond `marked.js` (client) and `passlib[bcrypt]` + `PyJWT` (server)
6. Machine clients using `Authorization: Bearer <API_KEY>` continue to work unchanged
7. Web UI users can log in with username and password; `POST /auth/login` returns a JWT; invalid credentials return 401
8. JWT is stored in `localStorage` and sent as `Authorization: Bearer <jwt>` on subsequent requests — bcrypt runs once per session, not per request
9. After JWT expiry (default 8h), the next request returns 401 and the login form reappears
10. Revoking one user (removing from `AUTH_USERS`) does not affect the API key or other users
11. With both `API_KEY` and `JWT_SECRET` unset: server runs open (local dev preserved)
12. `/ui/` paths and `POST /auth/login` load without credentials so the login form is reachable
