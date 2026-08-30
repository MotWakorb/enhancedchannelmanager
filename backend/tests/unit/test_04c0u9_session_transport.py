"""Session transport policy for bead enhancedchannelmanager-04c0u.9.

These prove the policy FUNCTION. The cache layer underneath it — which is what
carries an operator's break-glass decision from the HTTPS subprocess to the
plain-HTTP main process — is proved separately in
``test_04c0u9_tls_settings_cache.py``, deliberately WITHOUT patching
``auth.routes.get_tls_settings``.
"""

import logging
import re
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from starlette.requests import Request
from starlette.responses import Response

import auth.routes as auth_routes
from auth.routes import (
    _auth_cookie_secure,
    _clear_auth_cookies,
    _require_secure_session_transport,
    _reset_session_transport_log_state,
    _set_auth_cookies,
    break_glass_is_downgrading,
)
from tls.settings import break_glass_environment_override


def _request(scheme: str = "http", headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "scheme": scheme,
        "server": ("ecm.test", 6143 if scheme == "https" else 6100),
        "client": ("192.0.2.10", 51234),
        "headers": headers or [],
    })


def _tls(**overrides):
    values = {
        "enabled": False,
        "allow_http_session_cookies": False,
        "domain": "ecm.test",
        "https_port": 6143,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _rearm_warn_once():
    _reset_session_transport_log_state()
    yield
    _reset_session_transport_log_state()


@pytest.fixture
def issued_tls_dir(tmp_path, monkeypatch):
    """A TLS directory holding the key material the HTTPS listener starts from.

    ``auth.routes`` binds ``TLS_DIR`` by value at import (the convention every
    other consumer in ``tls/`` already follows), so the patch target is the
    name in ``auth.routes``, not ``tls.settings``.
    """
    tls_dir = tmp_path / "tls"
    tls_dir.mkdir()
    (tls_dir / "cert.pem").write_text("certificate fixture")
    (tls_dir / "key.pem").write_text("private-key fixture")
    monkeypatch.setattr(auth_routes, "TLS_DIR", tls_dir)
    return tls_dir


@pytest.fixture
def empty_tls_dir(tmp_path, monkeypatch):
    """A TLS directory with no key material: enabled-but-not-yet-issued."""
    tls_dir = tmp_path / "tls-empty"
    tls_dir.mkdir()
    monkeypatch.setattr(auth_routes, "TLS_DIR", tls_dir)
    return tls_dir


def _policy(tls, public_base_url="", load_failed=False):
    return (
        patch("auth.routes.get_tls_settings", return_value=tls),
        patch("auth.routes.get_public_base_url", return_value=public_base_url),
        patch("auth.routes.tls_settings_load_failed", return_value=load_failed),
    )


class _Policy:
    """Context manager bundling the three trusted-state patches."""

    def __init__(self, tls, public_base_url="", load_failed=False):
        self._patches = _policy(tls, public_base_url, load_failed)

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


# ---------------------------------------------------------------------------
# Cookie transport policy
# ---------------------------------------------------------------------------


def test_direct_https_sets_secure_session_cookies(empty_tls_dir):
    with _Policy(_tls()):
        assert _auth_cookie_secure(_request("https")) is True


def test_activated_ecm_tls_protects_cookies_even_on_http_listener(issued_tls_dir):
    with _Policy(_tls(enabled=True)):
        response = Response()
        _set_auth_cookies(response, "access", "refresh", _request("http"))

    cookies = response.headers.getlist("set-cookie")
    assert len(cookies) == 2
    assert all("Secure" in cookie for cookie in cookies)
    assert all("HttpOnly" in cookie for cookie in cookies)
    assert all("samesite=lax" in cookie.lower() for cookie in cookies)


def test_enabling_tls_before_certificate_exists_does_not_lock_out_http(empty_tls_dir):
    with _Policy(_tls(enabled=True)):
        assert _auth_cookie_secure(_request("http")) is False


def test_https_public_origin_protects_reverse_proxy_sessions(empty_tls_dir):
    with _Policy(_tls(), public_base_url="https://ecm.example.test"):
        assert _auth_cookie_secure(_request("http")) is True


def test_cookie_policy_reads_the_request_scope_scheme_not_a_forwarded_header(empty_tls_dir):
    """ECM adds no ``X-Forwarded-Proto`` trust of its own.

    NARROW CLAIM, and the narrowness is the point. This constructs the ASGI
    scope directly, which is DOWNSTREAM of uvicorn's ``ProxyHeadersMiddleware``
    — enabled by default (``proxy_headers=True``,
    ``forwarded_allow_ips`` defaulting to ``127.0.0.1``) and living outside this
    application, where it may itself rewrite ``scope['scheme']`` for a loopback
    client. So this test cannot and does not prove that a forged header is
    inert end to end; it proves that ECM's own policy code reads the scheme it
    was handed and never the header. See ``README.md`` and ``docs/api.md`` for
    the operator-facing statement of the same narrowed claim.
    """
    headers = [(b"x-forwarded-proto", b"https")]
    with _Policy(_tls()):
        assert _auth_cookie_secure(_request("http", headers)) is False


# ---------------------------------------------------------------------------
# Break-glass
# ---------------------------------------------------------------------------


def test_explicit_break_glass_allows_http_cookie_recovery(issued_tls_dir):
    with _Policy(_tls(enabled=True, allow_http_session_cookies=True)):
        assert _auth_cookie_secure(_request("http")) is False


def test_break_glass_does_not_weaken_real_https(issued_tls_dir):
    with _Policy(_tls(enabled=True, allow_http_session_cookies=True)):
        assert _auth_cookie_secure(_request("https")) is True


def test_environment_break_glass_supports_locked_out_operator(monkeypatch, issued_tls_dir):
    monkeypatch.setenv("ECM_ALLOW_HTTP_SESSION_COOKIES", "true")
    with _Policy(_tls(enabled=True)):
        assert _auth_cookie_secure(_request("http")) is False


def test_stored_break_glass_beats_a_configured_https_public_origin(issued_tls_dir):
    """Both escape hatches must survive an https ``public_base_url``.

    The ``public_base_url`` check used to return True BEFORE break-glass was
    ever evaluated, so on a reverse-proxy deployment — the exact deployment
    whose operators were told by bead qsqfv to configure that value — the
    documented recovery did nothing, in either of its two forms.
    """
    with _Policy(
        _tls(enabled=True, allow_http_session_cookies=True),
        public_base_url="https://ecm.example.test",
    ):
        assert _auth_cookie_secure(_request("http")) is False


def test_environment_break_glass_beats_a_configured_https_public_origin(
    monkeypatch, issued_tls_dir
):
    monkeypatch.setenv("ECM_ALLOW_HTTP_SESSION_COOKIES", "true")
    with _Policy(_tls(enabled=True), public_base_url="https://ecm.example.test"):
        assert _auth_cookie_secure(_request("http")) is False


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "  ", "False", "disabled"])
def test_non_affirmative_environment_values_keep_protection_on(
    monkeypatch, issued_tls_dir, value
):
    """``ECM_ALLOW_HTTP_SESSION_COOKIES=false`` must NOT open the hatch.

    ``docker-compose.yml`` ships
    ``ECM_ALLOW_HTTP_SESSION_COOKIES=${ECM_ALLOW_HTTP_SESSION_COOKIES:-false}``,
    so on every compose deployment the literal string ``false`` is present in
    the container environment. A truthiness parse (``.strip() != ""``) would
    therefore disable session protection on every default install while the
    only positive test — ``"true"`` — kept passing. This is the negative half
    that kills that mutation.
    """
    monkeypatch.setenv("ECM_ALLOW_HTTP_SESSION_COOKIES", value)
    assert break_glass_environment_override() is False
    with _Policy(_tls(enabled=True)):
        assert _auth_cookie_secure(_request("http")) is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_affirmative_environment_values_open_the_hatch(monkeypatch, issued_tls_dir, value):
    monkeypatch.setenv("ECM_ALLOW_HTTP_SESSION_COOKIES", value)
    assert break_glass_environment_override() is True
    with _Policy(_tls(enabled=True)):
        assert _auth_cookie_secure(_request("http")) is False


