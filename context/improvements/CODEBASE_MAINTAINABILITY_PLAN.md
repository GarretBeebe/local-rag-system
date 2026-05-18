# Codebase Maintainability Improvement Plan

This plan captures code-quality, performance, and maintainability improvements
identified during the codebase review. The order prioritizes the one active
correctness bug first, then establishes a reproducible environment and test
baseline, then proceeds with structural cleanup. No code changes are implied by
this document.

## Goals

- Reduce import-time side effects and startup surprises.
- Prevent stale or duplicate indexed chunks after file changes.
- Improve retrieval and ingestion scalability.
- Make the web API easier to understand, test, and extend.
- Improve build reproducibility and local tooling consistency.

## Non-Goals

- Replacing Qdrant.
- Replacing Ollama.
- Changing the public OpenAI-compatible API shape.
- Rewriting the full RAG pipeline.
- Introducing a new web framework.

## Phase 1: Fix Re-Indexing Semantics (Narrow Fix + Focused Tests)

Purpose: eliminate the active correctness bug where changed files accumulate
stale chunks. `index_file()` currently creates new UUID point IDs and upserts
without deleting existing vectors first (`index_documents.py:83`, `:118`), and
`watcher.py:83` unconditionally calls `upsert_hash()` regardless of whether
indexing actually succeeded. This phase is scoped narrowly: fix the bug and
add regression tests that cover it. Broader test infrastructure comes in Phase 3.

### Changes

- Delete existing vectors for a file before inserting replacement chunks.
- Give `index_file()` an explicit return type with distinct outcomes: `indexed`,
  `skipped` (large file, unreadable, empty chunks), `failed` (embedding error or
  Qdrant error). The current `None`-for-everything contract makes it impossible
  for the watcher to distinguish a successful index from a silent skip or failure.
- Update `watcher.py` to call `upsert_hash()` only on an `indexed` outcome.
- Decide and document whether partial embedding failures should leave the old
  document in place or replace it with a partial index. Pick one; do not leave
  this ambiguous.
- Prefer stable point IDs derived from document ID and chunk index, or keep UUIDs
  only if pre-delete is guaranteed.

### Files

- `ingest/index_documents.py`
- `indexer/watcher.py`
- `indexer/fingerprint_store.py`

### Acceptance Criteria

- Re-indexing the same changed file does not leave old chunks behind.
- A failed or skipped re-index does not update the fingerprint hash.
- Qdrant contains exactly one current document version per filepath.
- A regression test verifies that changing a file and re-indexing leaves exactly
  one version in Qdrant.
- A regression test verifies that a failed re-index does not mark the fingerprint
  as current.

## Phase 2: Improve Packaging and Build Reproducibility

Purpose: make local and Docker builds repeatable so phases 3 onward can be
validated reliably.

**Strategy decision required before starting**: the Dockerfile currently installs
CPU torch separately and then runs `pip install -e .`, which means a naive lock
file may not capture the right torch variant. Before any work begins, choose one
of: (a) pinned `requirements.txt` with CPU torch extras explicit, (b) `uv.lock`
with a `uv pip compile` workflow, or (c) another lock format. Do not start this
phase with "introduce a lock file" as the plan — pick the format first.

### Changes

- Adopt the chosen packaging strategy and generate the initial lock file.
- Add development dependencies for linting, tests, and optional type checking.
- Optimize Docker layer caching by installing dependencies before copying
  application source, so source-only edits do not reinstall packages.
- Consider a non-editable Docker install unless live editable installs are required.

### Files

- `pyproject.toml`
- `Dockerfile`
- Lock file (format determined by strategy decision above).

### Acceptance Criteria

- Fresh environments can run lint/test tooling from declared dependencies.
- Docker rebuilds avoid reinstalling unchanged dependencies after source-only edits.
- Builds are reproducible across machines.

## Phase 3: Add Real Automated Tests and Separate Smoke Scripts

Purpose: establish a broader test baseline before making structural changes.
Without this, phases 4 onward cannot be safely validated.

### Changes

- Move `ingest/test_rag.py` out of pytest discovery, or rename it as a smoke script.
- Add assert-based unit tests for:
  - chunking behavior
  - path normalization and ignore matching
  - request validation
  - auth token behavior
  - retrieval deduplication
  - changed-file re-indexing leaves exactly one current document version in Qdrant
    (this is the most important regression test and must be present before any
    retrieval or indexing refactor proceeds)
  - failed or empty re-indexing does not mark the fingerprint as current
- Add integration tests behind explicit markers for Qdrant and Ollama.
- Add documented commands for unit tests versus integration smoke checks.

### Files

- `ingest/test_rag.py`
- `tests/`
- `pyproject.toml`
- Optional `scripts/` directory.

### Acceptance Criteria

- Unit tests run without Qdrant or Ollama.
- Integration smoke checks are opt-in.
- Test tooling is installed through project metadata.
- CI or local commands clearly distinguish fast tests from service-dependent checks.

## Phase 4: Remove Import-Time Side Effects and Separate Runtime Clients

Purpose: make modules cheap to import and settings safe to load without creating
network connections. These two concerns are merged because `settings.py` creates
the global Qdrant client on import, which means fixing import side effects in
`retrieval.py` and `keyword_index.py` without first fixing the client factory
would create churn — the import fixes would still trigger a Qdrant connection
through settings.

### Changes

- Replace the global `qdrant_client` in `settings.py` with a client factory or
  runtime module. This must happen before the model-loading side effects are moved,
  since both depend on the same settings import.
- Keep environment parsing in one place, but avoid creating network clients there.
- Consider a typed config object for settings that are currently bare constants.
- Update modules to receive clients from a shared runtime dependency instead of
  importing directly from `settings.py`.
