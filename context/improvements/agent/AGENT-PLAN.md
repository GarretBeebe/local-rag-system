# RAG-Based Chat Agent — Implementation Plan

## Context

The existing rag-system is a self-hosted, local-first RAG pipeline (Ollama → Qdrant → FastAPI → vanilla JS UI) with no agentic capability. This plan extends it into a tool-calling agent: the same chat interface gains an **Agent mode** that lets the LLM invoke tools (file system browsing, Nextcloud WebDAV, CalDAV calendar, IMAP/SMTP email, contacts, and RAG search) in an iterative loop before returning a final response to the user.

The goal is to reuse as much of the existing infrastructure as possible — same Ollama backend, same auth system, same web UI — and add the minimum new surface area to enable real tool use.

---

## Architecture Overview

```
User (browser) → /v1/agent/chat
                    ↓
            agent/loop.py
            [system prompt (with current time) + tool defs + history (SQLite, per user)]
                    ↓
        Ollama /api/chat  (llama3.1/3.2)
                    ↓
         tool_calls present?
          ┌──── Yes ────┐
          ↓             └→ SSE {type:tool_call} → execute tool
       stream                    ↓
       final text        write tool? → SSE {type:confirmation_required} → stop turn
          ↓                          user clicks Confirm in UI
      SSE {type:done}               POST /v1/agent/confirm → verify user → execute side effect
        → browser
```

**Policy:** The LLM plans and drafts; the backend enforces. Write tools never execute inside the loop — they queue a pending action and emit a `confirmation_required` event. Execution only happens when the user explicitly confirms via a separate endpoint, which verifies the authenticated user owns the pending action.

All new code runs inside the existing `rag-api` Docker container. No new services required.

---

## Ollama API Switch

**Current:** `ollama_client.py` calls `/api/generate` — raw text completion, no tool calling.

**Required:** Switch to `/api/chat` which supports the OpenAI tool-calling format (llama3.1 natively supports this via Ollama).

### Change to `api/ollama_client.py`

The existing client uses module-level `_session` and `OLLAMA_BASE_URL`. Add two module-level functions alongside the existing `generate()`/`stream_generate()`:

```python
def chat(model: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
    payload = {"model": model, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
    resp = _session.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()

def stream_chat(model: str, messages: list[dict]) -> Iterator[str]:
    payload = {"model": model, "messages": messages, "stream": True}
    with _session.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line:
                chunk = json.loads(line)
                if content := chunk.get("message", {}).get("content"):
                    yield content
```

The existing `generate()`/`stream_generate()` functions are **not changed** — the non-agent RAG path stays on `/api/generate`.

---

## New File Structure

```
agent/
├── __init__.py
├── loop.py             # Agentic loop: call Ollama, dispatch tools, iterate
├── memory.py           # Session conversation history (SQLite-backed, per user)
├── confirmations.py    # In-memory pending action store for write confirmation flow
├── registry.py         # Tool schema generation, dispatch table, capability-tier routing
├── system_prompt.py    # System prompt builder (injects current time + timezone)
└── tools/
    ├── __init__.py
    ├── filesystem.py   # list_directory, read_file (sandboxed to configured roots)
    ├── nextcloud.py    # WebDAV: list_files, read_document, write_document
    ├── calendar.py     # CalDAV: list_events, search_calendar, create_event, find_free_time
    ├── contacts.py     # CardDAV: list_contacts, resolve_contact
    ├── email_tool.py   # IMAP: list_emails, search_email, read_email | SMTP: draft_email
    ├── system.py       # get_current_time
    └── rag_search.py   # Wraps existing retrieval.retrieve_best() pipeline
```

---

## SSE Event Envelope

All SSE events use a consistent typed envelope. The UI parser handles one format, not a mix of free-form strings and structured objects.

```
data: {"type": "text",                 "content": "Here is what I found..."}
data: {"type": "tool_call",            "name": "list_events", "status": "running"}
data: {"type": "tool_result",          "name": "list_events", "status": "ok"}
data: {"type": "tool_result",          "name": "list_events", "status": "error", "message": "..."}
data: {"type": "confirmation_required","action": "send_email", "confirmation_id": "...", "preview": "To: ..."}
data: {"type": "error",               "message": "Tool timed out"}
data: {"type": "done"}
```

