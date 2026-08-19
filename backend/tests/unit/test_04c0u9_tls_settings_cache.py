"""The break-glass toggle must reach the process it has to affect (BLOCK 1).

Bead enhancedchannelmanager-04c0u.9 remediation.

Why this file exists at all: every test written for 04c0u.9 patched
``auth.routes.get_tls_settings`` directly, which bypasses the cache layer
entirely. Sixteen tests passed while the UI control was inert, because the
defect lives *between* the stored setting and the policy function, not in
either of them.

The topology that produced it:

1. ``main.py`` reads TLS settings at startup, so the MAIN process — the one
   serving the plain-HTTP listener on 6100 — memoized ``allow_http_session_
   cookies=False``.
2. With TLS active, TLS Settings is only reachable over HTTPS.
3. ``POST /api/tls/configure`` is not in ``tls/subprocess_proxy._FORWARD_ALLOWLIST``,
   so it is served INSIDE the HTTPS subprocess.
4. The save therefore updated the SUBPROCESS's copy.
5. Nothing invalidated the main process's copy, so the next login on 6100 still
   saw ``False``.

So these tests drive the real ``tls.settings`` module against a real file, and
the policy tests below call ``_auth_cookie_secure`` with ``get_tls_settings``
UNPATCHED.
"""

import json

import pytest
from starlette.requests import Request

import auth.routes as auth_routes
import tls.settings as tls_settings_module
from auth.routes import _auth_cookie_secure
from tls.settings import (
    clear_tls_settings_cache,
    get_tls_settings,
    save_tls_settings,
    tls_settings_load_failed,
)


@pytest.fixture
def config_root(tmp_path, monkeypatch):
    """Relocate ``tls_settings.json`` and the TLS key directory into tmp_path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    tls_dir = config_dir / "tls"
    tls_dir.mkdir()

    monkeypatch.setattr(tls_settings_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(tls_settings_module, "TLS_CONFIG_FILE", config_dir / "tls_settings.json")
    monkeypatch.setattr(tls_settings_module, "TLS_DIR", tls_dir)
    # ``auth.routes`` binds TLS_DIR by value at import, as every other consumer
    # in tls/ does.
    monkeypatch.setattr(auth_routes, "TLS_DIR", tls_dir)

    clear_tls_settings_cache()
    yield config_dir
    clear_tls_settings_cache()


def _issue_certificate(config_root):
    tls_dir = config_root / "tls"
    (tls_dir / "cert.pem").write_text("certificate fixture")
    (tls_dir / "key.pem").write_text("private-key fixture")


def _write_settings_as_another_process(config_root, **fields):
    """Write tls_settings.json the way the HTTPS subprocess would.

    Deliberately a plain file write and NOT ``save_tls_settings``: the whole
    point is that the writer is a DIFFERENT process, so nothing in this
    interpreter is told the file changed.
    """
    payload = {"enabled": False, "mode": "manual", "allow_http_session_cookies": False}
    payload.update(fields)
    (config_root / "tls_settings.json").write_text(json.dumps(payload))


def _http_request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "scheme": "http",
        "server": ("ecm.test", 6100),
        "client": ("192.0.2.10", 51234),
        "headers": [],
    })


def test_a_write_from_another_process_is_visible_without_any_invalidation(config_root):
    _write_settings_as_another_process(config_root, enabled=True)
    assert get_tls_settings().allow_http_session_cookies is False

    _write_settings_as_another_process(
        config_root, enabled=True, allow_http_session_cookies=True
    )

    assert get_tls_settings().allow_http_session_cookies is True


def test_break_glass_saved_over_https_reaches_the_plain_http_listener(config_root):
    """The end-to-end BLOCK 1 scenario, with ``get_tls_settings`` UNPATCHED."""
    _issue_certificate(config_root)
    _write_settings_as_another_process(config_root, enabled=True)

    # Main process warms its cache at startup, exactly as main.py does.
    assert get_tls_settings().enabled is True
    assert _auth_cookie_secure(_http_request()) is True

    # Admin ticks the box in TLS Settings. That save is served by the HTTPS
    # subprocess, so nothing in THIS process is notified.
    _write_settings_as_another_process(
        config_root, enabled=True, allow_http_session_cookies=True
    )

    assert _auth_cookie_secure(_http_request()) is False

    # ...and untickng it re-protects the listener, again with no invalidation.
    _write_settings_as_another_process(
        config_root, enabled=True, allow_http_session_cookies=False
    )

    assert _auth_cookie_secure(_http_request()) is True


def test_a_local_save_does_not_force_a_reload_but_stays_correct(config_root):
    _write_settings_as_another_process(config_root, enabled=True)
    settings = get_tls_settings()

    settings.allow_http_session_cookies = True
    assert save_tls_settings(settings) is True

    assert get_tls_settings().allow_http_session_cookies is True
    assert json.loads((config_root / "tls_settings.json").read_text())[
        "allow_http_session_cookies"
    ] is True


def test_a_deleted_config_file_falls_back_to_defaults(config_root):
    _write_settings_as_another_process(config_root, enabled=True)
    assert get_tls_settings().enabled is True

    (config_root / "tls_settings.json").unlink()

    assert get_tls_settings().enabled is False
    assert tls_settings_load_failed() is False


def test_an_unparseable_config_file_is_reported_as_a_load_failure(config_root):
    (config_root / "tls_settings.json").write_text("{ this is not json")

    settings = get_tls_settings()

    assert settings.enabled is False
    assert tls_settings_load_failed() is True


def test_a_load_failure_makes_the_http_listener_fail_closed(config_root):
    """Fail closed: unknown transport is treated as protected, not as "off"."""
    _issue_certificate(config_root)
    (config_root / "tls_settings.json").write_text("{ this is not json")

    assert _auth_cookie_secure(_http_request()) is True


def test_repairing_the_config_file_clears_the_load_failure(config_root):
    (config_root / "tls_settings.json").write_text("{ this is not json")
    assert get_tls_settings().enabled is False
    assert tls_settings_load_failed() is True

    _write_settings_as_another_process(config_root, enabled=True)

    assert get_tls_settings().enabled is True
    assert tls_settings_load_failed() is False
