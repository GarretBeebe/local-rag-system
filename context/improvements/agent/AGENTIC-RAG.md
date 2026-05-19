# Agentic RAG — Design Plan

## What This Is (and Isn't)

This document covers converting the RAG retrieval pipeline into an **agentic retrieval loop**:
the LLM decides when to retrieve, what to search for, and whether to retrieve again before
answering. This is not about adding personal-assistant tools (email, calendar, WebDAV) — that
is covered in `AGENT-PLAN.md`. This is exclusively about making retrieval itself agentic.

**Current model:**
```
question → [embed → qdrant + bm25 → mmr → rerank] → build_prompt → generate → answer
```
One retrieval pass, fixed pipeline, LLM has no control over what it gets.

**Agentic model:**
```
question → LLM decides: what should I search for?
             → retrieve("query A") → results fed back to LLM
             → LLM decides: I need more, retrieve again
             → retrieve("query B") → results fed back to LLM
             → LLM decides: I have enough, synthesize
             → final answer with citations from all retrieval rounds
```
The LLM drives retrieval. It can issue multiple calls with different queries, synthesize
across rounds, and explicitly signal when it has enough context.

---

## Feasibility Assessment

### Code: YES

The codebase is modular and clean. The key pieces are already in place:

| What's needed | Current state | Gap |
|---|---|---|
| Retrieval function | `api/retrieval.py:retrieve_best()` — clean callable | None — becomes the tool |
| Tool calling support | `api/ollama_client.py` uses `/api/generate` | Must switch to `/api/chat` |
| Tool schema | Not defined | Must add |
| Agent loop | Not implemented | Must add |
| Streaming in loop | `api_server.py` supports SSE | Must handle non-streamable tool phases |
| `rag_mode` toggle | Already exists (`strict`/`augmented`) | Add `"agentic"` as third mode |

The `retrieve_best()` function signature is already exactly what you'd want as a tool:
```python
# api/retrieval.py
def retrieve_best(question: str, recall_k=15, mmr_k=12, final_k=4) -> list[ScoredChunk]
```
Wrap it, expose the `question` parameter to the LLM, return formatted results — done.

### Hardware: CONDITIONALLY YES

**Setup:** NUCbox running Docker stack (api + qdrant + watcher); Ollama on host; CPU-only
PyTorch in container; qwen2.5:14b as the generation model.

**The concern:** Each agent iteration requires a full LLM round trip. On CPU:
- qwen2.5:14b at 4-bit quant: ~3–8 tok/s depending on prompt length
- Tool call response (short, ~20–50 tokens): ~5–15 seconds
- Final answer (medium, ~200–500 tokens): ~30–100 seconds
- A 3-iteration loop could take **60–150 seconds total**

**This is borderline acceptable for synchronous use.** The 240s timeout already in
`api_server.py` handles it. Streaming the final answer mitigates perceived latency.

**Hard constraints to enforce:**
- Maximum 3 retrieval iterations per query (configurable via `MAX_AGENT_ITERATIONS`)
- Make agentic mode opt-in via `rag_mode: "agentic"` — don't break the fast default path
- Use the same qwen2.5:14b model; don't introduce a separate model dependency

**Model reliability:** qwen2.5:14b has solid tool-calling support via Ollama. The bigger
risk is the model issuing redundant retrieval calls on simple questions. Mitigate with a
well-engineered system prompt that sets clear retrieval expectations.

---

## What Changes

### 1. `api/ollama_client.py` — Add chat endpoint

The current client calls `/api/generate`. Tool calling requires `/api/chat`.

New methods to add:
```python
def chat(messages: list[dict], tools: list[dict] | None = None, ...) -> dict
def stream_chat(messages: list[dict], tools: list[dict] | None = None, ...) -> Iterator[dict]
```

The `/api/chat` request format:
```json
{
  "model": "qwen2.5:14b",
  "messages": [...],
  "tools": [...],
  "stream": false,
  "options": { "num_ctx": 16384 }
}
```

Response includes either `message.content` (text) or `message.tool_calls` (list of calls).
During tool-call phases, streaming must be disabled (the response must be accumulated to
parse tool call JSON). Streaming is re-enabled for the final answer generation.

### 2. `api/tools.py` — Tool definitions (new file)

Define the retrieval tool schema and execution function:

```python
RETRIEVAL_TOOL = {
    "type": "function",
    "function": {
        "name": "retrieve",
        "description": (
            "Search the document knowledge base for information relevant to a query. "
            "Use this when you need to find specific facts, documents, or context. "
            "You can call this multiple times with different queries to gather "
            "information from different angles."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Be specific — use keywords and concepts "
                                   "from what you're looking for, not the user's question verbatim."
                }
            },
            "required": ["query"]
        }
    }
}

def execute_tool(name: str, arguments: dict) -> str:
    """Execute a tool call and return formatted results as a string."""
    if name == "retrieve":
        chunks = retrieve_best(arguments["query"])
        return _format_chunks(chunks)
    raise ValueError(f"Unknown tool: {name}")

def _format_chunks(chunks: list[ScoredChunk]) -> str:
    """Format retrieved chunks for inclusion in the message stream."""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {chunk.filename} (score: {chunk.rerank_score:.3f})\n"
            f"{chunk.text}"
        )
    return "\n\n---\n\n".join(parts) if parts else "No relevant documents found."
```

No `retrieve_from_file` tool for now — `retrieve_best()` already handles filename detection
via regex in the query. Adding a second tool increases prompt complexity for marginal gain.

### 3. `api/agent_loop.py` — Agent loop (new file)

```python
MAX_ITERATIONS = settings.MAX_AGENT_ITERATIONS  # default 3

def run_agent(question: str, model: str, rag_mode: str) -> tuple[str, list[ScoredChunk]]:
    """
    Run the agentic retrieval loop.
    Returns (final_answer, all_retrieved_chunks_for_citations).
    """
    messages = [
        {"role": "system", "content": _build_system_prompt(rag_mode)},
        {"role": "user", "content": question},
    ]
    all_chunks: list[ScoredChunk] = []

    for iteration in range(MAX_ITERATIONS):
        response = ollama_client.chat(messages, tools=TOOLS, stream=False)
        msg = response["message"]

        if not msg.get("tool_calls"):
            # LLM produced a final answer — done
            return msg["content"], all_chunks

        # Execute tool calls
        messages.append({"role": "assistant", "content": "", "tool_calls": msg["tool_calls"]})
        for tool_call in msg["tool_calls"]:
            name = tool_call["function"]["name"]
            args = tool_call["function"]["arguments"]
            result, chunks = execute_tool_with_chunks(name, args)
            all_chunks.extend(chunks)
            messages.append({
                "role": "tool",
                "content": result,
            })

    # Exhausted iterations — ask for final answer without tools
    messages.append({"role": "user", "content": "Please synthesize your findings and answer now."})
    final = ollama_client.chat(messages, tools=None, stream=False)
    return final["message"]["content"], all_chunks


def run_agent_stream(question: str, model: str, rag_mode: str) -> Iterator[str]:
    """
    Streaming variant. Tool-call rounds are non-streaming (blocking),
    only the final answer token-streams.
    """
    messages = [
        {"role": "system", "content": _build_system_prompt(rag_mode)},
        {"role": "user", "content": question},
    ]

    for iteration in range(MAX_ITERATIONS):
        response = ollama_client.chat(messages, tools=TOOLS, stream=False)
        msg = response["message"]

        if not msg.get("tool_calls"):
            # Final answer — switch to streaming
            yield from ollama_client.stream_chat(messages + [msg], tools=None)
            return

        # Execute tools silently (client sees nothing yet)
        messages.append({"role": "assistant", "content": "", "tool_calls": msg["tool_calls"]})
        for tool_call in msg["tool_calls"]:
            name = tool_call["function"]["name"]
            args = tool_call["function"]["arguments"]
            result = execute_tool(name, args)
            messages.append({"role": "tool", "content": result})

    # Force final answer
    messages.append({"role": "user", "content": "Please synthesize your findings and answer now."})
    yield from ollama_client.stream_chat(messages, tools=None)
```

### 4. `api/query_rag.py` — Route agentic mode

The existing `ask()` and `ask_stream_sync()` functions gate on `rag_mode`. Add a branch:

```python
def ask(question: str, model: str, rag_mode: str) -> str:
    if rag_mode == "agentic":
        answer, chunks = run_agent(question, model, rag_mode)
        return _format_with_sources(answer, chunks)
    # ... existing strict/augmented path unchanged
```

The existing `strict`/`augmented` paths are untouched.

### 5. `settings.py` — One new constant

```python
MAX_AGENT_ITERATIONS: int = int(os.environ.get("MAX_AGENT_ITERATIONS", "3"))
```