Text chunks are wrapped in `{"type":"text","content":"..."}` rather than being raw strings, so the parser never needs to guess event type from shape. The existing non-agent `/v1/chat/completions` endpoint is not changed.

---

## Tool Specifications

All read tools enforce explicit output size limits. Oversized results are truncated before being returned to the model to prevent context window overflow on the subsequent Ollama call.

### `system.py`

| Tool | Args | Returns | Limit |
|------|------|---------|-------|
| `get_current_time` | _(none)_ | Current date, time, day of week, UTC offset in user's timezone | — |

Always available — included in every request regardless of capability tier.

### `filesystem.py`

**Sandboxing:** Both tools use `Path.resolve()` + `Path.is_relative_to()` to prevent prefix-confusion attacks (e.g. `/data/nextcloud2` falsely matching `/data/nextcloud`).

```python
from pathlib import Path

ROOTS = [Path(r.strip()).resolve()
         for r in os.getenv("AGENT_FILESYSTEM_ROOTS", "").split(",") if r.strip()]

def _safe_path(path: str) -> Path:
    real = Path(path).resolve()
    if not any(real.is_relative_to(root) for root in ROOTS):
        raise PermissionError(f"Path outside allowed roots: {path}")
    return real
```

| Tool | Args | Returns | Limit |
|------|------|---------|-------|
| `list_directory` | `path: str` | JSON list of entries with name, type (file/dir), size | Max 200 entries |
| `read_file` | `path: str` | File text content (UTF-8) | Max 32KB; truncated with notice |

### `nextcloud.py`

Connects via WebDAV using app-password credentials. XML parsing uses `defusedxml.ElementTree` to prevent XML bomb attacks.

| Tool | Args | Returns | Limit |
|------|------|---------|-------|
| `list_nextcloud_files` | `path: str` (default `/`) | JSON list of files/dirs with name, size, modified | Max 200 entries |
| `read_nextcloud_document` | `path: str` | Document text content | Max 50KB; truncated with notice |
| `write_nextcloud_document` | `path: str, content: str` | Queues confirmation action — does not write immediately | — |

**WebDAV calls:**
- `PROPFIND` with `Depth: 1` → parse `<d:response>` entries via `defusedxml`
- `GET` → document content
- `PUT` → only executed after user confirmation via `/v1/agent/confirm`

**Auth:** HTTP Basic with `NEXTCLOUD_USER` + `NEXTCLOUD_PASSWORD` (app token, not user password).

### `calendar.py`

Connects to Nextcloud CalDAV at `{NEXTCLOUD_URL}/remote.php/dav/calendars/{user}/`.

All date/time args are ISO 8601 with timezone. The system prompt injects the current time and user timezone so the model can resolve relative references like "Friday at 2pm" before calling these tools.

| Tool | Args | Returns | Limit |
|------|------|---------|-------|
| `list_events` | `start: str` (ISO datetime), `end: str` (ISO datetime) | JSON list: title, start, end, location, description | Max 50 events |
| `search_calendar` | `query: str`, `start: str` (optional), `end: str` (optional) | Events matching text query, optionally within a date range | Max 20 results |
| `find_free_time` | `date: str` (ISO date) | Free slots on that day based on existing events | Max 10 slots |
| `create_event` | `title: str, start: str, end: str, description: str` (optional) | Queues confirmation action; checks for conflicts before queuing | — |

**CalDAV calls:**
- `REPORT` with `calendar-query` filter → parse VCALENDAR/VEVENT entries via `defusedxml`
- `PUT` → only executed after user confirmation via `/v1/agent/confirm`

**Conflict check:** Before queuing a `create_event` confirmation, `list_events` is called for the proposed time window. If a conflict exists, the model is informed rather than queuing the action.

### `contacts.py`

Connects to Nextcloud CardDAV at `{NEXTCLOUD_URL}/remote.php/dav/addressbooks/{user}/`. Useful for resolving names to email addresses before drafting email.

