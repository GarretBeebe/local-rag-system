# Stop Button for Long-Running Queries

## Context

The RAG web UI streams responses via SSE. Queries can take 30+ seconds (retrieval + LLM
generation). There is currently no way to cancel a running query: no `AbortController` on the
frontend, no cancellation signal on the backend. Clicking away or refreshing leaves the background
thread generating tokens against the GPU for the full duration of the query.

## Goals

- Add a Stop button that appears while a query is in-flight.
- Client-side abort is immediate (no waiting for server acknowledgement).
- Server detects the disconnect and stops GPU work promptly.
- Partial response stays visible in the chat with a `[stopped]` indicator.

## Non-Goals

- Cancelling the retrieval phase (it's uninterruptible synchronous work; we just skip entering
  the Ollama loop if cancelled before it starts).
- WebSocket or other transport changes.
- New dependencies.

## Architecture

The cancellation chain flows through four layers:

```
User clicks Stop
  → AbortController.abort()
    → fetch throws AbortError (client, immediate)
      → server detects disconnect via is_disconnected() polling (~500ms lag)
        → threading.Event.set()
          → background thread checks event between Ollama tokens
            → breaks out of stream loop, thread exits
```

## Files Changed

| File | Change |
|------|--------|
| `web/index.html` | Stop button HTML/CSS, AbortController, catch AbortError |
| `web/api_server.py` | Thread `Request` into generator, disconnect watcher task, `threading.Event` |
| `api/query_rag.py` | Accept and check `cancel` event in `ask_stream_sync` |
| `api/ollama_client.py` | Accept and check `cancel` event in `stream_generate` |

---

## Implementation Details

### `web/index.html`

**CSS** — add a stop button variant alongside `button.primary`:
```css
button.stop { background: #7a2020; color: #fff; border: none; border-radius: 8px;
  padding: 0 1rem; height: 42px; font-size: 0.95rem; cursor: pointer; flex-shrink: 0; }
button.stop:hover { background: #962828; }
```

**HTML** — add stop button inside `.input-bar` (currently line 261):
```html
<button id="send-btn" class="primary">Send</button>
<button id="stop-btn" class="stop" style="display:none">Stop</button>
```

**JS variable declarations** — after existing `const sendBtn`:
```js
const stopBtn = $('stop-btn');
let _abortCtl = null;
```

**`sendMessage()`** — at the start, swap buttons and create controller:
```js
sendBtn.style.display = 'none';
stopBtn.style.display = '';
_abortCtl = new AbortController();
let wasStopped = false;
```

Add `signal: _abortCtl.signal` to the `fetch` options.

Replace the generic `catch` with an `AbortError`-aware handler:
```js
} catch (err) {
  if (err.name === 'AbortError') {
    wasStopped = true;
    thinking.remove();
    if (assistantDiv) {
      const suffix = document.createElement('span');
      suffix.style.cssText = 'color:#888;font-style:italic;font-size:0.82em;margin-left:0.4em;';
      suffix.textContent = '[stopped]';
      assistantDiv.appendChild(suffix);
    } else {
      appendMessage('error', '[stopped]');
    }
  } else {
    thinking.remove();
    appendMessage('error', `Request failed: ${err.message}`);
  }
}
```

Restore buttons in `finally`:
```js
} finally {
  _abortCtl = null;
  sendBtn.disabled = false;
  sendBtn.style.display = '';
  stopBtn.style.display = 'none';
  if (!wasStopped) inputEl.focus();
}
```

Wire the Stop button after the `sendBtn` event listener:
```js
stopBtn.addEventListener('click', () => { if (_abortCtl) _abortCtl.abort(); });
```

---

### `web/api_server.py`

**Import** — add `threading` to existing stdlib imports.

**`_rag_stream_response` signature** — add `http_request: Request | None = None`:
```python
async def _rag_stream_response(
    question: str,
    model: str,
    rag_mode: Literal["strict", "augmented"] = "augmented",
    http_request: Request | None = None,
) -> AsyncIterator[str]:
```

**Inside `_rag_stream_response`** — create cancel event after the queue:
```python
cancel_event = threading.Event()
```

Update `_run()` to pass `cancel_event` to `ask_stream_sync`:
```python
def _run():
    try:
        for text in ask_stream_sync(question, model, rag_mode, cancel_event):
            loop.call_soon_threadsafe(queue.put_nowait, text)
    except Exception as exc:
        loop.call_soon_threadsafe(queue.put_nowait, exc)
    finally:
        loop.call_soon_threadsafe(queue.put_nowait, None)
```

