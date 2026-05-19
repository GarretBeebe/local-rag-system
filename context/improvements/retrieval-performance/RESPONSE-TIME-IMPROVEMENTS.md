# Response Time Improvement Findings

This document summarizes latency improvement opportunities found in the local
RAG response path.

## Current Hot Path

The main query path is:

```text
chat request
  -> ask()
  -> embed query with Ollama
  -> Qdrant vector recall
  -> BM25 keyword recall
  -> MMR diversification
  -> CPU cross-encoder reranking
  -> prompt assembly
  -> Ollama generation
  -> response
```

Relevant files:

- `web/api_server.py`
- `api/query_rag.py`
- `api/retrieval.py`
- `api/embed.py`
- `api/keyword_index.py`
- `settings.py`

## Highest-Impact Opportunities

### 1. Implement Real Streaming From Ollama

`web/api_server.py` supports `stream: true`, but the server waits for the full
RAG answer before sending streamed chunks. `_stream_answer()` currently replays
an already-complete answer word by word.

Recommended change:

- Add streaming generation support in `api/query_rag.py`.
- Call Ollama with `stream: true`.
- Proxy generated chunks directly through the FastAPI streaming response.

Expected benefit:

- Large improvement in perceived latency and time to first token.
- Does not require reducing retrieval quality.

### 2. Reduce Cross-Encoder Reranking Work

`api/retrieval.py` loads `BAAI/bge-reranker-base` on CPU. The current request
path can rerank roughly 40 candidates:

- 10 MMR-selected vector results.
- Up to 30 keyword results.

Cross-encoder reranking is likely one of the most expensive pre-generation
steps.

Recommended changes:

- Lower `recall_k` from `30` to `10-15`.
- Filter BM25 results with score `<= 0`.
- Dedupe vector and keyword results before reranking.
- Cap reranker input to the top `10-15` candidates.
- Consider skipping rerank for short exact-match queries.

Expected benefit:

- Lower CPU time per request.
- Less queuing under concurrent requests.
- Minimal quality loss if candidate filtering is tuned carefully.

### 3. Reduce Prompt Size

`api/query_rag.py` currently calls:

```python
retrieve_best(question, recall_k=30, mmr_k=10, final_k=6)
```

Markdown chunks can be up to `MAX_MD_CHUNK = 2000` characters. Passing six large
chunks to the generator increases prompt evaluation time.

Recommended changes:

- Reduce `final_k` from `6` to `3-4`.
- Cap each chunk's included text before prompt assembly.
- Consider smaller chunk sizes for documents that frequently produce large
  context blocks.

Expected benefit:

- Faster Ollama prompt processing.
- Faster total generation start.

### 4. Make MMR Optional for Latency-Sensitive Mode

`api/retrieval.py` requests Qdrant results with `with_vectors=True` so local MMR
can compare candidate vectors. Returning vectors increases response payload size
and adds Python-side cosine work.

Recommended changes:

- Add a configuration flag to disable MMR.
- When MMR is disabled, query Qdrant with `with_vectors=False`.
- Use direct top vector results plus filtered keyword results.

Expected benefit:

- Faster Qdrant response handling.
- Less Python CPU work.
- Useful for an explicit "fast mode".

### 5. Optimize BM25 Search

`api/keyword_index.py` scores every indexed document for each query, then fully
sorts all scored rows:

```python
scores = self.bm25.get_scores(tokens)
ranked = sorted(zip(scores, self.meta), reverse=True, key=lambda x: x[0])
```

Recommended changes:

- Replace full sorting with `heapq.nlargest(limit, ...)`.
- Filter zero-score results.
- For larger corpora, consider SQLite FTS, Tantivy, or Qdrant sparse vectors.

Expected benefit:

- Lower keyword recall time as the corpus grows.
- Less unnecessary reranker input.

### 6. Reuse HTTP Connections to Ollama

`api/embed.py` and `api/query_rag.py` use plain `requests.post` calls. A shared
`requests.Session` can reuse connections to the Ollama service.

Recommended changes:

- Create a small Ollama client module with a shared `requests.Session`.
- Use it for embedding and generation calls.

Expected benefit:

- Small but low-risk latency improvement.
- Cleaner central handling for timeouts and errors.

### 7. Tune RAG Concurrency

`web/api_server.py` uses:

```python
ThreadPoolExecutor(max_workers=4)
asyncio.Semaphore(4)
```

For a local Ollama backend, four concurrent RAG requests may compete for the
same CPU/GPU/model resources and increase per-request latency.

Recommended changes:

- Benchmark concurrency levels of `1`, `2`, and `4`.
- Prefer lower concurrency if the goal is faster individual responses.
- Keep higher concurrency only if aggregate throughput is more important.

Expected benefit:

- Lower tail latency under load.
- Less local model contention.

## Measurement First

Before making behavioral changes, add per-stage timing around:

- Query embedding.
- Qdrant vector recall.
- BM25 keyword recall.
- MMR.
- Cross-encoder reranking.
- Prompt assembly.
- Ollama generation.

This will make it clear which recommendations matter most on the actual
hardware and corpus.

## Suggested Priority Order

1. Add timing instrumentation.
2. Implement true Ollama streaming.
3. Reduce and dedupe reranker candidates.
4. Reduce final prompt context size.
5. Add an optional fast retrieval mode without MMR.
6. Optimize BM25 top-k selection.
7. Add shared Ollama HTTP session handling.
8. Benchmark and tune RAG concurrency.