def test_break_glass_warns_once_per_process_when_it_actually_downgrades(
    issued_tls_dir, caplog
):
    with _Policy(_tls(enabled=True, allow_http_session_cookies=True)):
        with caplog.at_level(logging.WARNING, logger="auth.routes"):
            _auth_cookie_secure(_request("http"))
            _auth_cookie_secure(_request("http"))

    downgrades = [r for r in caplog.records if "Break-glass is ON" in r.getMessage()]
    assert len(downgrades) == 1, [r.getMessage() for r in caplog.records]


def test_break_glass_is_silent_when_it_suppresses_nothing(empty_tls_dir, caplog):
    """No TLS and no https origin: cookies would be non-Secure regardless."""
    with _Policy(_tls(allow_http_session_cookies=True)):
        with caplog.at_level(logging.WARNING, logger="auth.routes"):
            _auth_cookie_secure(_request("http"))

    assert not [r for r in caplog.records if "Break-glass is ON" in r.getMessage()]


@pytest.mark.parametrize(
    "public_base_url, enabled, expected",
    [
        # A reverse-proxy deployment: ECM's own TLS is OFF, the operator
        # declared an https origin, and break-glass is downgrading the cookies
        # that origin would otherwise have protected.
        ("https://ecm.example.test", False, True),
        # ECM's own TLS.
        ("", True, True),
        # Neither: the cookies would be non-Secure with or without the hatch,
        # so there is nothing for the hatch to be blamed for.
        ("", False, False),
    ],
)
def test_the_downgrade_verdict_is_the_same_one_the_warning_uses(
    tmp_path, monkeypatch, caplog, public_base_url, enabled, expected
):
    """``GET /api/tls/status`` and the WARN must not be able to disagree.

    They did. The status field required ECM's OWN TLS
    (``enabled and has_certificate()``) while the WARN accepts an https
    ``public_base_url`` too — so on a reverse-proxy deployment with break-glass
    on, the log said the hatch was downgrading live sessions, the API said
    ``session_cookies_plaintext: false``, the banner never rendered and the UI
    looked unchecked. That is exactly the population the break-glass reorder in
    ``_session_transport`` was made to serve.
    """
    tls_dir = tmp_path / "tls"
    tls_dir.mkdir()
    if enabled:
        (tls_dir / "cert.pem").write_text("certificate fixture")
        (tls_dir / "key.pem").write_text("private-key fixture")
    monkeypatch.setattr(auth_routes, "TLS_DIR", tls_dir)

    with _Policy(
        _tls(enabled=enabled, allow_http_session_cookies=True),
        public_base_url=public_base_url,
    ):
        with caplog.at_level(logging.WARNING, logger="auth.routes"):
            assert _auth_cookie_secure(_request("http")) is False
            verdict = break_glass_is_downgrading()

    warned = bool([r for r in caplog.records if "Break-glass is ON" in r.getMessage()])
    assert verdict is expected
    assert warned is expected


