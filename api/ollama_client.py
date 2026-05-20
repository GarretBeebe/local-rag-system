"""Shared HTTP session for all Ollama API calls."""

import json
import logging
import threading
import time
from collections.abc import Iterator
from typing import Any

import requests
from requests import RequestException

from settings import OLLAMA_BASE_URL, OLLAMA_GENERATE_TIMEOUT_SECONDS, OLLAMA_NUM_CTX

logger = logging.getLogger(__name__)

_session = requests.Session()
_MAX_RETRIES = 2
_RETRY_DELAY = 1.0


def post(path: str, **kwargs: Any) -> requests.Response:
    return _session.post(f"{OLLAMA_BASE_URL}{path}", **kwargs)


def get(path: str, **kwargs: Any) -> requests.Response:
    return _session.get(f"{OLLAMA_BASE_URL}{path}", **kwargs)


def _post_with_retry(path: str, **kwargs: Any) -> requests.Response:
    """POST with up to _MAX_RETRIES retries on 5xx responses."""
    url = f"{OLLAMA_BASE_URL}{path}"
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            r = _session.post(url, **kwargs)
            if r.status_code >= 500 and attempt < _MAX_RETRIES:
                logger.warning(
                    "Ollama returned HTTP %d for %s (attempt %d/%d), retrying",
                    r.status_code, path, attempt + 1, _MAX_RETRIES + 1,
                )
                time.sleep(_RETRY_DELAY)
                continue
            if not r.ok:
                raise RuntimeError(
                    f"Ollama request to {path} failed: HTTP {r.status_code}"
                )
            return r
        except RequestException as e:
            last_exc = e
            if attempt < _MAX_RETRIES:
                logger.warning("Ollama request to %s failed: %s (retrying)", path, e)
                time.sleep(_RETRY_DELAY)
    raise RuntimeError(f"Ollama request to {path} failed after retries: {last_exc}")


def _generate_payload(model: str, prompt: str, *, stream: bool) -> dict[str, Any]:
    return {
        "model": model, "prompt": prompt, "stream": stream,
        "options": {"num_ctx": OLLAMA_NUM_CTX},
    }


def generate(prompt: str, model: str, timeout: float = OLLAMA_GENERATE_TIMEOUT_SECONDS) -> str:
    """Return a complete generated response from Ollama."""
    r = _post_with_retry(
        "/api/generate", json=_generate_payload(model, prompt, stream=False), timeout=timeout
    )
    return r.json()["response"]


def stream_generate(
    prompt: str,
    model: str,
    timeout: float = OLLAMA_GENERATE_TIMEOUT_SECONDS,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    """Yield text chunks from Ollama's streaming generation API."""
    with _session.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json=_generate_payload(model, prompt, stream=True),
        stream=True,
        timeout=timeout,
    ) as resp:
        if not resp.ok:
            raise RuntimeError(
                f"Ollama stream request failed: HTTP {resp.status_code}"
            )
        for line in resp.iter_lines(decode_unicode=True):
            if cancel and cancel.is_set():
                break
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Ollama stream: skipping malformed line: %r", line[:120])
                continue
            if data.get("response"):
                yield data["response"]
            if data.get("done"):
                break
