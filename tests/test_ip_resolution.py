"""Unit tests for trusted client IP resolution."""

from unittest.mock import MagicMock


def _make_request(peer_host, xff=""):
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = peer_host
    req.headers.get.side_effect = lambda k, default="": xff if k == "X-Forwarded-For" else default
    return req


def test_untrusted_peer_ignores_xff(monkeypatch):
    import web.api_server as srv

    monkeypatch.setattr(srv, "TRUSTED_PROXY_IPS", set())
    req = _make_request("1.2.3.4", xff="10.0.0.1, 10.0.0.2")
    assert srv.resolve_client_ip(req) == "1.2.3.4"


def test_trusted_proxy_uses_first_xff_ip(monkeypatch):
    import web.api_server as srv

    monkeypatch.setattr(srv, "TRUSTED_PROXY_IPS", {"10.0.0.100"})
    req = _make_request("10.0.0.100", xff="203.0.113.1, 10.0.0.99")
    assert srv.resolve_client_ip(req) == "203.0.113.1"


def test_trusted_proxy_empty_xff_falls_back_to_peer(monkeypatch):
    import web.api_server as srv

    monkeypatch.setattr(srv, "TRUSTED_PROXY_IPS", {"10.0.0.100"})
    req = _make_request("10.0.0.100", xff="")
    assert srv.resolve_client_ip(req) == "10.0.0.100"


def test_trusted_proxy_whitespace_only_xff_falls_back_to_peer(monkeypatch):
    import web.api_server as srv

    monkeypatch.setattr(srv, "TRUSTED_PROXY_IPS", {"10.0.0.100"})
    req = _make_request("10.0.0.100", xff="   ")
    assert srv.resolve_client_ip(req) == "10.0.0.100"


def test_missing_client_returns_unknown(monkeypatch):
    import web.api_server as srv

    monkeypatch.setattr(srv, "TRUSTED_PROXY_IPS", set())
    req = MagicMock()
    req.client = None
    req.headers.get.side_effect = lambda k, default="": ""
    assert srv.resolve_client_ip(req) == "unknown"


def test_trusted_proxy_malformed_xff_falls_back_to_peer(monkeypatch):
    import web.api_server as srv

    monkeypatch.setattr(srv, "TRUSTED_PROXY_IPS", {"10.0.0.100"})
    req = _make_request("10.0.0.100", xff="not-an-ip-address")
    assert srv.resolve_client_ip(req) == "10.0.0.100"


def test_trusted_proxy_accepts_valid_ipv6(monkeypatch):
    import web.api_server as srv

    monkeypatch.setattr(srv, "TRUSTED_PROXY_IPS", {"10.0.0.100"})
    req = _make_request("10.0.0.100", xff="::1")
    assert srv.resolve_client_ip(req) == "::1"
