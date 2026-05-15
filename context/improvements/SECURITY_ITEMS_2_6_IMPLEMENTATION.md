# Security Items 2-6 Implementation Notes

Date: 2026-05-15

Scope: Implement the immediate hardening items identified in the internet-exposed
RAG review, excluding item 1 because the API must stay reachable from the separate
Caddy host at `192.168.68.69`.

## Findings

### 2. Public login endpoint needs a tighter limiter

`/auth/login` is intentionally unauthenticated so browser users can sign in, but
it currently shares the same 30 requests/minute per-IP limiter as the rest of the
API. That is too loose for credential guessing and can also let login attempts
consume the general request budget.

Implementation target:
- Add a login-specific per-IP limiter.
- Keep the existing general limiter for authenticated API traffic.

### 3. Indexed content includes broad code and personal directories

The watcher indexes broad paths including `/watch/Code` and multiple personal
Nextcloud folders. It also allows many source/config extensions. The ignore
matcher only checks exact path components, so patterns like `*.pem` or
`*secret*` are not possible.

Implementation target:
- Add glob-style ignore support.
- Add baseline secret and credential ignore patterns to the container watcher
  config.
- Keep current watch roots unchanged to avoid silently dropping intended content.

### 4. Qdrant has no API key

Qdrant is not host-published in the main compose stack, but containers on the
same Docker network can still access it without authentication. A compromised
container could read or delete indexed chunks.

Implementation target:
- Add `QDRANT_API_KEY` setting.
- Configure Qdrant with `QDRANT__SERVICE__API_KEY`.
- Pass the key to API and watcher containers.
- Initialize `QdrantClient` with the key when set.
- Require `QDRANT_API_KEY` in compose so the hardening cannot silently run
  disabled after a container recreate.

### 5. Standalone Qdrant compose publishes unauthenticated ports

`vector-db/qdrant/docker-compose.yml` publishes `6333` and `6334` directly. If
used as-is, it exposes the database API without authentication.

Implementation target:
- Remove the host port mappings.
- Add a warning comment.

### 6. Non-streaming answers are logged

The non-streaming chat route logs the first 200 characters of each generated
answer. Since answers may contain private document excerpts, this can leak
sensitive content into application logs.

Implementation target:
- Remove answer-content logging from the request path.

## Verification Plan

- Run the Python test suite if present.
- Run static import/compile checks for changed Python modules.
- Inspect compose/config diffs for expected environment and port changes.

## Deployment Note

The current local `.env` must be updated before recreating the containers:

```bash
QDRANT_API_KEY=<output of: openssl rand -hex 32>
```

After setting it, recreate the affected services so Qdrant and both clients use
the same key.
