"""Tests for the SECURITY-CRITICAL remote-client factory (bead
enhancedchannelmanager-1t3al, epic i39wu).

The module under test (``tasks.dbas_sync_client``) is the seam that makes
cross-instance sync reuse the DBAS orchestrator: it builds a ``DispatcharrClient``
pointed at a ``SyncTarget`` row (Dispatcharr-B) instead of the local
``get_client()``. It is the genuine SSRF + Fernet + credential-freshness gate for
the new outbound path (threat model §11 Addendum D rows D1/D5/D6).

Covered:
* ``make_remote_client`` decrypts the Fernet credentials at CALL time and builds a
  client pointed at ``base_url`` (D5 — never log plaintext).
* a ``base_url`` resolving to a denied IP (169.254.169.254 / loopback in
  public-only) is REJECTED by the factory's SSRF gate at construct time, and —
  the load-bearing part — on EVERY outbound request via the pinned transport (D1).
* the credential-freshness helper aborts on version-mismatch / token_revoked_at /
  disabled / missing, and proceeds otherwise (D5, mirrors dbas_backup
  ``_check_credential_freshness``).
* ``insecure=true`` skips TLS verify AND the per-cycle audit helper writes a
  ``category='sync_outbound'`` journal row with ``tls_verified=false`` (D6).
* credentials never appear in logs.
"""
from __future__ import annotations

import ipaddress
import logging
from unittest.mock import patch

import httpx
import pytest

from cloud_storage.crypto import encrypt_credentials, reset_key_cache
from config import DispatcharrSettings
from export_models import SyncTarget
from security.ssrf import ResolvedTarget, SSRFError, SSRFMode