| Tool | Args | Returns | Limit |
|------|------|---------|-------|
| `list_contacts` | _(none)_ | JSON list: display name, email(s), phone(s) | Max 200 contacts |
| `resolve_contact` | `name: str` | Best-match contact with all available fields | Single result |

**CardDAV calls:**
- `PROPFIND` with `Depth: 1` + `REPORT` with `addressbook-query` → parse VCARD entries via `defusedxml`

### `email_tool.py`

Uses Python standard library `imaplib` + `smtplib`. No new dependencies.

**Send flow:** The model calls `draft_email`, which queues a pending action scoped to the authenticated user and emits a `confirmation_required` event. The loop stops. The user reviews the draft in the UI and clicks Confirm, which calls `/v1/agent/confirm`. The endpoint verifies the `user_id` from the JWT matches the pending action's owner before executing. Only then does SMTP send occur.

| Tool | Args | Returns | Limit |
|------|------|---------|-------|
| `list_emails` | `folder: str` (default `INBOX`), `limit: int` (default 10, max 50) | JSON list: subject, from, date, message-id | Max 50 results |
| `search_email` | `query: str`, `folder: str` (default `INBOX`) | Matching emails: subject, from, date, message-id only (no bodies) | Max 20 results |
| `read_email` | `message_id: str` | Email headers + plaintext body | Max 50KB body; truncated with notice |
| `draft_email` | `to: str, subject: str, body: str` | Queues send action for confirmation; returns draft preview | — |

**Config env vars:**
- `IMAP_HOST`, `IMAP_PORT` (default 993), `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_SSL` (default true)
- `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`

### `rag_search.py`

Wraps the existing retrieval pipeline. No new logic — just calls `retrieve_best()` from `api/retrieval.py`. Output is bounded by the existing top-k setting in the retrieval config.

| Tool | Args | Returns | Limit |
|------|------|---------|-------|
| `search_rag_documents` | `query: str` | Top-k chunks with source citations | Bounded by retrieval top-k config |

---

## `agent/system_prompt.py` — System Prompt

The system prompt is built dynamically at request time so it always contains the current date/time and user timezone. Uses the repo's module-level import style (`from settings import ...`).

```python
import datetime, zoneinfo
from settings import USER_TIMEZONE

_TEMPLATE = """You are a personal assistant for Garret. Today is {datetime_str} ({timezone}).

Your tools (only use tools that are relevant to the request):
- get_current_time: Current time and date
- search_rag_documents: Search Garret's personal knowledge base
- list_directory / read_file: Browse files on the local server (read-only)
- list_nextcloud_files / read_nextcloud_document: Read Nextcloud files
- write_nextcloud_document: Write a Nextcloud file (requires user confirmation)
- list_events / search_calendar / find_free_time: Read calendar
- create_event: Add a calendar event (requires user confirmation)
- list_contacts / resolve_contact: Look up contacts
- list_emails / search_email / read_email: Read email
- draft_email: Draft an email for user review (requires user confirmation before sending)

Rules:
- Always resolve relative dates ("Friday", "tomorrow") to absolute ISO datetimes using the current date above before calling calendar tools.
- Check for conflicts before proposing a create_event action.
- Use resolve_contact to look up an email address before drafting email to someone by name.
- Use search_rag_documents when the user asks about something that might be in their notes or documents.
- If a query is ambiguous, ask a clarifying question rather than guessing at tool use.
- Write actions (write_nextcloud_document, create_event, draft_email) require user confirmation — you will be notified when confirmation is pending.
"""

def build() -> str:
    tz = zoneinfo.ZoneInfo(USER_TIMEZONE)
    now = datetime.datetime.now(tz)
    return _TEMPLATE.format(
        datetime_str=now.strftime("%A, %B %-d %Y at %-I:%M %p"),
        timezone=USER_TIMEZONE,
    )
```

---

## `agent/memory.py` — Session Conversation History

Session history uses two tables in the existing `users.sqlite3` database: one for session metadata and one for per-turn messages. This avoids repeating session-level fields on every message row and makes session listing queries straightforward.

Sessions are scoped to `user_id` from the JWT — a user cannot read or write another user's session.