Add disconnect watcher task after `future.add_done_callback`:
```python
async def _watch_disconnect():
    if http_request is None:
        return
    while not cancel_event.is_set():
        await asyncio.sleep(0.5)
        if await http_request.is_disconnected():
            cancel_event.set()
            logger.info("Client disconnected — cancelling stream")
            return

disconnect_task = asyncio.create_task(_watch_disconnect())
```

Add a `finally` block to the existing `try/except asyncio.TimeoutError`:
```python
except asyncio.TimeoutError:
    cancel_event.set()
    logger.warning("RAG stream timed out")
    yield _make_stream_chunk(request_id, created, model, content="\n\n[Error: generation timed out]")
finally:
    cancel_event.set()   # covers GeneratorExit and all other exit paths
    disconnect_task.cancel()
    with suppress(asyncio.CancelledError):
        await disconnect_task
```

**`chat` endpoint** — add `request: Request` and forward it:
```python
@app.post("/v1/chat/completions")
async def chat(request: Request, req: ChatRequest):
    ...
    if req.stream:
        return StreamingResponse(
            _rag_stream_response(question, req.model, rag_mode, request),
            ...
        )
```

**`chat_alias`** — must also accept and forward `request`:
```python
@app.post("/chat/completions")
async def chat_alias(request: Request, req: ChatRequest):
    return await chat(request, req)
```

`Request` is already imported from `fastapi`. `suppress` is already imported from `contextlib`.

---

### `api/query_rag.py`

**Import** — add `import threading`.

**`ask_stream_sync` signature** — add `cancel: threading.Event | None = None`:
```python
def ask_stream_sync(
    question: str,
    model: str,
    rag_mode: Literal["strict", "augmented"] = "augmented",
    cancel: threading.Event | None = None,
) -> Iterator[str]:
```

**Body** — add cancel checks at each boundary and pass event to Ollama:
```python
def ask_stream_sync(...) -> Iterator[str]:
    if cancel and cancel.is_set():
        return

    chunks = retrieve_best(question)

    if cancel and cancel.is_set():
        return

    if not chunks:
        if rag_mode == "augmented":
            yield from ollama_client.stream_generate(question, model, cancel=cancel)
        else:
            yield _NO_CONTEXT_REPLY
        return

    prompt = build_prompt(question, chunks, rag_mode)
    with timed("stream_generate"):
        yield from ollama_client.stream_generate(prompt, model, cancel=cancel)

    if cancel and cancel.is_set():
        return   # don't append sources if cancelled mid-generation

    yield _format_sources(chunks)
```

---

### `api/ollama_client.py`

**Import** — add `import threading`.

**`stream_generate` signature** — add `cancel: threading.Event | None = None`:
```python
def stream_generate(
    prompt: str,
    model: str,
    timeout: float = 120.0,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
```

**Body** — check cancel on each token:
```python
for line in resp.iter_lines(decode_unicode=True):
    if cancel and cancel.is_set():
        break   # exits with-block, closes HTTP connection to Ollama
    if not line:
        continue
    data = json.loads(line)
    if data.get("response"):
        yield data["response"]
    if data.get("done"):
        break
```

---

## Gotchas

**Polling lag (~500ms)**: The disconnect watcher polls every 500ms. Between the client aborting
and the server detecting it, up to ~500ms of extra tokens may be generated. Acceptable.

**`GeneratorExit`**: Starlette throws `GeneratorExit` into the async generator when the client
disconnects. The `finally: cancel_event.set()` handles this path — both the watcher and
`GeneratorExit` converge on the same event, so whichever fires first wins.

**Semaphore release**: The semaphore is released via `future.add_done_callback`, which fires
when the thread exits. An early cancellation causes the thread to exit sooner, freeing the
concurrency slot faster than a full run.

**`chat_alias` must also take `request`**: Starlette only injects `Request` if it appears in the
function signature. Forgetting this on `chat_alias` will cause a 422 or the request object won't
be passed.

**`_abortCtl` race**: Two concurrent sends are blocked by `sendBtn.disabled`, so the
module-level `_abortCtl` variable is safe.

---

## Verification

1. **Normal flow**: Send query → Stop button appears → response streams → Stop disappears, Send
   returns.
2. **Stop during retrieval**: Click Stop within 1s. Fetch aborts instantly (client). Server logs
   "Client disconnected" within ~500ms. No Ollama call made.
3. **Stop mid-stream**: Click Stop after tokens appear. Partial response stays with `[stopped]`
   appended. Server stops within one token of detection.
4. **Natural completion**: `[DONE]` arrives, reader loop exits, Send restored, Stop hidden.
5. **Tab close mid-stream**: Disconnect watcher fires, thread exits cleanly.
