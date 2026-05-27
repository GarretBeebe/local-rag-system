from fastapi.testclient import TestClient

import web.api_server as srv


def test_ui_static_mount_does_not_serve_backend_source(monkeypatch):
    with TestClient(srv.app) as client:
        assert client.get("/ui/").status_code == 200
        assert client.get("/ui/app.js").status_code == 200
        assert client.get("/ui/api_server.py").status_code == 404
        assert client.get("/ui/auth.py").status_code == 404


def test_cookie_token_is_accepted_when_authorization_header_missing(monkeypatch):
    monkeypatch.setattr(srv, "is_valid_token", lambda token: token == "cookie-token")
    monkeypatch.setattr(srv.ollama_client, "get", lambda *args, **kwargs: _FakeModelsResponse())

    with TestClient(srv.app) as client:
        client.cookies.set(srv._AUTH_COOKIE, "cookie-token")
        r = client.get("/v1/models")

    assert r.status_code == 200


def test_root_requires_auth_when_auth_configured(monkeypatch):
    with TestClient(srv.app) as client:
        r = client.get("/")

    assert r.status_code == 401


def test_healthz_remains_available_for_container_healthcheck(monkeypatch):
    with TestClient(srv.app) as client:
        r = client.get("/healthz")

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


class _FakeModelsResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"models": [{"name": "test-model"}]}


def test_login_sets_httponly_cookie(monkeypatch):
    monkeypatch.setattr(srv.user_store, "get_hash", lambda username: "stored-hash")
    monkeypatch.setattr(srv._bcrypt, "checkpw", lambda password, stored: True)
    monkeypatch.setattr(srv, "create_session", lambda username: "opaque-session-token")

    with TestClient(srv.app) as client:
        r = client.post("/auth/login", json={"username": "alice", "password": "secret"})

    assert r.status_code == 200
    assert r.json() == {"ok": True}
    cookie = r.headers["set-cookie"]
    assert "rag_token=opaque-session-token" in cookie
    assert "HttpOnly" in cookie
    assert "Max-Age=28800" in cookie


def test_static_app_js_does_not_reference_removed_token_variable():
    js = srv._STATIC_DIR.joinpath("app.js").read_text()
    assert "TOKEN" not in js
    assert "localStorage" not in js
    assert "Authorization" not in js


def test_chat_view_uses_hidden_attribute_not_permanent_display_none():
    css = srv._STATIC_DIR.joinpath("app.css").read_text()
    chat_block = css.split("#chat-view {", 1)[1].split("}", 1)[0]
    assert "display: flex" in chat_block
    assert "display: none" not in chat_block
    assert "[hidden]" in css