```sql
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id  TEXT      NOT NULL PRIMARY KEY,
    user_id     TEXT      NOT NULL,
    title       TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_sessions_user
    ON agent_sessions (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_messages (
    session_id  TEXT      NOT NULL,
    turn_index  INTEGER   NOT NULL,
    role        TEXT      NOT NULL,   -- 'user' | 'assistant' | 'tool'
    content     TEXT      NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, turn_index),
    FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id)
);
```

```python
def load_history(session_id: str, user_id: str, max_turns: int = 20) -> list[dict]:
    """Return the last max_turns messages for this session, verifying user ownership."""
    ...

def append_turn(session_id: str, user_id: str, role: str, content: str) -> None:
    """Append a single message, creating the session row if it doesn't exist.
    Rejects writes to sessions owned by a different user."""
    ...
```

**Session ID:** The web UI generates a UUID on page load and passes it as `session_id` in the request body. `user_id` is extracted from the JWT server-side — the client never provides it.

---

## `agent/confirmations.py` — Pending Action Store

Write tools never execute inside the agent loop. Instead they register a pending action here, scoped to the authenticated user. The loop emits a `confirmation_required` SSE event and stops the current turn. Execution only happens when the user confirms via `/v1/agent/confirm`, which verifies `user_id` before popping the action.

**Restart loss:** This store is in-memory. A Docker or API restart will silently invalidate all pending actions; the confirmation buttons in open browser tabs will return "action expired." This is acceptable for v1 — stale pending actions from before a restart should not execute. If persistence is needed, move to a SQLite table with TTL cleanup in Phase 4.

```python
import threading, uuid, time

_pending: dict[str, dict] = {}  # confirmation_id → entry
_lock = threading.Lock()
TTL_SECONDS = 300  # pending actions expire after 5 minutes

def store(user_id: str, session_id: str, action_type: str, action: dict) -> str:
    """Store a pending action scoped to a user and return a confirmation_id."""
    cid = str(uuid.uuid4())
    with _lock:
        _pending[cid] = {
            "user_id": user_id,
            "session_id": session_id,
            "action_type": action_type,
            "action": action,
            "expires": time.time() + TTL_SECONDS,
        }
    return cid

def pop(confirmation_id: str, user_id: str) -> dict | None:
    """Retrieve and remove a pending action.
    Returns None if expired, not found, or owned by a different user."""
    with _lock:
        entry = _pending.pop(confirmation_id, None)
    if entry and entry["expires"] > time.time() and entry["user_id"] == user_id:
        return entry["action"]
    return None
```

Write tool functions return `{"confirmation_id": cid, "preview": "..."}` rather than executing. The loop detects this shape and emits the appropriate SSE event.

---

## `agent/registry.py` — Tool Registry and Capability-Tier Routing

Tools are organized into capability tiers. The loop selects which tiers to include based on what is configured, rather than guessing intent from query keywords (keyword matching fails on mixed queries like "find the proposal and email Sam a summary").

```python
from settings import AGENT_ALLOW_WRITES

# Tier 0: always included
TIER_ALWAYS = {"get_current_time", "search_rag_documents"}

# Tier 1: safe reads — included when the relevant service is configured
TIER_READS = {
    "list_directory", "read_file",
    "list_nextcloud_files", "read_nextcloud_document",
    "list_events", "search_calendar", "find_free_time",
    "list_contacts", "resolve_contact",
    "list_emails", "search_email", "read_email",
}

# Tier 2: write/side-effect — included only when AGENT_ALLOW_WRITES=true
# These tools do NOT execute directly; they queue a confirmation action.
TIER_WRITES = {
    "write_nextcloud_document",
    "create_event",
    "draft_email",
}

def select_tools() -> list[dict]:
    """Return tool schemas for the appropriate capability tiers."""
    names = set(TIER_ALWAYS)
    names |= {t for t in TIER_READS if _service_configured(t)}
    if AGENT_ALLOW_WRITES:
        names |= TIER_WRITES
    return [_SCHEMAS[n] for n in names if n in _SCHEMAS]

def _service_configured(tool_name: str) -> bool:
    """Return False if the tool's backing service has no credentials configured."""
    ...

def dispatch(name: str, args: dict) -> any: ...
```

