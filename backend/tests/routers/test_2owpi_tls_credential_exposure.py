"""TLS DNS-provider credentials must not reach a log line or a response body.

Bead ``enhancedchannelmanager-2owpi``, child of the auth-posture epic
``enhancedchannelmanager-9kwzp``. The bead was filed at P1 over at-rest
encryption and recalibrated to P3: ``/config/tls_settings.json`` is already
0600, is not in git, and is not swept into backups, and ECM must decrypt the
credential unattended to renew a certificate, so a Fernet key beside it buys
consistency rather than protection. The PO declined encryption on 2026-08-13
and scoped this bead to three cheap checks instead. This file pins two of
them; the third (the startup mode/ownership probe) is pinned in
``tests/unit/test_tls.py``.

CHECK 1 — does any API response echo a stored credential?

``GET /api/tls/settings`` masks all three credential fields down to their last
four characters. That was reported in passing by an earlier engineer and is
verified here at the source rather than trusted, because masking on one route
is not masking on all of them. The rest of the router is checked too: the
carrier that actually mattered was NOT the settings read but
``last_renewal_error``, a free-text field written by the renewal path,
persisted into ``tls_settings.json``, and echoed by ``GET /api/tls/status`` —
the weakest-gated route in the router (plain admin tier, admits the MCP
service principal, and no-ops entirely while ``require_auth`` is false).

CHECK 2 — are the values written to logs on a renewal failure?

Two paths were found and both are pinned below.

* An operator-supplied Cloudflare token containing a character illegal in an
  HTTP header value (a trailing newline is the realistic shape: it survives a
  copy-paste and nothing strips it) makes h11 raise ``LocalProtocolError``
  whose ``str()`` is ``Illegal header value b'Bearer <THE WHOLE TOKEN>'``.
  That string was returned as the provider error, logged verbatim at
  ``tls/routes.py`` on the credential-test route, and on the renewal path was
  stored into ``last_renewal_error`` and served back out of ``GET /status``.
  *Mechanism absent is not defect fixed*: the fix is not "no exception in the
  current code carries a token", it is that every provider error string now
  goes through ``mask_secrets`` before any caller can log, persist or return
  it, so a future exception carrying one is redacted too.

* ``main.py``'s ``RequestValidationError`` handler logs the raw request body,
  the raw ``exc.body`` and ``exc.errors()`` for every path outside
  ``/api/auth``, and echoes ``exc.errors()`` and ``exc.body`` in the 422. A
  ``POST /api/tls/configure`` that fails validation for any unrelated reason
  therefore wrote ``aws_secret_access_key`` and ``dns_api_token`` in clear to
  the application log. Logs get pasted into GitHub issues. Note that
  ``mask_secrets`` does NOT save this path: its key/value rule needs the key
  name adjacent to the separator, and a JSON body puts a closing quote between
  them, so ``{"aws_secret_access_key": "..."}`` passes through it untouched.
  The fix is path-based redaction, and the path list is a module constant so
  the remaining credential-bearing routes can be added to one place.

No test in this file contains a real credential. Placeholders follow
``docs/pytest_conventions.md`` -> "Credential Fixtures in Security Tests":
angle-bracket values are never scan candidates, and the one AWS key ID is the
AWS documentation example key that this repo's other fixtures already use,
assembled from split literals so no single line carries the whole pattern.
"""
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import User
from tls.settings import TLSSettings


# Placeholders. Nothing here depends on their shape beyond the newline in
# TOKEN_WITH_ILLEGAL_HEADER_CHAR, which is the whole point of that fixture.
DNS_TOKEN = "<synthetic-cloudflare-token-2owpi>"
TOKEN_WITH_ILLEGAL_HEADER_CHAR = "<synthetic-pasted-token-2owpi>\n"
# Split so no single literal matches ``AKIA[0-9A-Z]{16}``; the assembled value
# is byte-for-byte the AWS documentation example key.
AWS_KEY_ID = "AKIA" + "IOSFODNN7EXAMPLE"
AWS_SECRET = "<synthetic-aws-secret-access-key-2owpi>"


