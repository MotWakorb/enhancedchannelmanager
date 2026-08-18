"""Session transport policy for bead enhancedchannelmanager-04c0u.9."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from starlette.requests import Request
from starlette.responses import Response

from auth.routes import _auth_cookie_secure, _set_auth_cookies


def _request(scheme: str = "http", headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "scheme": scheme,
        "server": ("ecm.test", 6143 if scheme == "https" else 6100),
        "headers": headers or [],
    })


def _tls(**overrides):
    values = {
        "enabled": False,
        "allow_http_session_cookies": False,
        "cert_path": "/does-not-exist/cert.pem",
        "key_path": "/does-not-exist/key.pem",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_direct_https_sets_secure_session_cookies():
    with patch("auth.routes.get_tls_settings", return_value=_tls()), \
         patch("auth.routes.get_public_base_url", return_value=""):
        assert _auth_cookie_secure(_request("https")) is True


def test_activated_ecm_tls_protects_cookies_even_on_http_listener(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("certificate fixture")
    key.write_text("private-key fixture")
    tls = _tls(enabled=True, cert_path=str(cert), key_path=str(key))
    with patch("auth.routes.get_tls_settings", return_value=tls), \
         patch("auth.routes.get_public_base_url", return_value=""):
        response = Response()
        _set_auth_cookies(response, "access", "refresh", _request("http"))

    cookies = response.headers.getlist("set-cookie")
    assert len(cookies) == 2
    assert all("Secure" in cookie for cookie in cookies)
    assert all("HttpOnly" in cookie for cookie in cookies)
    assert all("SameSite=lax" in cookie for cookie in cookies)


def test_enabling_tls_before_certificate_exists_does_not_lock_out_http():
    with patch("auth.routes.get_tls_settings", return_value=_tls(enabled=True)), \
         patch("auth.routes.get_public_base_url", return_value=""):
        assert _auth_cookie_secure(_request("http")) is False


def test_https_public_origin_protects_reverse_proxy_sessions():
    with patch("auth.routes.get_tls_settings", return_value=_tls()), \
         patch("auth.routes.get_public_base_url", return_value="https://ecm.example.test"):
        assert _auth_cookie_secure(_request("http")) is True


def test_forged_forwarded_proto_is_not_a_trust_signal():
    headers = [(b"x-forwarded-proto", b"https")]
    with patch("auth.routes.get_tls_settings", return_value=_tls()), \
         patch("auth.routes.get_public_base_url", return_value=""):
        assert _auth_cookie_secure(_request("http", headers)) is False


def test_explicit_break_glass_allows_http_cookie_recovery(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.touch()
    key.touch()
    with patch(
        "auth.routes.get_tls_settings",
        return_value=_tls(
            enabled=True,
            allow_http_session_cookies=True,
            cert_path=str(cert),
            key_path=str(key),
        ),
    ), patch("auth.routes.get_public_base_url", return_value=""):
        assert _auth_cookie_secure(_request("http")) is False


def test_break_glass_does_not_weaken_real_https():
    with patch(
        "auth.routes.get_tls_settings",
        return_value=_tls(enabled=True, allow_http_session_cookies=True),
    ), patch("auth.routes.get_public_base_url", return_value=""):
        assert _auth_cookie_secure(_request("https")) is True


def test_environment_break_glass_supports_locked_out_operator(monkeypatch, tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.touch()
    key.touch()
    monkeypatch.setenv("ECM_ALLOW_HTTP_SESSION_COOKIES", "true")
    with patch(
        "auth.routes.get_tls_settings",
        return_value=_tls(enabled=True, cert_path=str(cert), key_path=str(key)),
    ), \
         patch("auth.routes.get_public_base_url", return_value=""):
        assert _auth_cookie_secure(_request("http")) is False
