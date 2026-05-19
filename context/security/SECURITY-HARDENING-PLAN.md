# Security Hardening Plan

Derived from `context/security_risks.md`. Addresses all 8 findings in priority order.

---

## Step 1 — Remove Qdrant's LAN Exposure (CRITICAL)

**File:** `docker-compose.yml`

Delete the `ports:` block from the `qdrant` service. The `api` and `watcher` containers reach Qdrant via the Docker-internal hostname `qdrant:6333` — no host port binding is needed.

```yaml
# REMOVE these lines from the qdrant service:
ports:
  - "6333:6333"
  - "6334:6334"
```

After: `docker compose down && docker compose up -d`  
Verify: `curl http://localhost:6333/` should return connection refused.

---

## Step 2 — Make Watcher Volume Mounts Read-Only (HIGH)

**File:** `docker-compose.yml`

Append `:ro` to both host directory mounts in the `watcher` service:

```yaml
# CHANGE from:
- ${NEXTCLOUD_PATH}:/watch/Nextcloud
- ${CODE_PATH}:/watch/Code

# TO:
- ${NEXTCLOUD_PATH}:/watch/Nextcloud:ro
- ${CODE_PATH}:/watch/Code:ro
```

Verify: `docker exec rag-watcher touch /watch/Nextcloud/test` should return "Read-only file system".

---

## Step 3 — Add API Key Authentication to FastAPI (HIGH)

### 3a. Add env vars to `docker-compose.yml`

In the `api` service `environment:` block, add:

```yaml
API_KEY: ${API_KEY:-}
CORS_ORIGINS: ${CORS_ORIGINS:-*}
```

### 3b. Add vars to `.env` (and `.env.example`)

```bash
# API authentication key. All non-health-check endpoints require:
#   Authorization: Bearer <this value>
# Leave empty to disable auth (local dev only).
API_KEY=your-strong-random-secret-here

# Comma-separated allowed CORS origins. Use * for local dev.
# Example: CORS_ORIGINS=https://chat.example.com
CORS_ORIGINS=*
```

Generate a strong key: `openssl rand -hex 32`

### 3c. Add to `settings.py`

```python
API_KEY = os.environ.get("API_KEY", "")
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",")]
```

### 3d. Update `web/api_server.py`

Add to imports:
```python
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from settings import GEN_MODEL, API_KEY, CORS_ORIGINS
```

Add rate limiter state (after `_RAG_CONCURRENCY` semaphore):
```python
_RATE_WINDOW = 60.0
_RATE_MAX = 30
_rate_buckets: dict[str, list[float]] = defaultdict(list)
_rate_lock = asyncio.Lock()

async def _check_rate_limit(ip: str) -> bool:
    async with _rate_lock:
        now = time.monotonic()
        _rate_buckets[ip] = [t for t in _rate_buckets[ip] if now - t < _RATE_WINDOW]
        if len(_rate_buckets[ip]) >= _RATE_MAX:
            return False
        _rate_buckets[ip].append(now)
        return True
```

Replace the existing `log_requests` middleware with:
```python
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    logger.info("%s %s", request.method, request.url.path)

    if request.url.path == "/" and request.method == "GET":
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    if not await _check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    if API_KEY:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {API_KEY}":
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)
```

Update CORS middleware to use settings:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)
```

---

## Step 4 — Strip Full File Paths from API Responses (HIGH)

**File:** `api/query_rag.py`

Add import at the top:
```python
from pathlib import Path
```

In `build_prompt` (line ~37), change:
```python
# FROM:
source = _resolve_source(p)

# TO:
source = Path(_resolve_source(p)).name
```

In `_format_sources` (line ~74), change:
```python
# FROM:
path = _resolve_source(p)