Each tool function carries a `__tool_schema__` attribute (a dict matching Ollama's tool format) set at definition time.

---

## `agent/loop.py` — The Agentic Loop

```python
import json
from typing import Iterator
from agent import memory, registry, system_prompt, confirmations
import ollama_client

MAX_ITERATIONS = 5  # personal assistant tasks rarely need more than 3-4 tool calls
from settings import AGENT_MODEL

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"

def run_agent(messages: list[dict], session_id: str, user_id: str) -> Iterator[str]:
    tools = registry.select_tools()
    history = [{"role": "system", "content": system_prompt.build()}]
    history += memory.load_history(session_id, user_id)
    history += messages

    # Persist the incoming user turn before the loop so it's recorded even if the loop errors
    if messages and messages[-1].get("role") == "user":
        memory.append_turn(session_id, user_id, "user", messages[-1].get("content", ""))

    for _ in range(MAX_ITERATIONS):
        response = ollama_client.chat(AGENT_MODEL, history, tools=tools)
        msg = response["message"]

        if not msg.get("tool_calls"):
            # Final response — yield content directly (do NOT call stream_chat again;
            # msg already contains the completed response from the chat() call above)
            content = msg.get("content", "")
            memory.append_turn(session_id, user_id, "assistant", content)
            yield _sse({"type": "text", "content": content})
            yield _sse({"type": "done"})
            return

        history.append(msg)
        for tc in msg["tool_calls"]:
            fn_name = tc["function"]["name"]

            # Guard against malformed model output before dispatch can catch anything
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError) as exc:
                err_content = json.dumps({"error": f"malformed arguments: {exc}"})
                history.append({"role": "tool", "name": fn_name, "content": err_content})
                memory.append_turn(session_id, user_id, "tool", err_content)
                continue

            yield _sse({"type": "tool_call", "name": fn_name, "status": "running"})
            result = registry.dispatch(fn_name, fn_args)  # catches exceptions, 30s timeout

            # Write tools return a confirmation dict instead of executing
            if isinstance(result, dict) and "confirmation_id" in result:
                yield _sse({
                    "type": "confirmation_required",
                    "action": fn_name,
                    "confirmation_id": result["confirmation_id"],
                    "preview": result.get("preview", ""),
                })
                yield _sse({"type": "done"})
                return  # stop the turn; resume after user confirms

            # Report accurate status — dispatch may return {"error": ...}
            is_error = isinstance(result, dict) and "error" in result
            yield _sse({"type": "tool_result", "name": fn_name,
                        "status": "error" if is_error else "ok"})

            tool_content = json.dumps(result)
            memory.append_turn(session_id, user_id, "tool", tool_content)
            history.append({"role": "tool", "name": fn_name, "content": tool_content})

    yield _sse({"type": "error", "message": "Agent reached max iterations without completing task"})
    yield _sse({"type": "done"})
```

**Critical:** When no tool calls are present, yield `msg["content"]` directly — do **not** pass `history + [msg]` to `stream_chat()`. That would regenerate the response a second time, doubling latency on every non-tool query.

**Error handling:** `registry.dispatch()` catches all exceptions and returns `{"error": str(e)}`. The loop checks this before emitting `tool_result` so the UI and model both see accurate state.

**Timeout:** Each tool call in `registry.dispatch()` runs with `concurrent.futures.ThreadPoolExecutor` at a 30-second timeout.

---

## API Changes — `web/api_server.py`

### `/v1/agent/chat`

```python
@app.post("/v1/agent/chat")
async def agent_chat(request: Request, body: AgentChatRequest, ...):
    # reuse existing auth middleware; extract user_id from JWT server-side
    # return StreamingResponse wrapping agent/loop.run_agent()
```

`AgentChatRequest` extends `ChatRequest` with a `session_id: str` field. `user_id` is never accepted from the client — always derived from the JWT.

### `/v1/agent/confirm`

```python
@app.post("/v1/agent/confirm")
async def agent_confirm(body: ConfirmRequest, user_id: str = Depends(get_current_user)):
    # body: {confirmation_id: str, confirmed: bool}
    # pop(body.confirmation_id, user_id) — returns None if not found, expired, or wrong user
    # if confirmed and action found: execute the queued side effect, return result
    # if not confirmed, or action not found: discard and return status
```

`user_id` comes from the JWT via the existing auth dependency, same as all other protected endpoints. `confirmations.pop()` enforces ownership — a confirmation\_id from another user's session silently returns `None`.

**CORS:** Both endpoints inherit the same CORS middleware as `/v1/chat/completions`, including `User-Agent` in `allow_headers` (required by the iOS Chatbox client).

---

## UI Changes — `web/index.html`

**Mode selector:** The existing RAG mode `<select>` adds a third option:

```html
<option value="agent">Agent</option>
```

When "Agent" is selected:
- The `fetch()` call targets `/v1/agent/chat` instead of `/v1/chat/completions`
- A `session_id` UUID is generated on page load and included in each request body
- A note "Agent mode: responses may take longer" appears when the mode is selected

**SSE parser** handles all typed events from the envelope:

| Event type | UI behavior |
|---|---|
| `text` | Append `content` to the message bubble (markdown-rendered) |
| `tool_call` | Show "⚙ running `name`…" status line |
| `tool_result` status `ok` | Update status line to "✓ `name`" |
| `tool_result` status `error` | Update status line to "✗ `name`: `message`" in red |
| `confirmation_required` | Render preview + **Confirm** / **Cancel** buttons; disable input |
| `error` | Show error message in red |
| `done` | Hide status line; re-enable input |

When the user clicks **Confirm** or **Cancel**, the UI posts to `/v1/agent/confirm` with the `confirmation_id` and `confirmed` flag, then re-enables input. No new streaming session is started.

---

## Configuration

### New env vars (add to `docker-compose.yml` and `.env`)

```env
# Agent model (can differ from the RAG model — agent benefits from a smarter model)
AGENT_MODEL=llama3.1:8b

# User timezone (IANA tz name) — injected into system prompt for date resolution
USER_TIMEZONE=America/Chicago

# Filesystem tool
AGENT_FILESYSTEM_ROOTS=/data/nextcloud,/home/garret  # comma-separated host paths

# Write gate — set to true to expose write_nextcloud_document, create_event, draft_email
AGENT_ALLOW_WRITES=false

# Nextcloud (WebDAV + CalDAV + CardDAV)
NEXTCLOUD_URL=http://192.168.68.69
NEXTCLOUD_USER=garret
NEXTCLOUD_PASSWORD=<app-token>   # generate in Nextcloud → Security → App passwords

# Email (IMAP)
IMAP_HOST=
IMAP_PORT=993
IMAP_USER=
IMAP_PASSWORD=
IMAP_SSL=true

# Email (SMTP)
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
```

### `settings.py` additions

```python
AGENT_MODEL = os.getenv("AGENT_MODEL", "llama3.1:8b")
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "UTC")
AGENT_ALLOW_WRITES = os.getenv("AGENT_ALLOW_WRITES", "false").lower() == "true"
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL", "")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER", "")
NEXTCLOUD_PASSWORD = os.getenv("NEXTCLOUD_PASSWORD", "")
AGENT_FILESYSTEM_ROOTS = [
    r.strip() for r in os.getenv("AGENT_FILESYSTEM_ROOTS", "").split(",") if r.strip()
]
# IMAP/SMTP vars similarly
```

### `docker-compose.yml` volume addition

```yaml
services:
  api:
    volumes:
      - ${NEXTCLOUD_PATH}:/data/nextcloud:ro   # already present
      - ${CODE_PATH}:/data/code:ro             # already present
      # add additional roots here as needed
```

---

## Implementation Phases

### Phase 1 — Core agent loop (read-only)
1. Add `chat()` / `stream_chat()` to `api/ollama_client.py`
2. Implement `agent/memory.py` (split `agent_sessions` + `agent_messages` schema, `user_id`-scoped, `load_history`, `append_turn`)
3. Implement `agent/system_prompt.py` (dynamic datetime injection, `from settings import USER_TIMEZONE`)
4. Implement `agent/confirmations.py` (in-memory store scoped to `user_id` with 5-minute TTL)
5. Implement `agent/registry.py` (capability-tier routing, dispatch)
6. Implement `agent/loop.py` (persist user turn before loop; typed SSE envelope; malformed-args guard; error-status tool results; direct-yield final response)
7. Implement `agent/tools/system.py` (`get_current_time`)
8. Implement `agent/tools/filesystem.py` (sandboxed list + read using `Path.is_relative_to()`; 200-entry / 32KB limits)
9. Implement `agent/tools/rag_search.py` (wraps existing retrieval)
10. Add `/v1/agent/chat` + `/v1/agent/confirm` (with `user_id` verification) to `web/api_server.py`
11. Add "Agent" mode to UI: `session_id`, typed SSE parser, confirmation button pair, tool error display

**Validation:** Multi-turn conversation with memory. User turns appear in history on subsequent requests. Tool status events visible in UI. Tool errors show red status, not green. `get_current_time` returns correct local time. Filesystem read works; paths outside roots are rejected including symlink traversal. Confirmation from a different session returns "not found."

### Phase 2 — Nextcloud + Calendar + Contacts + Audit Log
12. Add `defusedxml` dependency
13. Implement `agent/tools/nextcloud.py` (PROPFIND GET with 200-entry / 50KB limits; PUT gated by confirmation)
14. Implement `agent/tools/calendar.py` (CalDAV REPORT with 50-event / 20-result limits; conflict check; PUT gated by confirmation)
15. Implement `agent/tools/contacts.py` (CardDAV PROPFIND/REPORT; 200-contact limit)
16. Add minimal audit log to `users.sqlite3`:

```sql
CREATE TABLE IF NOT EXISTS agent_tool_audit (
    id           INTEGER   PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT      NOT NULL,
    user_id      TEXT      NOT NULL,
    tool_name    TEXT      NOT NULL,
    args_json    TEXT,
    success      INTEGER   NOT NULL,  -- 1 = ok, 0 = error
    duration_ms  INTEGER,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

17. Wire audit writes into `registry.dispatch()` — record every tool invocation before Phase 3 adds personal data access
18. Add `USER_TIMEZONE` + Nextcloud env vars to compose

**Validation:** "List my Nextcloud files", "what's on my calendar this week", "who is Sam's email address". Date resolution ("Friday at 2pm") produces correct absolute ISO datetime. Conflict check fires when creating overlapping events. Write attempts show confirmation UI before executing. Tool calls appear in audit table.

### Phase 3 — Email
19. Implement `agent/tools/email_tool.py` (IMAP list/search/read with limits; SMTP send gated by confirmation)
20. Add IMAP/SMTP env vars to compose

**Validation:** "Show me my last 5 emails", "search for emails about the project", "draft a reply to X" — verify draft preview appears, send only fires after Confirm click, Cancel discards without sending. `search_email` returns headers only, not full bodies.

### Phase 4 — Polish
- Persist confirmation store to SQLite with TTL cleanup (eliminates restart-loss risk)
- System prompt and routing tuning based on observed query patterns
- Audit log UI ("what did the agent do last session?")

---

## Files Modified

| File | Change |
|------|--------|
| `api/ollama_client.py` | Add module-level `chat()` and `stream_chat()` using `/api/chat` |
| `web/api_server.py` | Add `/v1/agent/chat` and `/v1/agent/confirm` (with `user_id` ownership check) |
| `web/index.html` | Agent mode selector, `session_id`, typed SSE parser, tool error display, confirmation UI |
| `settings.py` | Add new env vars (`AGENT_MODEL`, `USER_TIMEZONE`, `AGENT_ALLOW_WRITES`, etc.) |
| `docker-compose.yml` | Add new env vars |
| `pyproject.toml` | Add `defusedxml` |

## Files Created

| File | Purpose |
|------|---------|
| `agent/__init__.py` | Package marker |
| `agent/loop.py` | Tool-calling agentic loop with typed SSE envelope |
| `agent/memory.py` | Split-table SQLite session history, scoped to `user_id` |
| `agent/confirmations.py` | In-memory pending write action store, scoped to `user_id`, with TTL |
| `agent/registry.py` | Capability-tier tool routing, dispatch table, schema generation |
| `agent/system_prompt.py` | Dynamic system prompt builder (injects current datetime + timezone) |
| `agent/tools/__init__.py` | Package marker |
| `agent/tools/system.py` | `get_current_time` |
| `agent/tools/filesystem.py` | Sandboxed filesystem access (`Path.is_relative_to()`); output limits |
| `agent/tools/nextcloud.py` | WebDAV client (defusedxml); output limits; writes queue confirmation |
| `agent/tools/calendar.py` | CalDAV client with conflict check; output limits; writes queue confirmation |
| `agent/tools/contacts.py` | CardDAV client (list + resolve); output limits |
| `agent/tools/email_tool.py` | IMAP list/search/read with output limits; SMTP send queues confirmation |
| `agent/tools/rag_search.py` | Wrapper around existing retrieval pipeline |

---

## Key Risks

1. **llama3.1 tool calling quality**: Smaller Ollama models sometimes miss tool calls or produce malformed JSON in arguments. Malformed args are caught before `dispatch()` and fed back to the model as a tool error. Capability-tier routing (sending a relevant subset of schemas) reduces miss rate. If still unreliable, fallback to qwen2.5.

2. **Write tool blast radius**: All write tools (`write_nextcloud_document`, `create_event`, `draft_email` / SMTP send) are gated behind both `AGENT_ALLOW_WRITES=true` AND explicit user confirmation via `/v1/agent/confirm`. The endpoint verifies `user_id` from the JWT matches the pending action's owner. The model cannot trigger or confirm a write — those are two separate human-gated steps.

3. **Confirmation store restart loss**: Pending actions are in-memory and are lost on API restart. A user with a confirmation prompt open in the browser will get "action expired" after restart. This is acceptable for v1 — stale pending actions from before a restart should not execute. Phase 4 upgrades this to SQLite with TTL cleanup if it becomes a pain point.

4. **Filesystem sandboxing**: `Path.is_relative_to()` correctly rejects `/data/nextcloud2` as not relative to `/data/nextcloud`, and `Path.resolve()` eliminates symlinks before the check. Test with `../../etc/passwd` and symlink targets outside roots.

5. **Output size / context overflow**: All read tools enforce explicit result size limits. A tool returning a 500-entry directory listing or a 1MB document would overflow the context for the next Ollama call. Truncation with a notice string ("... truncated at 200 entries") keeps the model aware of the limit.

6. **Session ownership**: `load_history` and `append_turn` both take `user_id` from the JWT. A `session_id` belonging to another user returns empty history silently (to avoid leaking session existence). `confirmations.pop()` applies the same ownership check.

7. **Agent loop latency**: Multiple Ollama round-trips before the final answer means higher latency than the RAG path. Tool status SSE events mitigate the UX impact. `AGENT_MODEL` can point to a smaller/faster model independently of the RAG model.

8. **Double-call regression**: The final `yield` in the loop must yield `msg["content"]` directly — never pass the completed assistant message back to `stream_chat()`. This would silently regenerate the response and double latency on every non-tool query.

9. **iOS Chatbox CORS**: Both `/v1/agent/chat` and `/v1/agent/confirm` must inherit the same CORS middleware as `/v1/chat/completions`, including `User-Agent` in `allow_headers`.

10. **Email credentials in env**: IMAP/SMTP passwords in `.env` — ensure `.env` is `.gitignore`'d (it already is per the existing project).

---

## Dependencies

| Library | Status | Use |
|---------|--------|-----|
| `requests` | Already present | Ollama client, WebDAV, CalDAV, CardDAV |
| `imaplib`, `smtplib`, `email` | Python stdlib | Email |
| `zoneinfo` | Python stdlib (3.9+) | Timezone handling in system prompt |
| `defusedxml` | **New — add to pyproject.toml** | Safe XML parsing for WebDAV/CalDAV/CardDAV PROPFIND responses |
| `concurrent.futures` | Python stdlib | Tool call timeouts |
| `xml.etree.ElementTree` | Python stdlib — **do not use** | Replaced by defusedxml throughout |
