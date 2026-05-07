# Response Time Implementation Plan

This plan turns the response-time findings into implementation phases. The
order is intentionally measurement-first: add visibility, make low-risk changes,
then tune retrieval behavior with data.

## Goals

- Improve perceived latency for streaming clients.
- Reduce CPU time spent before generation starts.
- Reduce prompt evaluation time in Ollama.
- Preserve answer quality unless an explicit fast mode is enabled.
- Make performance changes configurable enough to tune per machine.

## Non-Goals

- Replacing Qdrant.
- Replacing Ollama.
- Reworking ingestion architecture.
- Changing the OpenAI-compatible API surface.

## Phase 1: Add Timing Instrumentation

Purpose: establish a baseline before changing behavior.

### Changes

- Add per-stage timing around the full RAG path:
  - query embedding
  - Qdrant vector recall
  - BM25 keyword recall
  - MMR
  - reranking
  - prompt assembly
  - Ollama generation
- Log total request time in `web/api_server.py`.
- Keep logs compact and structured enough for comparison across runs.

### Files

- `api/query_rag.py`
- `api/retrieval.py`
- `api/embed.py`
- `api/keyword_index.py`
- `web/api_server.py`

### Acceptance Criteria

- A single chat request produces timing data for each major stage.
- Timing logs include enough context to compare retrieval-only time versus
  generation time.
- No response payload format changes.

## Phase 2: Add Shared Ollama Client

Purpose: centralize Ollama calls and reuse HTTP connections.

### Changes

- Add `api/ollama_client.py`.
- Create a module-level `requests.Session`.
- Add helpers for:
  - embeddings
  - non-streaming generation
  - streaming generation
- Move timeout and error handling into this client.
- Update `api/embed.py` and `api/query_rag.py` to use the client.

### Files

- `api/ollama_client.py`
- `api/embed.py`
- `api/query_rag.py`

### Acceptance Criteria

- Existing non-streaming calls still work.
- Embedding behavior is unchanged.
- Ollama connection handling lives in one place.

## Phase 3: Implement True Streaming

Purpose: improve time to first token for clients using `stream: true`.

### Changes

- Split RAG into two steps:
  - retrieve context and build prompt
  - generate answer
- Add a streaming generation path that calls Ollama with `stream: true`.
- Update `web/api_server.py` so streaming requests do not wait for the full
  answer before sending chunks.
- Preserve the existing non-streaming response path.
- Keep source citations behavior for non-streaming responses.
- Decide how citations should appear in streaming responses:
  - recommended first version: stream the answer, then append the sources block
    after generation finishes.

### Files

- `api/query_rag.py`
- `web/api_server.py`
- `api/ollama_client.py`

### Acceptance Criteria

- `stream: true` starts returning SSE chunks while Ollama is still generating.
- `stream: false` returns the same OpenAI-compatible response shape as before.
- Streaming responses terminate with `data: [DONE]`.
- Timeout and error behavior remains clear.

## Phase 4: Reduce Reranker Candidate Volume

Purpose: reduce CPU-heavy cross-encoder work without a large quality drop.

### Changes

- Add configurable retrieval parameters in `settings.py`:
  - `RAG_RECALL_K`
  - `RAG_MMR_K`
  - `RAG_FINAL_K`
  - `RAG_RERANK_MAX_CANDIDATES`
- Change `ask()` to use settings instead of hard-coded values.
- Filter BM25 candidates with score `<= 0`.
- Dedupe candidates before reranking.
- Cap candidates passed into `rerank()`.

### Files

- `settings.py`
- `api/query_rag.py`
- `api/retrieval.py`
- `api/keyword_index.py`

### Starting Defaults

- `RAG_RECALL_K=15`
- `RAG_MMR_K=8`
- `RAG_FINAL_K=4`
- `RAG_RERANK_MAX_CANDIDATES=12`

These should be adjusted after timing and quality checks.

### Acceptance Criteria

- Reranker input count is visible in logs.
- Zero-score BM25 results are not sent to the reranker.
- Duplicate candidates are removed.
- Retrieval still returns useful source citations.

## Phase 5: Reduce Prompt Size

Purpose: lower Ollama prompt evaluation time.

### Changes

- Add a configurable prompt text cap:
  - `RAG_MAX_CONTEXT_CHARS`
  - optional `RAG_MAX_CHUNK_CONTEXT_CHARS`
- Trim chunk text during prompt assembly, not during indexing.
- Keep citation metadata intact even if text is trimmed.
- Consider reducing `RAG_FINAL_K` after reviewing quality.

### Files

- `settings.py`
- `api/query_rag.py`

### Acceptance Criteria

- Prompt size is logged.
- Prompt assembly never includes unbounded chunk text.
- Sources still identify the original files and chunk indexes.

## Phase 6: Add Fast Retrieval Mode

Purpose: allow latency-sensitive deployments to skip MMR and avoid returning
vectors from Qdrant.

### Changes

- Add `RAG_FAST_MODE` or `RAG_ENABLE_MMR`.
- When MMR is disabled:
  - call Qdrant with `with_vectors=False`
  - skip local MMR
  - merge vector and keyword candidates directly
- Keep default behavior quality-oriented unless timings show fast mode should
  become the default.

### Files

- `settings.py`
- `api/retrieval.py`

### Acceptance Criteria

- Fast mode avoids requesting vectors from Qdrant.
- Default mode still supports MMR.
- Logs clearly show whether MMR ran.

## Phase 7: Optimize BM25 Top-K

Purpose: avoid full sorting of every BM25 score.

### Changes

- Replace full `sorted(...)[:limit]` with `heapq.nlargest(...)`.
- Filter zero-score results before returning.
- Include BM25 result count in timing logs.

### Files

- `api/keyword_index.py`

### Acceptance Criteria

- Keyword search returns the same shape as before.
- Zero-score keyword matches are omitted.
- Runtime improves or remains neutral on small corpora.

## Phase 8: Benchmark and Tune Concurrency

Purpose: avoid local model contention.

### Changes

- Add configurable values:
  - `RAG_MAX_WORKERS`
  - `RAG_MAX_CONCURRENCY`
- Benchmark values of `1`, `2`, and `4`.
- Keep the best default for this deployment based on measured p50 and p95
  latency.

### Files

- `settings.py`
- `web/api_server.py`

### Acceptance Criteria

- Concurrency is controlled by environment variables.
- Benchmarks include single-request and concurrent-request runs.
- Recommended defaults are documented.

## Validation Plan

Use a small fixed query set that covers:

- exact filename or identifier lookup
- code question
- documentation summary question
- broad conceptual question
- no-context question

For each implementation phase, record:

- total latency
- time to first token for streaming
- embedding time
- vector recall time
- BM25 time
- rerank time
- prompt size
- generation time
- number of candidates reranked
- number of final context chunks

Compare answer quality manually against the baseline for the fixed query set.

## Rollout Strategy

1. Merge instrumentation first.
2. Collect baseline timings.
3. Merge shared Ollama client.
4. Merge true streaming.
5. Tune retrieval defaults behind configuration.
6. Enable smaller rerank and prompt defaults after quality review.
7. Keep fast mode opt-in until it has enough quality validation.
8. Tune concurrency last, because best values depend on hardware and Ollama
   model behavior.

## Expected Result

The biggest user-visible improvement should come from true streaming. The
largest backend latency reductions should come from smaller reranker input,
smaller prompt context, and lower model contention under concurrent traffic.