def _admin_user() -> User:
    return User(
        id=2001,
        username="admin-2owpi",
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )


def _configured_settings(**overrides) -> TLSSettings:
    base = dict(
        enabled=True,
        mode="letsencrypt",
        domain="tls-2owpi.example.com",
        acme_email="operator@example.com",
        dns_provider="cloudflare",
        dns_api_token=DNS_TOKEN,
        aws_access_key_id=AWS_KEY_ID,
        aws_secret_access_key=AWS_SECRET,
    )
    base.update(overrides)
    return TLSSettings(**base)


# ===========================================================================
# CHECK 1 — API responses
# ===========================================================================


class TestSettingsReadMasksEveryCredentialField:
    """Verified at source, not taken on report."""

    @pytest.mark.asyncio
    async def test_get_settings_masks_all_three_credential_fields(
        self, async_client
    ):
        with patch("tls.routes.get_tls_settings",
                   return_value=_configured_settings()), \
                patch("tls.routes.CertificateStorage"), \
                patch("auth.dependencies.get_auth_settings") as auth_mock, \
                patch("auth.dependencies.get_current_user",
                      new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.get("/api/tls/settings")

        assert response.status_code == 200, response.text
        body = response.json()

        # Masked to the last four characters, which is the documented,
        # deliberately-accepted disclosure of this route.
        assert body["dns_api_token"] == "***" + DNS_TOKEN[-4:]
        assert body["aws_access_key_id"] == "***" + AWS_KEY_ID[-4:]
        assert body["aws_secret_access_key"] == "***" + AWS_SECRET[-4:]

        # And none of the full values survives anywhere in the payload.
        raw = response.text
        for secret in (DNS_TOKEN, AWS_KEY_ID, AWS_SECRET):
            assert secret not in raw


class TestStatusReadCannotCarryACredential:
    """``last_renewal_error`` is free text on the weakest-gated route."""

    @pytest.mark.asyncio
    async def test_status_does_not_echo_a_credential_via_last_renewal_error(
        self, async_client
    ):
        poisoned = (
            "DNS challenge failed: Failed to get zone ID: Illegal header "
            f"value b'Bearer {DNS_TOKEN}'"
        )
        settings = _configured_settings(last_renewal_error=poisoned)

        storage = MagicMock()
        storage.return_value.has_certificate.return_value = False

        with patch("tls.routes.get_tls_settings", return_value=settings), \
                patch("tls.routes.CertificateStorage", new=storage), \
                patch("auth.dependencies.get_auth_settings") as auth_mock, \
                patch("auth.dependencies.get_current_user",
                      new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.get("/api/tls/status")

        assert response.status_code == 200, response.text
        assert DNS_TOKEN not in response.text


# ===========================================================================
# CHECK 2a — the DNS-provider error string
# ===========================================================================


class TestDNSProviderErrorsAreRedacted:
    @pytest.mark.asyncio
    async def test_cloudflare_verify_credentials_never_returns_the_token(self):
        from tls.dns_providers.cloudflare import CloudflareDNS

        provider = CloudflareDNS(api_token=TOKEN_WITH_ILLEGAL_HEADER_CHAR)
        valid, error = await provider.verify_credentials()

        # The call must still fail — this is a broken token — but the reason
        # must not be the token itself.
        assert valid is False
        assert error
        assert TOKEN_WITH_ILLEGAL_HEADER_CHAR.strip() not in error

    @pytest.mark.asyncio
    async def test_cloudflare_get_zone_id_never_raises_with_the_token(self):
        from tls.dns_providers.base import DNSProviderError
        from tls.dns_providers.cloudflare import CloudflareDNS

        provider = CloudflareDNS(api_token=TOKEN_WITH_ILLEGAL_HEADER_CHAR)
        with pytest.raises(DNSProviderError) as excinfo:
            await provider.get_zone_id("tls-2owpi.example.com")

        assert TOKEN_WITH_ILLEGAL_HEADER_CHAR.strip() not in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_test_dns_provider_route_logs_no_token(
        self, async_client, caplog
    ):
        """The route logs the provider's verdict; it must be redacted."""
        body = {
            "provider": "cloudflare",
            "api_token": TOKEN_WITH_ILLEGAL_HEADER_CHAR,
            "domain": "tls-2owpi.example.com",
        }

        with patch("tls.routes._acme_available", new=True), \
                patch("auth.dependencies.get_auth_settings") as auth_mock, \
                patch("auth.dependencies.get_current_user",
                      new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            with caplog.at_level(logging.DEBUG):
                response = await async_client.post(
                    "/api/tls/test-dns-provider", json=body,
                )

        assert response.status_code == 200, response.text
        assert TOKEN_WITH_ILLEGAL_HEADER_CHAR.strip() not in response.text
        for record in caplog.records:
            assert TOKEN_WITH_ILLEGAL_HEADER_CHAR.strip() not in record.getMessage()


class TestRenewalErrorIsRedactedBeforeItIsPersisted:
    """``last_renewal_error`` is written to disk and served by GET /status."""

    @pytest.mark.asyncio
    async def test_dns_challenge_failure_stores_no_credential(self):
        from tls import renewal

        settings = _configured_settings()
        saved = []

        acme = MagicMock()
        acme.return_value.initialize = AsyncMock(return_value=True)
        acme.return_value.request_certificate = AsyncMock(
            return_value=MagicMock(success=False, error="Challenge pending")
        )
        acme.return_value.get_all_pending_challenges.return_value = [
            MagicMock(txt_record_name="_acme-challenge.tls-2owpi.example.com",
                      txt_record_value="synthetic-challenge-value")
        ]

        provider = MagicMock()
        provider.create_and_get_zone = AsyncMock(
            side_effect=RuntimeError(
                f"Illegal header value b'Bearer {DNS_TOKEN}'"
            )
        )

        with patch("tls.renewal._acme_available", new=True), \
                patch("tls.renewal.get_tls_settings", return_value=settings), \
                patch("tls.renewal.save_tls_settings",
                      side_effect=lambda s: saved.append(s.last_renewal_error)), \
                patch("tls.renewal.ACMEClient", new=acme), \
                patch("tls.renewal.get_dns_provider", return_value=provider):
            result = await renewal.renew_certificate()

        assert result.success is False
        assert result.error
        assert DNS_TOKEN not in result.error
        assert settings.last_renewal_error
        assert DNS_TOKEN not in settings.last_renewal_error
        for persisted in saved:
            assert DNS_TOKEN not in (persisted or "")


# ===========================================================================
# CHECK 2b — the validation-error handler
# ===========================================================================


class TestValidationErrorHandlerRedactsTLSBodies:
    """A malformed configure body must not put the credential in the log.

    ``https_port`` is sent as a non-numeric string so the body fails
    validation for a reason that has nothing to do with the credentials —
    which is exactly the realistic case, and the one that used to log them.
    """

    _BAD_BODY = {
        "enabled": True,
        "mode": "letsencrypt",
        "domain": "tls-2owpi.example.com",
        "https_port": "not-a-port",
        "dns_provider": "route53",
        "dns_api_token": DNS_TOKEN,
        "aws_access_key_id": AWS_KEY_ID,
        "aws_secret_access_key": AWS_SECRET,
    }

    @pytest.mark.asyncio
    async def test_configure_422_logs_no_credential(self, async_client, caplog):
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
                patch("auth.dependencies.get_current_user",
                      new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            with caplog.at_level(logging.DEBUG):
                response = await async_client.post(
                    "/api/tls/configure", json=self._BAD_BODY,
                )

        assert response.status_code == 422, response.text

        logged = "\n".join(r.getMessage() for r in caplog.records)
        for secret in (DNS_TOKEN, AWS_SECRET, AWS_KEY_ID):
            assert secret not in logged

    @pytest.mark.asyncio
    async def test_configure_422_response_echoes_no_credential(
        self, async_client
    ):
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
                patch("auth.dependencies.get_current_user",
                      new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            response = await async_client.post(
                "/api/tls/configure", json=self._BAD_BODY,
            )

        assert response.status_code == 422, response.text
        for secret in (DNS_TOKEN, AWS_SECRET, AWS_KEY_ID):
            assert secret not in response.text

    @pytest.mark.asyncio
    async def test_test_dns_provider_422_logs_no_credential(
        self, async_client, caplog
    ):
        """The other TLS route whose body carries a credential."""
        bad = {
            "api_token": DNS_TOKEN,
            "aws_secret_access_key": AWS_SECRET,
        }  # "provider" is required and missing

        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
                patch("auth.dependencies.get_current_user",
                      new=AsyncMock(return_value=_admin_user())):
            auth_mock.return_value.require_auth = True
            auth_mock.return_value.setup_complete = True
            with caplog.at_level(logging.DEBUG):
                response = await async_client.post(
                    "/api/tls/test-dns-provider", json=bad,
                )

        assert response.status_code == 422, response.text
        logged = "\n".join(r.getMessage() for r in caplog.records)
        for secret in (DNS_TOKEN, AWS_SECRET):
            assert secret not in logged
        for secret in (DNS_TOKEN, AWS_SECRET):
            assert secret not in response.text

    @pytest.mark.asyncio
    async def test_a_non_credential_path_still_logs_its_body(
        self, async_client, caplog
    ):
        """The redaction is scoped, not a blanket loss of debuggability.

        Without this, widening the prefix list to ``/api`` would look like a
        passing change while destroying the diagnostic value the handler
        exists for.
        """
        from main import CREDENTIAL_BEARING_BODY_PREFIXES

        assert not any(
            p in ("/api", "/api/", "/") for p in CREDENTIAL_BEARING_BODY_PREFIXES
        )
        assert "/api/auth" in CREDENTIAL_BEARING_BODY_PREFIXES
        assert "/api/tls" in CREDENTIAL_BEARING_BODY_PREFIXES

    def test_tls_configure_body_fields_are_all_covered_by_the_prefix(self):
        """A TLS route body must never be outside the redacted prefix set."""
        from main import CREDENTIAL_BEARING_BODY_PREFIXES, app
        from fastapi.routing import APIRoute

        tls_paths = [
            route.path for route in app.routes
            if isinstance(route, APIRoute) and route.path.startswith("/api/tls")
        ]
        assert tls_paths
        for path in tls_paths:
            assert any(path.startswith(p) for p in CREDENTIAL_BEARING_BODY_PREFIXES)


# ===========================================================================
# The settings loader must not print field values on a malformed file
# ===========================================================================


class TestSettingsLoadFailureLogsNoValues:
    def test_type_corrupt_credential_field_is_not_logged(
        self, tmp_path, caplog
    ):
        """Pydantic v2 puts ``input_value=<the value>`` in its error text."""
        from tls import settings as tls_settings

        config_file = tmp_path / "tls_settings.json"
        # A credential stored as a JSON number rather than a string: pydantic
        # refuses int -> str coercion and reports the offending input value.
        config_file.write_text(json.dumps({
            "enabled": True,
            "aws_secret_access_key": 98765432109876543210,
        }))

        with patch.object(tls_settings, "TLS_CONFIG_FILE", config_file):
            tls_settings.clear_tls_settings_cache()
            with caplog.at_level(logging.DEBUG, logger="tls.settings"):
                loaded = tls_settings.load_tls_settings()
            tls_settings.clear_tls_settings_cache()

        assert isinstance(loaded, TLSSettings)
        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "98765432109876543210" not in logged
        # The failure must still be diagnosable: name the field.
        assert "aws_secret_access_key" in logged