# ---------------------------------------------------------------------------
# Fail-closed and single-source-of-truth for "the certificate exists"
# ---------------------------------------------------------------------------


def test_unreadable_tls_config_fails_closed(empty_tls_dir):
    """"We could not read the config" is not "TLS is off".

    ``load_tls_settings`` degrades to ``TLSSettings()`` (``enabled=False``) when
    ``tls_settings.json`` fails to parse, while the HTTPS listener may still be
    serving from key material already on disk. Reading that fallback as "TLS is
    off" would emit cleartext-replayable session cookies with only an ERROR log
    to mark it.
    """
    with _Policy(_tls(), load_failed=True):
        assert _auth_cookie_secure(_request("http")) is True


def test_certificate_presence_comes_from_the_directory_the_listener_starts_from(
    tmp_path, monkeypatch
):
    """``has_certificate()``, not ``tls_settings.cert_path`` / ``key_path``.

    ``tls/https_server.py`` starts uvicorn from ``CertificateStorage(TLS_DIR)``
    — the hard-coded ``cert.pem`` / ``key.pem``. Deciding cookie policy from the
    stored paths instead is a SECOND notion of "the certificate exists", and
    ``tls_settings.json`` is backup-restored, so the two can disagree. Here the
    stored paths point at real files while the listener's directory is empty:
    the listener is not serving, so cookie policy must not claim it is.
    """
    stray = tmp_path / "stray"
    stray.mkdir()
    (stray / "cert.pem").write_text("certificate fixture")
    (stray / "key.pem").write_text("private-key fixture")
    empty = tmp_path / "tls"
    empty.mkdir()
    monkeypatch.setattr(auth_routes, "TLS_DIR", empty)

    tls = _tls(
        enabled=True,
        cert_path=str(stray / "cert.pem"),
        key_path=str(stray / "key.pem"),
    )
    with _Policy(tls):
        assert _auth_cookie_secure(_request("http")) is False


