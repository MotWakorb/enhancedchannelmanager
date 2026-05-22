"""Tests for the dedicated OAuth signing secret (bead buiqr.3, ADR-009 §3).

The OAuth access-token signing secret is DELIBERATELY separate from ECM's
user-session ``jwt.secret_key`` (auth_settings.json). This isolation is the
blast-radius control (threat model SR1): the MCP Resource Server reads only this
dedicated secret for offline verification, so a compromised MCP container can
forge MCP-scope tokens but NOT ECM admin sessions. These tests pin that the
secret is generated, persisted, idempotent, distinct from the session key, and
redacted from backups.
"""
import config


def test_generates_and_persists_when_absent(monkeypatch):
    """First use auto-generates a high-entropy secret and persists it."""
    settings = config.DispatcharrSettings()
    assert settings.mcp_oauth_signing_secret == ""
    saved = {}
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "save_settings", lambda s: saved.setdefault("s", s))

    secret = config.get_or_create_oauth_signing_secret()

    assert secret and len(secret) >= 32
    assert settings.mcp_oauth_signing_secret == secret
    assert saved.get("s") is settings  # persisted exactly once


def test_idempotent_when_present(monkeypatch):
    """A present secret is returned unchanged and NOT re-persisted."""
    settings = config.DispatcharrSettings(mcp_oauth_signing_secret="already-set-secret")
    save_calls = []
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "save_settings", lambda s: save_calls.append(s))

    secret = config.get_or_create_oauth_signing_secret()

    assert secret == "already-set-secret"
    assert save_calls == []  # no churn / no re-generation


def test_distinct_from_user_session_jwt_secret(monkeypatch):
    """SR1 — the OAuth secret must NEVER be ECM's user-session jwt.secret_key."""
    from auth.settings import get_jwt_secret_key

    settings = config.DispatcharrSettings()
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(config, "save_settings", lambda s: None)

    oauth_secret = config.get_or_create_oauth_signing_secret()

    assert oauth_secret != get_jwt_secret_key()


def test_oauth_signing_secret_is_credential_class():
    """ID5/SR1 — the secret must be redacted from backup exports."""
    from routers.backup import _SETTINGS_CREDENTIAL_FIELDS

    assert "mcp_oauth_signing_secret" in _SETTINGS_CREDENTIAL_FIELDS


def test_oauth_allow_insecure_defaults_false():
    """bd-buiqr.5 — the HTTP-posture flag defaults to the SAFE (fail-closed) value.

    ADR-009 §4: discovery is off on plain-HTTP non-loopback deploys unless the
    operator explicitly opts in. A True default would silently expose the OAuth
    surface over cleartext (threat model HT1), so the default must be False.
    """
    assert config.DispatcharrSettings().oauth_allow_insecure is False
