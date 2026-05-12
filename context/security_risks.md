# Security Risk Assessment

Reviewed: 2026-05-11  
Scope: Application code, Docker Compose configuration, volume mounts, Caddy reverse proxy

---

## Architecture

```
Internet ──► Caddy (DuckDNS/TLS) ──► tk421.nucbox:8000 (FastAPI)
                                            │
                                     qdrant:6333 (Docker internal + LAN-exposed)
                                            │
                                  host.docker.internal:11434 (Ollama, host)
```

`ai.spoonscloud.duckdns.org` is internet-accessible. Caddy provisions TLS automatically via Let's Encrypt.

---

## Summary

The Caddy layer adds TLS and a secret-path capability URL as the sole access control for the API. This meaningfully raises the bar for casual attackers, but the secret can leak through logs and referrer headers, there is no rate limiting, and Qdrant remains completely unprotected on the LAN. The attack surface from the open internet is the secret URL; the attack surface from the LAN is Qdrant directly.

---

## What Caddy Mitigates

| Finding | Status |
|---|---|
| No TLS | **Fixed** — Caddy handles Let's Encrypt automatically |
| Unknown host access | **Fixed** — `:80` and `:443` return 400 for unconfigured hostnames |
| Unauthenticated internet access to API | **Partially mitigated** — secret path prefix required (see below) |

---

## Findings

### CRITICAL — Qdrant Still Exposed on LAN with No Authentication

**File:** `docker-compose.yml:29-30`

Qdrant binds to `0.0.0.0:6333` (HTTP) and `0.0.0.0:6334` (gRPC) and is not proxied through Caddy. It has no authentication. Any device on your LAN can read every indexed document chunk, delete the entire collection, or insert arbitrary data.

**Example attack:** `curl http://tk421.nucbox:6333/collections/documents/points/scroll`

This exposes the full text of everything ever indexed: Nextcloud files, resumes, code, scripts.

**Fix:** Remove the `ports:` block for Qdrant in `docker-compose.yml`. It only needs to be reachable by the `api` container, which uses the Docker-internal hostname `qdrant`.

---

### HIGH — Secret Path Is a Capability URL, Not Authentication

**File:** `Caddyfile:51-64`

```
@invalid {
    not path /d4ddba4633759950d0ddf3ea325e648a0b63d5a69d695924f1cecf7e528389c7/*
}
respond @invalid 403
```

The 64-character hex prefix acts as a shared secret baked into the URL. This pattern is legitimate (used in webhook systems, one-time links) but carries specific risks that true authentication does not:

- **Caddy access logs** record the full request path in plaintext — the secret is in every log line
- **Browser history and autocomplete** store the full URL
- **Referrer headers** — if Caddy or the backend ever issues a redirect to an external URL, browsers include the full originating URL in the `Referer` header, leaking the secret to that third party
- **No expiry or rotation** — once leaked, the secret is valid until the Caddyfile is manually updated and reloaded
- **No per-client identity** — you cannot revoke access for one client without revoking it for all

**Fix:** Replace with `Authorization: Bearer <token>` header validation, either in Caddy (using a `basicauth` or `request_header` matcher) or in the FastAPI middleware. Headers do not appear in logs by default and are not included in Referer leakage.

---

### HIGH — No Rate Limiting

**File:** `Caddyfile`

There is no rate limiting in the Caddy configuration. An attacker who obtains the secret URL can send unlimited requests, consuming GPU/CPU on Ollama and the reranker without restriction.

Standard Caddy does not include a rate-limit directive; it requires the `caddy-ratelimit` plugin or a compiled custom build.

**Fix:** Either build Caddy with `caddy-ratelimit` and add a `rate_limit` directive, or add FastAPI middleware that tracks requests per IP and returns 429 after a threshold.

---

### HIGH — Host Volumes Mounted Read-Write

**File:** `docker-compose.yml:88-90`

```yaml
- ${NEXTCLOUD_PATH}:/watch/Nextcloud
- ${CODE_PATH}:/watch/Code
```

The entire Nextcloud and Code directories are mounted into the watcher container without `:ro`. The watcher only reads files. A container escape gives an attacker read-write access to both directories.

**Fix:** Append `:ro` to both volume entries.

---

### HIGH — Full Filesystem Paths in Every API Response

**File:** `api/query_rag.py:71-78`

Every chat completion response includes the resolved absolute path of every source file:

```
[S1] /mnt/c/Users/Garret/Nextcloud/Resumes/resume.pdf (rerank=0.9234)
```

Anyone who obtains the secret URL receives a map of your filesystem with every query response. Combined with Caddy log exposure of the secret, this compounds into a meaningful data leak.

**Fix:** Return only the filename (or a stable hashed identifier) instead of the full path in `_format_sources`.

---

### MEDIUM — Ollama Likely Exposed on LAN

**File:** `settings.py:26`

Ollama runs on the host and on Windows defaults to binding on `0.0.0.0:11434`. If so, any device on the LAN can call it directly — bypassing the RAG layer and using your GPU for free.

**Verify:** `curl http://localhost:11434/api/tags`  
If it responds, Ollama is bound to all interfaces.

**Fix:** Set `OLLAMA_HOST=127.0.0.1` in the Ollama service configuration (Windows: Environment tab in Task Scheduler or a `.env` next to the Ollama binary).

---

### MEDIUM — CORS Still Fully Open on the API

**File:** `web/api_server.py:56-61`

```python
allow_origins=["*"]
allow_methods=["*"]
allow_headers=["*"]
```

Now that the API is internet-accessible behind a known domain, open CORS means any website can make credentialed cross-origin requests from a visitor's browser — as long as the browser already has the secret URL. If the secret URL ever leaks to a malicious actor, they can embed it in a webpage and exfiltrate responses from your RAG system via visitors' browsers.

**Fix:** Set `allow_origins` to the specific client origins that legitimately call the API (e.g., Open WebUI's domain).

---

### LOW — Model Name Not Validated Against an Allowlist

**File:** `web/api_server.py:249`

The `model` field is forwarded to Ollama without validation. Ollama sanitizes it, so no known injection vector exists, but an attacker can force Ollama to attempt loading arbitrary model names.

**Fix:** Validate `req.model` against the list returned by `/v1/models` before forwarding.

---

## What Is Not a Risk

- **No RCE:** No `subprocess`, `os.system`, `shell=True`, `eval`, or `exec` anywhere in the codebase.
- **No SQL injection:** Qdrant filters use typed objects, not string interpolation.
- **No file write via API:** There is no upload or write endpoint.
- **No path traversal via API:** File paths in responses come from stored metadata, not user-controlled input.

---

## Recommended Fix Priority

| Priority | Action | Effort |
|---|---|---|
| 1 | Remove Qdrant `ports:` block — LAN exposure of all indexed data | Low |
| 2 | Add `:ro` to watcher volume mounts | Trivial |
| 3 | Replace secret-path with `Authorization` header auth in Caddy or FastAPI | Low |
| 4 | Add rate limiting (FastAPI middleware or Caddy plugin) | Medium |
| 5 | Strip full paths from API responses | Low |
| 6 | Restrict Ollama to `127.0.0.1` | Low |
| 7 | Restrict CORS `allow_origins` to known client domains | Low |
| 8 | Validate `model` against `/v1/models` allowlist | Low |