# ---------------------------------------------------------------------------
# Refusing to mint a session over cleartext
# ---------------------------------------------------------------------------


def _sign_in_url(detail: str):
    """The recovery address the refusal message directs the operator to.

    Extracted and parsed rather than substring-matched. ``"https://ecm.test:6143"
    in detail`` also passes for ``https://ecm.test:61430`` and for
    ``https://ecm.test:6143.attacker.test`` — both proven against this test —
    so it cannot fail in the way the assertion claims, and the host and the
    port are exactly what it exists to pin. Callers compare the parsed scheme
    and netloc, which is the precise origin check CodeQL's
    ``py/incomplete-url-substring-sanitization`` asks for; netloc rather than
    ``hostname``/``port`` so a malformed authority fails as an assertion
    instead of a ``ValueError`` out of :func:`urllib.parse.urlsplit`.
    """
    match = re.search(r"Sign in at (\S+) instead\.", detail)
    assert match is not None, f"no sign-in directive in refusal message: {detail!r}"
    return urlsplit(match.group(1))


def test_plaintext_session_is_refused_when_ecm_terminates_tls(issued_tls_dir):
    with _Policy(_tls(enabled=True)):
        with pytest.raises(Exception) as excinfo:
            _require_secure_session_transport(_request("http"))
    exc = excinfo.value
    assert exc.status_code == 403
    # 403 and not 401: the SPA's fetchJson treats 401 as "refresh and retry",
    # which would swallow the message.
    sign_in = _sign_in_url(exc.detail)
    assert (sign_in.scheme, sign_in.netloc) == ("https", "ecm.test:6143")
    assert "ECM_ALLOW_HTTP_SESSION_COOKIES" in exc.detail


@pytest.mark.parametrize(
    "scheme, public_base_url, break_glass",
    [
        ("https", "", False),
        ("http", "https://ecm.example.test", False),
        ("http", "", True),
    ],
)
def test_plaintext_refusal_predicate_stays_narrow(
    issued_tls_dir, scheme, public_base_url, break_glass
):
    """Only fires where there is no legitimate plaintext client.

    Real TLS, a reverse proxy that declared its https origin, and an open
    break-glass hatch each mean a plaintext request here is expected.
    """
    with _Policy(
        _tls(enabled=True, allow_http_session_cookies=break_glass),
        public_base_url=public_base_url,
    ):
        _require_secure_session_transport(_request(scheme))


def test_plaintext_refusal_does_not_fire_without_ecm_tls(empty_tls_dir):
    with _Policy(_tls()):
        _require_secure_session_transport(_request("http"))


def test_an_unreadable_settings_file_does_not_lock_login_out(empty_tls_dir):
    """A filesystem fault must not become a permanent, total login lockout.

    ``tls_settings.json`` failing to parse makes ``_ecm_terminates_tls`` answer
    True — correctly, for the cookie ATTRIBUTE, which must fail closed. Wiring
    the REFUSAL to the same answer was the disproportionate half: on an instance
    that never enabled TLS there is no HTTPS listener to fall back to, so a
    truncated or zero-byte file — one disk-full or one container kill during a
    settings save away — turned into a 403 on login, dispatcharr_login AND
    refresh, with the stored break-glass flag unreachable behind the same
    failure (``load_tls_settings`` degrades to defaults) and only
    ``ECM_ALLOW_HTTP_SESSION_COOKIES`` plus a restart to recover.
    """
    with _Policy(_tls(), load_failed=True):
        # Still fails closed on the cookie attribute.
        assert _auth_cookie_secure(_request("http")) is True
        # ...and still mints the session rather than 403ing.
        _require_secure_session_transport(_request("http"))