- Move `CrossEncoder` construction out of `api/retrieval.py` module import.
- Move `KeywordIndex` construction and background refresh thread out of module import.
- Initialize retrieval dependencies during API startup or through lazy factories.
- Keep command-line scripts able to import retrieval helpers without loading all models.

### Files

- `settings.py`
- `api/retrieval.py`
- `api/keyword_index.py`
- `ingest/index_documents.py`
- `indexer/fingerprint_store.py`
- `web/api_server.py`

### Acceptance Criteria

- Importing `settings` never creates a network client.
- Importing `api.retrieval` does not immediately load the reranker model.
- Importing `api.retrieval` does not immediately scroll Qdrant.
- Background keyword refresh starts only when the API/runtime explicitly starts it.
- Tests can substitute a fake Qdrant client without monkeypatching global settings.
- Runtime behavior still uses the same environment variables.
- Existing query behavior remains unchanged after initialization.

## Phase 5: Improve Keyword Index Scalability

Purpose: avoid full-collection rebuilds as the indexed corpus grows.

**Design note**: the incremental update approach (tracking changes from the
watcher/fingerprint store) must be concretely designed before implementation
begins. "Avoid scrolling the entire collection when possible" is not specific
enough to code against — the data flow and consistency model need to be pinned
first or this phase will be punted mid-implementation.

### Changes

- Replace synchronous startup rebuilds with explicit startup lifecycle management.
- Avoid scrolling the entire Qdrant collection every refresh when possible.
- Track indexed document changes from the watcher/fingerprint store for incremental
  updates, or persist keyword index state separately.
- Cache known filenames used for filename extraction.
- Add compact logging for keyword index size, refresh duration, and failures.

### Files

- `api/keyword_index.py`
- `api/retrieval.py`
- `indexer/fingerprint_store.py`

### Acceptance Criteria

- Keyword index refresh work is observable in logs.
- Query-time filename extraction does not read every fingerprint row.
- Large collections do not force blocking full-index rebuilds during import.

## Phase 6: Improve Ingestion Throughput

Purpose: reduce indexing time for multi-chunk files and larger initial scans.

### Changes

- Batch embedding requests if the active embedding API supports it.
- Add configurable ingestion concurrency with conservative defaults.
- Keep Qdrant upserts batched per file or configurable by batch size.
- Add per-file timing logs for read, chunk, embed, and upsert stages.

### Files

- `api/embed.py`
- `ingest/index_documents.py`
- `indexer/watcher.py`
- `settings.py`

### Acceptance Criteria

- Initial indexing is faster for files with many chunks.
- Ingestion concurrency does not overwhelm Ollama by default.
- Embedding failures remain visible and actionable.

## Phase 7: Split the Web API Module

Purpose: reduce the size and mixed responsibilities of `web/api_server.py`.
Deferred until phases 2–4 are complete so the refactor can be validated by
the test suite rather than by manual inspection.

### Changes

- Move authentication and token validation into a dedicated module.
- Move rate limiting into a dedicated module.
- Move request/response schemas into a dedicated module.
- Move OpenAI-compatible response and SSE formatting into a dedicated module.
- Keep `web/api_server.py` focused on app creation, route wiring, and lifespan.

### Files

- `web/api_server.py`
- `web/auth.py`
- `web/rate_limit.py`
- `web/schemas.py`
- `web/openai_compat.py`

### Acceptance Criteria

- `web/api_server.py` is materially smaller and mostly route orchestration.
- Existing endpoints and response shapes are unchanged.
- Auth, rate limit, and response formatting logic can be tested independently.

## Phase 8: Replace Loose Dicts With Typed Data Structures

Purpose: reduce fragile key access across retrieval and prompt assembly.

### Changes

- Introduce typed dataclasses or Pydantic models for retrieved chunks and payloads.
- Normalize optional fields such as `vector`, `score`, and `rerank_score`.
- Replace direct nested dict access where practical.
- Keep serialization boundaries explicit at Qdrant and API response edges.

### Files

- `api/retrieval.py`
- `api/query_rag.py`
- `api/keyword_index.py`

### Acceptance Criteria

- Retrieval code no longer depends on broad `dict[str, Any]` for core objects.
- Missing payload fields fail clearly.
- Prompt assembly remains compatible with existing indexed payloads.

## Phase 9: Make Operational Constants Configurable

Purpose: allow deployments to tune behavior without code changes.

### Changes

- Move hard-coded executor and semaphore sizes into settings.
- Move rate limit window and request counts into settings.
- Move Ollama generation options, including context size, into settings.
- Move retrieval defaults such as recall, MMR, and final result counts into settings
  if not already handled by the response-time plan.

### Files

- `settings.py`
- `web/api_server.py`
- `api/ollama_client.py`
- `api/retrieval.py`

### Acceptance Criteria

- Operators can tune API concurrency and rate limits through environment variables.
- Operators can tune Ollama generation options through environment variables.
- Defaults remain compatible with the current Docker setup.

## Phase 10: Improve Error Handling and Observability

Purpose: make failures easier to diagnose without leaking internals to clients.

### Changes

- Use narrower exception handling around Qdrant, Ollama, JSON parsing, and filesystem
  operations.
- Add structured logs for external service failures.
- Preserve concise client-facing error messages.
- Add request IDs to API logs and streaming errors.

### Files

- `api/ollama_client.py`
- `api/embed.py`
- `api/retrieval.py`
- `ingest/index_documents.py`
- `indexer/watcher.py`
- `web/api_server.py`

### Acceptance Criteria

- Service failures include enough log context to diagnose the failing dependency.
- Client responses avoid raw stack traces or implementation details.
- Streaming errors are visible in logs with a request identifier.
