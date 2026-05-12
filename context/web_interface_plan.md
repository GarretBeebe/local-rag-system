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

**Mechanism: HTTP Basic Auth enforced server-side on all API routes.**

Credentials are stored as bcrypt hashes in an `AUTH_USERS` environment
variable — one `username:hash` pair per line. The server parses them at
startup into an in-memory lookup. If `AUTH_USERS` is not set, the server
runs unauthenticated (local-only use case, existing behavior preserved).

### Server side (`web/api_server.py`)

Add a FastAPI dependency injected into every API route (all routes under
`/v1/*` and `/chat/*`). The dependency:

1. Reads the `Authorization` header.
2. Decodes the base64 `Basic <credentials>` value to extract `username:password`.
3. Looks up the username in the parsed `AUTH_USERS` dict.
4. Verifies the password against the stored bcrypt hash using `passlib`.
5. Raises `HTTPException(401)` with `WWW-Authenticate: Basic realm="RAG"`
   on any failure (missing header, unknown user, wrong password).

The static file mount (`/ui/`) and the health check route (`GET /`) are
**not** covered by this dependency — they remain public. The HTML shell is
harmless without valid API credentials.

### Client side (`web/index.html`)

The UI stores credentials in `sessionStorage` after the user logs in and
includes them as an `Authorization: Basic <base64>` header on every
`fetch()` call. Flow:

1. On load, attempt `GET /v1/models` with any stored credentials.
2. If the response is `401`, show a login form (username + password fields).
3. On submit, encode `username:password` as base64, store in `sessionStorage`,
   retry the request.
4. If the retry succeeds, proceed to the chat UI.
5. If any subsequent API call returns `401`, clear `sessionStorage` and show
   the login form again (handles password changes or session expiry).

The browser's native Basic Auth dialog is deliberately bypassed; the custom
form gives a consistent UI and avoids the browser caching credentials in a
way that's hard to clear.

### Credential management

Passwords must be hashed before adding to `AUTH_USERS`. Provide a helper
command in the README:

```bash
python -c "from passlib.hash import bcrypt; print(bcrypt.hash('yourpassword'))"
```

Then set in `.env`:

```
AUTH_USERS=alice:$2b$12$...
  bob:$2b$12$...
```

Multiline env vars work in `.env` files and are passed correctly by
docker-compose.

### New dependency

Add `passlib[bcrypt]` to `pyproject.toml`. No other new dependencies.

### Security note

Basic Auth over plain HTTP sends credentials base64-encoded (not encrypted)
on every request. This is acceptable on a trusted local network. If the API
is ever exposed beyond localhost, put a TLS-terminating reverse proxy (nginx,
Caddy) in front. Document this clearly in the README.

## Files to Touch

| File | Change |
|------|--------|
| `web/index.html` | New — full UI with login form and chat (HTML + embedded CSS + JS) |
| `web/api_server.py` | Mount `StaticFiles` at `/ui`; auth dependency on all API routes |
| `settings.py` | Parse `AUTH_USERS` env var into a `dict[str, str]` at startup |
| `pyproject.toml` | Add `passlib[bcrypt]` dependency |
| `.env.example` | Add `AUTH_USERS` example with a placeholder hash |
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
- **Plaintext credentials in transit**: Basic Auth is not encrypted over HTTP.
  Credentials are exposed on the network to any observer. Acceptable on
  localhost or a trusted LAN; a blocker if the API is exposed to the internet.
  Mitigation: document the TLS reverse-proxy requirement prominently.
- **`sessionStorage` credential storage**: Credentials stored in
  `sessionStorage` are cleared when the tab closes but are readable by any JS
  on the page. Since there is no third-party JS (only the optional CDN script),
  the attack surface is minimal. Vendoring `marked.js` eliminates it entirely.
- **Timing attacks on password comparison**: `passlib`'s `verify()` uses
  constant-time comparison; this is safe. Do not replace it with a plain
  string comparison.

## Success Criteria

1. Navigating to `http://localhost:8000/ui/` opens the chat page
2. The model dropdown lists whatever models are in Ollama
3. Sending a question returns a streamed answer that renders as markdown
4. Errors (timeout, pipeline failure) display inline in the chat
5. No new containers, no build step, no new dependencies beyond `marked.js` and `passlib[bcrypt]`
6. With `AUTH_USERS` set: unauthenticated requests to `/v1/*` return `401`
7. With `AUTH_USERS` set: the login form appears, valid credentials grant access, invalid credentials re-prompt
8. With `AUTH_USERS` unset: the server behaves exactly as it does today (no auth, no breaking change)
9. A user not in `AUTH_USERS` cannot reach any API endpoint regardless of what credentials they supply