# TO:
path = Path(_resolve_source(p)).name
```

---

## Step 5 — Migrate Caddy from Secret Path to Authorization Header

The current Caddy config uses a secret path prefix as the only access control. This secret appears in plaintext in Caddy's access logs and browser history. Replace it with header-based auth.

**Current block (`ai.spoonscloud.duckdns.org`):**
```caddy
ai.spoonscloud.duckdns.org {
  @invalid {
    not path /d4ddba4633759950d0ddf3ea325e648a0b63d5a69d695924f1cecf7e528389c7/*
  }
  respond @invalid 403

  handle_path /d4ddba4633759950d0ddf3ea325e648a0b63d5a69d695924f1cecf7e528389c7/* {
    reverse_proxy tk421.nucbox:8000 {
      flush_interval -1
    }
  }
}
```

**Replace with:**
```caddy
ai.spoonscloud.duckdns.org {
  encode zstd gzip

  reverse_proxy tk421.nucbox:8000 {
    flush_interval -1
  }
}
```

The FastAPI middleware (Step 3) is now the auth layer. Caddy becomes a transparent TLS terminator.

**Optional defense-in-depth:** If you want Caddy to also inject the auth header so clients don't need to know the key (e.g. Open WebUI configured with no API key), add:
```caddy
  reverse_proxy tk421.nucbox:8000 {
    flush_interval -1
    header_up Authorization "Bearer {$RAG_API_KEY}"
  }
```
Then set `RAG_API_KEY` as a system environment variable on the Caddy host. In this mode, Caddy adds the header and FastAPI validates it — external clients need no credentials. Only use this if access to the Caddy host is already locked down.

**Apply the change:**
```bash
sudo caddy reload --config /etc/caddy/Caddyfile
# or, if using systemd:
sudo systemctl reload caddy
```

---

## Step 6 — Restrict Ollama to Localhost Only

Ollama on Windows defaults to binding on `0.0.0.0:11434`, making it accessible to anyone on the LAN.

**Verify the problem first:**
```bash
# From another machine on your LAN:
curl http://tk421.nucbox:11434/api/tags
# If this returns model data, Ollama is exposed.
```

**Fix on Windows:**

Option A — Environment variable (preferred):
1. Open System Properties → Advanced → Environment Variables
2. Under System Variables, add: `OLLAMA_HOST` = `127.0.0.1`
3. Restart the Ollama service (Task Manager → Services tab → OllamaService → Restart)

Option B — If running Ollama via a `.bat` / startup script:
```bat
set OLLAMA_HOST=127.0.0.1
ollama serve
```

**Verify fix:**
```bash
# From another machine — should now fail:
curl http://tk421.nucbox:11434/api/tags
# connection refused = fixed

# From the host itself — should still work:
curl http://127.0.0.1:11434/api/tags
```

**Note:** The Docker containers reach Ollama via `host.docker.internal:11434`. With `OLLAMA_HOST=127.0.0.1`, this will break because `host.docker.internal` resolves to the host's IP, not loopback. Set `OLLAMA_HOST=0.0.0.0` and instead block port 11434 at the Windows Firewall:

```
Windows Defender Firewall → Inbound Rules → New Rule
  Rule type: Port
  Protocol: TCP, port 11434
  Action: Block the connection
  Profile: Private, Public (NOT Domain if on a domain)
  Name: Block Ollama LAN access
```

This blocks LAN access while keeping `host.docker.internal` working inside Docker.

---

## Step 7 — Validate Model Name Against Allowlist (LOW)

**File:** `web/api_server.py`

In the `chat` endpoint, validate `req.model` against the live model list before running the pipeline:

```python
@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):
    valid = {m["id"] for m in models()["data"]}
    if req.model not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown model: {req.model!r}")
    ...
```

`models()` already caches against a 5s Ollama timeout and falls back to `GEN_MODEL`, so this adds negligible overhead.

---

## Verification Checklist

After all steps are complete:

- [ ] `curl http://localhost:6333/` → connection refused (Qdrant unexposed)
- [ ] `docker exec rag-watcher touch /watch/Nextcloud/test` → read-only error
- [ ] `curl http://localhost:8000/` → `{"status": "rag-api running"}` (health check, no auth needed)
- [ ] `curl -X POST http://localhost:8000/v1/chat/completions -d '...'` → 401 (no auth header)
- [ ] Same with `-H "Authorization: Bearer <key>"` → 200/504 depending on Ollama state
- [ ] 31 rapid requests to chat endpoint → 429 on the 31st
- [ ] `curl http://tk421.nucbox:11434/api/tags` from another machine → connection refused
- [ ] `curl https://ai.spoonscloud.duckdns.org/v1/chat/completions` with correct auth header → 200
