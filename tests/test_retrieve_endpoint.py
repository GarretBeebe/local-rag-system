from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import web.api_server as srv
from api.retrieval import Chunk

_TOKEN = "test-secret-token"
_CHUNK = Chunk(
    id="1",
    payload={"text": "def foo(): pass", "filepath": "/watch/Code/foo.py"},
    score=0.9,
    rerank_score=0.95,
)
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


def test_retrieve_no_token_configured():
    with TestClient(srv.app) as c:
        resp = c.post("/v1/retrieve", json={"query": "hello"}, headers=_AUTH)
    assert resp.status_code == 503


def test_retrieve_wrong_token(monkeypatch):
    monkeypatch.setattr(srv, "RAG_INTERNAL_TOKEN", _TOKEN)
    with TestClient(srv.app) as c:
        resp = c.post(
            "/v1/retrieve",
            json={"query": "hello"},
            headers={"Authorization": "Bearer wrong"},
        )
    assert resp.status_code == 401


def test_retrieve_missing_auth_header(monkeypatch):
    monkeypatch.setattr(srv, "RAG_INTERNAL_TOKEN", _TOKEN)
    with TestClient(srv.app) as c:
        resp = c.post("/v1/retrieve", json={"query": "hello"})
    assert resp.status_code == 401


def test_retrieve_returns_chunks(monkeypatch):
    monkeypatch.setattr(srv, "RAG_INTERNAL_TOKEN", _TOKEN)
    with patch("web.api_server.retrieve_best", return_value=[_CHUNK]), TestClient(srv.app) as c:
        resp = c.post("/v1/retrieve", json={"query": "foo function"}, headers=_AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["chunks"]) == 1
    assert data["chunks"][0]["text"] == "def foo(): pass"
    assert data["chunks"][0]["filepath"] == "/watch/Code/foo.py"
    assert data["chunks"][0]["score"] == pytest.approx(0.95)


def test_retrieve_empty_result(monkeypatch):
    monkeypatch.setattr(srv, "RAG_INTERNAL_TOKEN", _TOKEN)
    with patch("web.api_server.retrieve_best", return_value=[]), TestClient(srv.app) as c:
        resp = c.post("/v1/retrieve", json={"query": "nothing"}, headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"chunks": []}


def test_retrieve_uses_rerank_score_when_available(monkeypatch):
    monkeypatch.setattr(srv, "RAG_INTERNAL_TOKEN", _TOKEN)
    chunk_no_rerank = Chunk(
        id="2",
        payload={"text": "x", "filepath": "/f.py"},
        score=0.7,
        rerank_score=None,
    )
    with (
        patch("web.api_server.retrieve_best", return_value=[chunk_no_rerank]),
        TestClient(srv.app) as c,
    ):
        resp = c.post("/v1/retrieve", json={"query": "x"}, headers=_AUTH)
    assert resp.json()["chunks"][0]["score"] == pytest.approx(0.7)