---

## What Stays the Same

Everything below is **untouched**:

- `api/retrieval.py` — `retrieve_best()` becomes the tool implementation as-is
- `api/embed.py` — embedding unchanged
- `api/keyword_index.py` — BM25 index unchanged
- `ingest/` — ingestion pipeline unchanged
- `indexer/` — filesystem watcher unchanged
- `web/api_server.py` — routes and auth unchanged; streaming path needs ~5 lines added for agentic mode dispatch
- Qdrant — vector store unchanged
- Docker / docker-compose — unchanged

---

## System Prompt Design

The system prompt for agentic mode needs to set clear expectations. Key principles:

1. **Retrieve first, answer second** — do not answer from training knowledge; always retrieve
2. **Targeted queries beat broad ones** — be specific in what you search for
3. **Stop when you have enough** — don't retrieve more than necessary
4. **Cite sources** — reference filenames from the retrieved context

```python
def _build_system_prompt(rag_mode: str) -> str:
    base = (
        "You are a precise research assistant with access to a document knowledge base. "
        "Use the `retrieve` tool to search for relevant information before answering. "
        "You may call `retrieve` multiple times with different queries to gather context "
        "from multiple angles. Stop retrieving once you have sufficient information to answer. "
        "Base your answer on the retrieved documents. Cite the source filenames."
    )
    if rag_mode == "agentic":
        return base  # always retrieves
    return base  # same for now; reserved for future strict variant
```

---

## Citation Tracking Across Iterations

Each retrieval round returns `ScoredChunk` objects. The agent loop accumulates all of them
in `all_chunks`. Before returning the final answer, deduplicate by `(filepath, chunk_index)`
and pass the unique set to `_format_with_sources()`. This means the final citation list
reflects all documents consulted, not just the last retrieval round.

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Model calls retrieve on trivial questions, adding 15–30s overhead | Medium | Medium | Prompt instructs LLM to retrieve; agentic mode is opt-in |
| Model loops redundantly (same query twice) | Low | Low | 3-iteration hard cap; prompt says "stop when you have enough" |
| Total latency exceeds 240s on complex queries | Low | High | Iteration cap limits worst case; 240s timeout already enforced |
| Tool call JSON malformed / parse failure | Low | Medium | Validate tool call schema; fall back to non-agentic on parse error |
| Final answer quality worse than single-pass RAG | Low | High | A/B test before making agentic mode default |
| Context window overflow with 3× retrieved chunks | Medium | Medium | 3 rounds × 4 chunks × ~500 chars = ~6K chars; well within 16K token window |

---

## Implementation Order

Do these incrementally, verifying each step before moving to the next:

1. **Extend `ollama_client.py`** — add `chat()` method using `/api/chat`; test manually
   with a single message, confirm tool call response shape from qwen2.5:14b

2. **Create `api/tools.py`** — define `RETRIEVAL_TOOL` schema + `execute_tool()`; unit-test
   tool execution by calling `retrieve_best()` directly

3. **Create `api/agent_loop.py`** — implement non-streaming loop first; test end-to-end with
   a multi-hop question that requires two retrieval rounds (e.g., "compare X in doc A vs doc B")

4. **Wire into `api/query_rag.py`** — add `rag_mode == "agentic"` branch; verify existing
   `strict`/`augmented` paths are unaffected

5. **Add streaming support** — implement `run_agent_stream()`; verify SSE still works in the UI

6. **Add `settings.py` constant** — `MAX_AGENT_ITERATIONS` with env override

7. **Evaluate quality vs. latency** — run the same 10 test questions in both agentic and
   augmented mode; compare answer quality and wall-clock time on the NUCbox

---

## Open Questions Before Implementation

- **Does qwen2.5:14b reliably issue tool calls on this Ollama version?**
  Test: send a simple message with the `retrieve` tool defined and check if the model
  uses it or ignores it. Some Ollama versions have tool-calling quirks.

- **Should retrieval-exhausted fallback answer without documents?**
  If 3 iterations pass and the LLM still hasn't answered, the fallback forces a synthesis.
  In strict mode this should probably return an error instead.

- **Visible tool-call progress in the UI?**
  The streaming variant hides tool calls from the client. Consider emitting a
  `[Searching: "query"]` status line before tool execution starts, so the user knows
  the system is working. This requires a small SSE protocol extension.
