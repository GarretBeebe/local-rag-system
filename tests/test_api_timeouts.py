"""Tests proving operational timeout constants reach their HTTP call sites."""

from unittest.mock import MagicMock


def test_embed_passes_configured_timeout_to_http_call(monkeypatch):
    """embed() must forward OLLAMA_EMBED_TIMEOUT_SECONDS to the Ollama HTTP call."""
    from api.embed import embed
    from settings import OLLAMA_EMBED_TIMEOUT_SECONDS

    captured = {}

    def fake_post_with_retry(path, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        resp = MagicMock()
        resp.json.return_value = {"embedding": [0.1] * 768}
        return resp

    monkeypatch.setattr("api.embed.ollama_client.post_with_retry", fake_post_with_retry)
    embed("hello world")
    assert captured["timeout"] == OLLAMA_EMBED_TIMEOUT_SECONDS


def test_generate_passes_configured_timeout_to_http_call(monkeypatch):
    """generate() must forward OLLAMA_GENERATE_TIMEOUT_SECONDS to the Ollama HTTP call."""
    from api import ollama_client
    from settings import OLLAMA_GENERATE_TIMEOUT_SECONDS

    captured = {}

    def fake_post_with_retry(path, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"response": "ok"}
        return resp

    monkeypatch.setattr("api.ollama_client.post_with_retry", fake_post_with_retry)
    ollama_client.generate("test prompt", "test-model")
    assert captured["timeout"] == OLLAMA_GENERATE_TIMEOUT_SECONDS


def test_stream_generate_raises_on_ollama_error_payload(monkeypatch):
    """Ollama can report stream failures as 200 JSON events with an error field."""
    import pytest

    from api import ollama_client

    response = MagicMock()
    response.ok = True
    response.iter_lines.return_value = ['{"error":"model load failed"}']
    response.__enter__.return_value = response
    response.__exit__.return_value = None

    monkeypatch.setattr("api.ollama_client._session.post", lambda *a, **kw: response)

    with pytest.raises(RuntimeError, match="model load failed"):
        list(ollama_client.stream_generate("test prompt", "test-model"))


def test_models_endpoint_passes_configured_timeout(monkeypatch):
    """models() must forward OLLAMA_MODEL_LIST_TIMEOUT_SECONDS to the Ollama HTTP call."""
    import web.api_server as srv
    from settings import OLLAMA_MODEL_LIST_TIMEOUT_SECONDS

    captured = {}

    def fake_get(path, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        raise RuntimeError("suppressed by models() try/except")

    monkeypatch.setattr(srv.ollama_client, "get", fake_get)
    srv.models()  # exception caught internally; function returns fallback
    assert captured.get("timeout") == OLLAMA_MODEL_LIST_TIMEOUT_SECONDS


def test_warm_models_passes_configured_timeout(monkeypatch):
    """_warm_models() must forward OLLAMA_WARMUP_TIMEOUT_SECONDS to the LLM warmup call."""
    import asyncio

    import web.api_server as srv
    from settings import OLLAMA_WARMUP_TIMEOUT_SECONDS

    captured = {}

    def fake_post(path, **kwargs):
        if path == "/api/generate":
            captured["timeout"] = kwargs.get("timeout")
        raise RuntimeError("suppressed by _warm_one try/except")

    monkeypatch.setattr(srv.ollama_client, "post", fake_post)
    asyncio.run(srv._warm_models())
    assert captured.get("timeout") == OLLAMA_WARMUP_TIMEOUT_SECONDS


def test_model_warmup_is_opt_in_by_default():
    from settings import WARM_MODELS_ON_STARTUP

    assert WARM_MODELS_ON_STARTUP is False
