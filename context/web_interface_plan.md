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

**Mechanism: Bearer token auth using the existing `API_KEY` env var.**

The server already enforces `Authorization: Bearer <API_KEY>` on all
non-health-check endpoints via `security_middleware` in `web/api_server.py`.
No new server-side auth logic is needed.

### Server side (`web/api_server.py`)

One small change only: extend the bypass in `security_middleware` to also
allow unauthenticated access to `/ui/` paths, so the HTML shell loads in
the browser before the user has entered a key:

```python
if (request.url.path == "/" and request.method == "GET") or \
        request.url.path.startswith("/ui/"):
    return await call_next(request)
```

The HTML page itself is harmless without a valid key — all API calls it
makes will be rejected with 401 until the correct key is supplied.

### Client side (`web/index.html`)

The UI stores the API key in `sessionStorage` after the user enters it and
includes it as `Authorization: Bearer <key>` on every `fetch()` call. Flow:

1. On load, attempt `GET /v1/models` with any stored key.
2. If the response is `401` (or no key is stored), show a login form with a
   single API key field.
3. On submit, store the key in `sessionStorage` and retry the request.
4. If the retry succeeds, proceed to the chat UI.
5. If any subsequent API call returns `401`, clear `sessionStorage` and show
   the login form again (handles key rotation).

### Credential management

The API key is the value of `API_KEY` in `.env`. No hashing, no user
accounts. To rotate the key: update `.env` and run `docker compose up -d api`.

### No new dependencies

`passlib[bcrypt]` is **not** needed. Remove it from the plan.

### Security note

TLS is already in place via Caddy (`ai.spoonscloud.duckdns.org`). The API
key is never transmitted in plaintext over the internet. For local network
use (`http://localhost:8000/ui/`), the key transits in plaintext — acceptable
on loopback.

## Files to Touch

| File | Change |
|------|--------|
| `web/index.html` | New — full UI with API key login form and chat (HTML + embedded CSS + JS) |
| `web/api_server.py` | Mount `StaticFiles` at `/ui`; extend `security_middleware` bypass to cover `/ui/` paths |
| `settings.py` | No change — `API_KEY` already parsed |
| `.env.example` | No change — `API_KEY` already documented |
| `docker-compose.yml` | No change — `API_KEY` already passed through |

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
5. No new containers, no build step, no new dependencies beyond `marked.js`
6. With `API_KEY` set: unauthenticated requests to `/v1/*` return `401`
7. With `API_KEY` set: the login form appears, the correct key grants access, wrong key re-prompts
8. With `API_KEY` unset: the server runs without auth (existing local-only behavior preserved)
9. `/ui/` paths load without a key so the login form is reachable in the browser