import tasks.dbas_sync_client as sync_client
from tasks.dbas_sync_client import (
    SyncCredentialError,
    make_remote_client,
    sync_freshness_reason,
    audit_insecure_cycle,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------
def _make_target(test_session, **overrides) -> SyncTarget:
    creds = overrides.pop("creds", {"auth_method": "api_key", "api_key": "SECRET-KEY-1234"})
    fields = {
        "name": overrides.pop("name", "sync-b"),
        "base_url": overrides.pop("base_url", "https://dispatcharr-b.example"),
        "credentials": encrypt_credentials(creds),
        "enabled": overrides.pop("enabled", True),
        "insecure": overrides.pop("insecure", False),
    }
    fields.update(overrides)
    target = SyncTarget(**fields)
    test_session.add(target)
    test_session.commit()
    test_session.refresh(target)
    return target


@pytest.fixture(autouse=True)
def _reset_crypto_key():
    # Ensure a Fernet key exists / is fresh for the test config dir.
    reset_key_cache()
    yield
    reset_key_cache()


def _resolved(ip="93.184.216.34", host="dispatcharr-b.example", scheme="https", port=443):
    return ResolvedTarget(
        scheme=scheme, hostname=host, port=port,
        ip=ipaddress.ip_address(ip), url=f"{scheme}://{host}:{port}",
    )


# ---------------------------------------------------------------------------
# make_remote_client — Fernet decrypt + build
# ---------------------------------------------------------------------------
class TestMakeRemoteClient:
    def test_decrypts_credentials_and_points_at_base_url(self, test_session):
        target = _make_target(
            test_session,
            base_url="https://dispatcharr-b.example",
            creds={"auth_method": "api_key", "api_key": "SECRET-KEY-1234"},
        )
        with patch.object(sync_client, "validate_outbound_url", return_value=_resolved()):
            client = make_remote_client(target)
        assert client.base_url == "https://dispatcharr-b.example"
        assert isinstance(client.settings, DispatcharrSettings)
        assert client.settings.auth_method == "api_key"
        # The decrypted key reached the settings (canonical field).
        assert client.settings.dispatcharr_api_key == "SECRET-KEY-1234"

    def test_password_auth_credentials_map_to_settings(self, test_session):
        target = _make_target(
            test_session,
            creds={"auth_method": "password", "username": "admin", "password": "pw-secret"},
        )
        with patch.object(sync_client, "validate_outbound_url", return_value=_resolved()):
            client = make_remote_client(target)
        assert client.settings.auth_method == "password"
        assert client.settings.username == "admin"
        assert client.settings.password == "pw-secret"

    def test_undecryptable_credentials_fail_closed(self, test_session):
        target = _make_target(test_session)
        target.credentials = "not-a-valid-fernet-token"
        test_session.commit()
        with patch.object(sync_client, "validate_outbound_url", return_value=_resolved()):
            with pytest.raises(SyncCredentialError):
                make_remote_client(target)

    def test_credentials_never_logged(self, test_session, caplog):
        secret = "SUPER-SECRET-TOKEN-XYZ"
        target = _make_target(
            test_session, creds={"auth_method": "api_key", "api_key": secret}
        )
        with caplog.at_level(logging.DEBUG):
            with patch.object(sync_client, "validate_outbound_url", return_value=_resolved()):
                make_remote_client(target)
        assert secret not in caplog.text


# ---------------------------------------------------------------------------
# SSRF gate — at construct time AND on every request (the load-bearing AC)
# ---------------------------------------------------------------------------
class TestSSRFGate:
    def test_construct_time_rejects_denied_base_url(self, test_session):
        target = _make_target(test_session, base_url="http://169.254.169.254/latest")
        # The real validator denies IMDS in both modes; do not patch it here.
        with pytest.raises(SSRFError):
            make_remote_client(target)

    @pytest.mark.asyncio
    async def test_every_request_routes_through_chokepoint(self, test_session):
        """The pinned transport MUST call validate_outbound_url on each request
        and connect by the validated IP — not just once at construct time."""
        target = _make_target(test_session, base_url="https://dispatcharr-b.example")

        captured = {"validate_calls": 0, "connect_host": None}

        def _fake_validate(url, mode):
            captured["validate_calls"] += 1
            return _resolved(ip="93.184.216.34")

        async def _fake_inner_handle(request):
            # Record the host the socket would connect to (the rewritten URL).
            captured["connect_host"] = request.url.host
            return httpx.Response(200, json={"ok": True})

        with patch.object(sync_client, "validate_outbound_url", side_effect=_fake_validate):
            client = make_remote_client(target)
            # Patch the transport's *inner* real network handler so we never
            # open a socket but still exercise the pinning wrapper.
            with patch.object(
                client._client._transport._inner, "handle_async_request",
                side_effect=_fake_inner_handle,
            ):
                resp = await client._client.get("https://dispatcharr-b.example/api/x/")
        assert resp.status_code == 200
        # validate ran at request time (>=1; construct-time validate may add one).
        assert captured["validate_calls"] >= 1
        # The connection was rewritten to the validated IP, not the hostname.
        assert captured["connect_host"] == "93.184.216.34"

    @pytest.mark.asyncio
    async def test_request_to_rebinding_host_is_rejected_at_request_time(self, test_session):
        """If the host resolves to a denied IP at REQUEST time (DNS rebinding),
        the pinned transport raises SSRFError — even if construct-time passed."""
        target = _make_target(test_session, base_url="https://dispatcharr-b.example")

        calls = {"n": 0}

        def _validate(url, mode):
            calls["n"] += 1
            if calls["n"] == 1:
                return _resolved(ip="93.184.216.34")  # construct-time OK
            raise SSRFError("rebound to denied address")  # request-time denied

        with patch.object(sync_client, "validate_outbound_url", side_effect=_validate):
            client = make_remote_client(target)
            with pytest.raises(SSRFError):
                await client._client.get("https://dispatcharr-b.example/api/x/")


# ---------------------------------------------------------------------------
# Credential-freshness helper (mirrors dbas_backup _check_credential_freshness)
# ---------------------------------------------------------------------------
class TestFreshness:
    def test_proceeds_when_fresh(self, test_session):
        target = _make_target(test_session)
        reason = sync_freshness_reason(
            test_session, target.id, captured_version=target.credential_version
        )
        assert reason is None

    def test_aborts_when_missing(self, test_session):
        reason = sync_freshness_reason(test_session, 999999, captured_version=1)
        assert reason is not None
        assert "no longer exists" in reason

    def test_aborts_when_disabled(self, test_session):
        target = _make_target(test_session, enabled=False)
        reason = sync_freshness_reason(
            test_session, target.id, captured_version=target.credential_version
        )
        assert reason is not None
        assert "disabled" in reason

    def test_aborts_when_revoked(self, test_session):
        from datetime import datetime

        target = _make_target(test_session)
        target.token_revoked_at = datetime.utcnow()
        test_session.commit()
        reason = sync_freshness_reason(
            test_session, target.id, captured_version=target.credential_version
        )
        assert reason is not None
        assert "revoked" in reason

    def test_aborts_when_version_rotated(self, test_session):
        target = _make_target(test_session)
        captured = target.credential_version
        # Rotate credentials -> version bumps (same-txn listener).
        target.credentials = encrypt_credentials({"auth_method": "api_key", "api_key": "new"})
        test_session.commit()
        test_session.refresh(target)
        assert target.credential_version != captured
        reason = sync_freshness_reason(test_session, target.id, captured_version=captured)
        assert reason is not None
        assert "rotated" in reason


# ---------------------------------------------------------------------------
# insecure=true — verify-skip wiring + per-cycle audit row (D6)
# ---------------------------------------------------------------------------
class TestInsecure:
    def test_insecure_target_skips_tls_verify(self, test_session):
        target = _make_target(test_session, insecure=True)
        with patch.object(sync_client, "validate_outbound_url", return_value=_resolved()):
            client = make_remote_client(target)
        # The pinned transport carries verify=False for an insecure target.
        assert client._client._transport._verify is False

    def test_secure_target_keeps_tls_verify(self, test_session):
        target = _make_target(test_session, insecure=False)
        with patch.object(sync_client, "validate_outbound_url", return_value=_resolved()):
            client = make_remote_client(target)
        assert client._client._transport._verify is True

    def test_audit_insecure_cycle_writes_sync_outbound_row(self, test_session):
        target = _make_target(test_session, insecure=True)
        with patch.object(sync_client, "journal") as mock_journal:
            audit_insecure_cycle(target.id, target.name)
        mock_journal.log_entry.assert_called_once()
        kwargs = mock_journal.log_entry.call_args.kwargs
        assert kwargs["category"] == "sync_outbound"
        assert kwargs["user_initiated"] is False
        assert "tls_verified=false" in kwargs["description"].lower()