def test_an_unreadable_settings_file_still_refuses_when_key_material_exists(
    issued_tls_dir,
):
    """The refusal needs positive evidence, and cert.pem/key.pem are evidence.

    ``tls/https_server.py`` spawns uvicorn on exactly
    ``CertificateStorage(TLS_DIR).has_certificate()``, so a corrupt settings
    file next to real key material is the case where the listener probably IS
    serving — and the pre-activation replay this refusal exists to stop is
    live.
    """
    with _Policy(_tls(), load_failed=True):
        with pytest.raises(Exception) as excinfo:
            _require_secure_session_transport(_request("http"))
    assert excinfo.value.status_code == 403


def test_a_running_https_listener_is_evidence_on_its_own(empty_tls_dir):
    """Second positive signal, for key material deleted out from under a live
    listener."""
    with _Policy(_tls(), load_failed=True), patch(
        "auth.routes.https_server_manager", new=SimpleNamespace(is_running=True)
    ):
        with pytest.raises(Exception) as excinfo:
            _require_secure_session_transport(_request("http"))
    assert excinfo.value.status_code == 403


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("issued", [False, True])
def test_the_activation_and_cookie_policy_predicates_agree(
    tmp_path, monkeypatch, enabled, issued
):
    """Anti-drift for the two copies of "ECM terminates TLS".

    ``tls.routes._tls_termination_active`` decides when an activation happened
    and therefore when to revoke pre-activation sessions;
    ``auth.routes._ecm_terminates_tls`` decides whether the cookies those
    sessions carry must be ``Secure``. If they drift, sessions survive an
    activation they should not have survived, or are cut on a save that changed
    nothing. Their one intended difference — the fail-closed leg for an
    unreadable ``tls_settings.json`` — is held out here on purpose.
    """
    import tls.routes as tls_routes

    tls_dir = tmp_path / "tls"
    tls_dir.mkdir()
    if issued:
        (tls_dir / "cert.pem").write_text("certificate fixture")
        (tls_dir / "key.pem").write_text("private-key fixture")
    monkeypatch.setattr(auth_routes, "TLS_DIR", tls_dir)
    monkeypatch.setattr(tls_routes, "TLS_DIR", tls_dir)

    tls = _tls(enabled=enabled)
    with patch("auth.routes.get_tls_settings", return_value=tls), \
            patch("tls.routes.get_tls_settings", return_value=tls), \
            patch("auth.routes.tls_settings_load_failed", return_value=False):
        assert tls_routes._tls_termination_active() is auth_routes._ecm_terminates_tls()
        assert tls_routes._tls_termination_active() is (enabled and issued)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def test_logout_deletion_mirrors_the_attributes_the_cookies_were_issued_with(
    issued_tls_dir,
):
    """RFC 6265bis 8.6: a non-secure origin cannot clear a ``Secure`` cookie.

    Deletion matches on (name, domain, path) so the mismatch is latent today,
    but a deletion that does not mirror its issue-time attributes is a trap for
    the next change here — and nothing covered logout cookie attributes at all.
    """
    response = Response()
    with _Policy(_tls(enabled=True)):
        _clear_auth_cookies(response, _request("http"))

    cookies = response.headers.getlist("set-cookie")
    assert len(cookies) == 2
    for cookie in cookies:
        assert "Secure" in cookie, cookie
        assert "HttpOnly" in cookie, cookie
        assert "samesite=lax" in cookie.lower(), cookie
        assert "Max-Age=0" in cookie, cookie


def test_logout_deletion_is_not_secure_when_issue_time_policy_is_not(empty_tls_dir):
    response = Response()
    with _Policy(_tls()):
        _clear_auth_cookies(response, _request("http"))

    for cookie in response.headers.getlist("set-cookie"):
        assert "Secure" not in cookie, cookie
