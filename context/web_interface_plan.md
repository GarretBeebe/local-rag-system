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

| Client type | Mechanism | Credential |
|---|---|---|
| Machine clients (Chatbox, scripts, Open WebUI) | `Authorization: Bearer <API_KEY>` | Single shared `API_KEY` env var |
| Web UI users | `Authorization: Basic base64(user:pass)` | Per-user bcrypt hashes in `AUTH_USERS` env var |

Either credential type grants access. Both can be enabled simultaneously.
If neither is configured, the server runs open (local dev behaviour preserved).

### Server side (`web/api_server.py`)

Two changes to `security_middleware`:

**1. Extend the bypass to cover `/ui/` paths** so the HTML shell loads before
the user has authenticated:

```python
if (request.url.path == "/" and request.method == "GET") or \
        request.url.path.startswith("/ui/"):
    return await call_next(request)
```

**2. Accept either Bearer or Basic auth:**

```python
if API_KEY or AUTH_USERS:
    auth = request.headers.get("Authorization", "")
    bearer_ok = bool(API_KEY and auth == f"Bearer {API_KEY}")
    basic_ok = auth.lower().startswith("basic ") and \
               await asyncio.to_thread(_verify_basic_auth, auth)
    if not bearer_ok and not basic_ok:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
```

Add a sync helper (runs in thread pool because bcrypt is CPU-bound):

```python
import base64
from passlib.hash import bcrypt as bcrypt_ctx

def _verify_basic_auth(auth_header: str) -> bool:
    try:
        _, credentials = auth_header.split(" ", 1)
        username, password = base64.b64decode(credentials).decode().split(":", 1)
        stored = AUTH_USERS.get(username)
        return bool(stored and bcrypt_ctx.verify(password, stored))
    except Exception:
        return False
```

### `settings.py`

Parse `AUTH_USERS` at startup into a `dict[str, str]`:

```python
AUTH_USERS: dict[str, str] = {
    u.strip(): h.strip()
    for line in os.environ.get("AUTH_USERS", "").splitlines()
    if ":" in (line := line.strip())
    for u, h in [line.split(":", 1)]
}
```

### Client side (`web/index.html`)

The login form has username and password fields. On submit the UI encodes
`username:password` as base64 and stores the full `Authorization: Basic <value>`
header in `localStorage`. All subsequent `fetch()` calls include it.

Flow:
1. On load, attempt `GET /v1/models` with any stored credential.
2. If `401` (or nothing stored), show the login form.
3. On submit, encode credentials, store in `localStorage`, retry.
4. On success, show the chat UI.
5. On any subsequent `401`, clear `localStorage` and re-show the form.

### Credential management

Passwords must be hashed before adding to `AUTH_USERS`. Helper command:

```bash
python -c "from passlib.hash import bcrypt; print(bcrypt.hash('yourpassword'))"
```

Set in `.env` (one `username:hash` per line):

```
AUTH_USERS=alice:$2b$12$...
  bob:$2b$12$...
```

To revoke a user: remove their line from `AUTH_USERS` and run
`docker compose up -d api`. The API key is unaffected.

### New dependency

Add `passlib[bcrypt]` to both `pyproject.toml` and the `Dockerfile` pip
install block.

### Security note

TLS is already in place via Caddy (`ai.spoonscloud.duckdns.org`). Credentials
never transit in plaintext over the internet. For local access
(`http://localhost:8000/ui/`), Basic Auth transits in plaintext — acceptable
on loopback.

## Files to Touch

| File | Change |
|------|--------|
| `web/index.html` | New — username/password login form and chat UI (HTML + embedded CSS + JS) |
| `web/api_server.py` | Mount `StaticFiles` at `/ui`; extend `security_middleware` to accept Bearer or Basic; add `_verify_basic_auth` helper |
| `settings.py` | Parse `AUTH_USERS` env var into `dict[str, str]` at startup |
| `pyproject.toml` | Add `passlib[bcrypt]` dependency |
| `Dockerfile` | Add `passlib[bcrypt]` to pip install block |
| `.env.example` | Add `AUTH_USERS` example with placeholder hash and instructions |
| `docker-compose.yml` | Pass `AUTH_USERS` env var through to the `api` service |

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
5. No new containers, no build step, no new dependencies beyond `marked.js` (client) and `passlib[bcrypt]` (server)
6. Machine clients using `Authorization: Bearer <API_KEY>` continue to work unchanged
7. Web UI users can log in with username and password; invalid credentials re-prompt
8. Revoking one user (removing from `AUTH_USERS`) does not affect the API key or other users
9. With both `API_KEY` and `AUTH_USERS` unset: server runs open (local dev preserved)
10. `/ui/` paths load without credentials so the login form is reachable in the browser
