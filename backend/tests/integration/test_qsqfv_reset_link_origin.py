"""bead enhancedchannelmanager-qsqfv (P1): where the emailed reset link points.

THE DEFECT
----------

``POST /api/auth/forgot-password`` built the reset link's origin like this:

    forwarded_proto = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    forwarded_host = request.headers.get("X-Forwarded-Host", request.url.netloc)
    base_url = f"{forwarded_proto}://{forwarded_host}"

Every input there is supplied by whoever sent the request, including the
``request.url.netloc`` fallback, which is just the Host header. An
unauthenticated caller who knew a victim's email address could therefore make
ECM send that victim a GENUINE reset email, from ECM's own SMTP, carrying a
live reset token in a link pointing at the attacker's host.

WHAT IS PINNED HERE
-------------------

The fix is a configurable canonical origin (``public_base_url``). When it is
set it is used verbatim and the request is not consulted; when it is unset the
old header-derived behaviour is preserved on purpose (PO decision 2026-08-15)
so no existing install's reset email breaks, and ECM warns once per process.

:class:`TestEmittedResetLink` asserts on the EMITTED ARTIFACT: the bytes handed
to ``smtplib.SMTP.sendmail``, which is the message the victim actually
receives. Asserting on the arguments passed to ``send_password_reset_email``
would only prove the caller's intent, not what left the process, and both the
plain-text and HTML alternatives of this message carry the link separately.
"""
import re
from unittest.mock import MagicMock, patch

import pytest

import config
from config import DispatcharrSettings, normalize_public_base_url


# Distinctive so a leak is findable anywhere in the emitted bytes.
CONFIGURED_ORIGIN = "https://ecm.qsqfv-configured.invalid"
ATTACKER_HOST = "qsqfv-attacker.invalid"
SMTP_HOST = "smtp.qsqfv-mail.example.com"
SMTP_FROM_EMAIL = "ecm@example.com"
PROXY_HOST = "qsqfv-proxy.invalid"

# Placeholder shape per docs/pytest_conventions.md ("Credential Fixtures in
# Security Tests"): angle-bracket values are never scan candidates, and this
# password's exact shape is not load-bearing for anything asserted below.
USER_PASSWORD = "<synthetic-qsqfv-user-password>"

# An origin carrying userinfo, assembled from two literals so that no single
# line holds a whole scheme-plus-credentials-plus-host URL. The secrets
# ratchet's BasicAuthDetector matches that shape one line at a time, and the
# inline pragma detect-secrets suggests is deliberately disabled here
# (docs/pytest_conventions.md, "Credential Fixtures in Security Tests").
USERINFO_ORIGIN = (
    "https://someone:"
    + "placeholder@example.com"
)

RESET_LINK_RE = re.compile(r"(https?://[^/\s\"]+)/reset-password\?token=([A-Za-z0-9_\-]+)")


def _settings(**overrides) -> DispatcharrSettings:
    """Settings with SMTP configured, so the send path actually runs."""
    values = {
        "smtp_host": SMTP_HOST,
        "smtp_from_email": SMTP_FROM_EMAIL,
    }
    values.update(overrides)
    return DispatcharrSettings(**values)


@pytest.fixture
def stub_settings():
    """Install a settings object for the whole process, then restore.

    Writes the coherent ``config`` cache state rather than patching a
    ``get_settings`` name: ``auth.routes`` (the emailer) and
    ``config.get_public_base_url`` (the origin resolver) must see the SAME
    settings, and the cache is the one place both of them read through.
    ``clear_settings_cache()`` also re-arms the one-shot WARN flags, which
    several tests below assert on.
    """
    installed = []

    def _install(settings: DispatcharrSettings) -> DispatcharrSettings:
        config.clear_settings_cache()
        config._cached_settings = settings
        config._cached_mcp_files_signature = config._mcp_files_cache_signature()
        installed.append(settings)
        return settings

    yield _install
    config.clear_settings_cache()


@pytest.fixture
def captured_email():
    """Capture the raw message handed to ``smtplib.SMTP.sendmail``."""
    sent = {}

    def _sendmail(from_email, to_emails, message):
        sent["from"] = from_email
        sent["to"] = to_emails
        sent["message"] = message

    server = MagicMock()
    server.sendmail.side_effect = _sendmail
    with patch("auth.routes.smtplib.SMTP", return_value=server):
        yield sent


@pytest.fixture
def reset_user(test_session):
    """A local, active user with an email address, the only kind that gets mail."""
    from models import User
    from auth.password import hash_password

    user = User(
        username="qsqfv-victim",
        email="qsqfv-victim@example.com",
        password_hash=hash_password(USER_PASSWORD),
        auth_provider="local",
        is_admin=False,
        is_active=True,
    )
    test_session.add(user)
    test_session.commit()
    test_session.refresh(user)
    return user


def _emitted_link(captured: dict) -> tuple[str, str]:
    """Return (origin, token) from the link in the emitted message."""
    assert "message" in captured, "no email was emitted"
    matches = RESET_LINK_RE.findall(captured["message"])
    assert matches, f"no reset link found in emitted message: {captured['message'][:400]}"
    origins = {origin for origin, _ in matches}
    assert len(origins) == 1, f"emitted message carries conflicting origins: {origins}"
    return matches[0]


class TestEmittedResetLink:
    """End-to-end: POST /api/auth/forgot-password, read the emitted message."""

    @pytest.mark.asyncio
    async def test_poisoned_forwarded_headers_cannot_move_the_configured_link(
        self, async_client, reset_user, stub_settings, captured_email
    ):
        """RED WITHOUT THE FIX: the link pointed at X-Forwarded-Host."""
        stub_settings(_settings(public_base_url=CONFIGURED_ORIGIN))

        response = await async_client.post(
            "/api/auth/forgot-password",
            json={"email": reset_user.email},
            headers={
                "X-Forwarded-Host": ATTACKER_HOST,
                "X-Forwarded-Proto": "http",
            },
        )

        assert response.status_code == 200
        origin, token = _emitted_link(captured_email)
        assert origin == CONFIGURED_ORIGIN
        assert token, "the emitted link carried no token"
        # The token is live, so the attacker host must not appear ANYWHERE in
        # the message, not merely outside the link: both the plain-text and
        # HTML alternatives carry the URL independently.
        assert ATTACKER_HOST not in captured_email["message"]

    @pytest.mark.asyncio
    async def test_host_header_alone_cannot_move_the_configured_link(
        self, async_client, reset_user, stub_settings, captured_email
    ):
        """The Host-header fallback is caller-controlled too, so it is ignored."""
        stub_settings(_settings(public_base_url=CONFIGURED_ORIGIN))

        response = await async_client.post(
            "/api/auth/forgot-password",
            json={"email": reset_user.email},
            headers={"Host": ATTACKER_HOST},
        )

        assert response.status_code == 200
        origin, _ = _emitted_link(captured_email)
        assert origin == CONFIGURED_ORIGIN
        assert ATTACKER_HOST not in captured_email["message"]

    @pytest.mark.asyncio
    async def test_unset_public_base_url_still_honours_the_proxy_headers(
        self, async_client, reset_user, stub_settings, captured_email
    ):
        """No regression for installs that never configure the setting.

        This is the behaviour the PO deliberately kept: an existing
        reverse-proxied install whose operator upgrades and configures nothing
        must keep receiving working reset emails.
        """
        stub_settings(_settings(public_base_url=""))

        response = await async_client.post(
            "/api/auth/forgot-password",
            json={"email": reset_user.email},
            headers={
                "X-Forwarded-Host": PROXY_HOST,
                "X-Forwarded-Proto": "https",
            },
        )

        assert response.status_code == 200
        origin, token = _emitted_link(captured_email)
        assert origin == f"https://{PROXY_HOST}"
        assert token

    @pytest.mark.asyncio
    async def test_unusable_stored_value_degrades_to_the_legacy_behaviour(
        self, async_client, reset_user, stub_settings, captured_email, caplog
    ):
        """A hand-edited or restored settings.json cannot emit a broken link."""
        # The model itself does not reject this; settings.json is also written
        # by hand and by backup restores, which is why the read path
        # re-validates rather than trusting the stored value.
        stub_settings(_settings(public_base_url="ecm.qsqfv-no-scheme.invalid"))

        with caplog.at_level("WARNING"):
            response = await async_client.post(
                "/api/auth/forgot-password",
                json={"email": reset_user.email},
                headers={"X-Forwarded-Host": PROXY_HOST, "X-Forwarded-Proto": "https"},
            )

        assert response.status_code == 200
        origin, _ = _emitted_link(captured_email)
        assert origin == f"https://{PROXY_HOST}"
        assert any(
            "public_base_url" in record.message and "not usable" in record.message
            for record in caplog.records
        ), "no WARN named the unusable stored value"


class TestNormalizePublicBaseUrl:
    """The single place the shape of a configured value is decided."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("https://ecm.example.org", "https://ecm.example.org"),
            # A trailing slash is accepted and stripped; callers append their
            # own leading-slash path.
            ("https://ecm.example.org/", "https://ecm.example.org"),
            ("  https://ecm.example.org  ", "https://ecm.example.org"),
            # Scheme and host are case-insensitive; the port is not touched.
            ("HTTPS://ECM.Example.ORG:8443", "https://ecm.example.org:8443"),
            ("http://192.168.1.10:6100", "http://192.168.1.10:6100"),
            # IPv6 literals keep their brackets.
            ("http://[2001:DB8::1]:6100", "http://[2001:db8::1]:6100"),
            # Unset is a legitimate state, not an error.
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_accepts_and_normalizes(self, raw, expected):
        normalized, err = normalize_public_base_url(raw)
        assert err is None, f"unexpectedly rejected {raw!r}: {err}"
        assert normalized == expected

    @pytest.mark.parametrize(
        "raw,reason_fragment",
        [
            ("ecm.example.org", "http://"),
            ("//ecm.example.org", "http://"),
            ("ftp://ecm.example.org", "http://"),
            ("javascript:alert(1)", "http://"),
            ("https://", "host"),
            ("https://ecm.example.org/ecm", "path"),
            ("https://ecm.example.org/reset-password", "path"),
            ("https://ecm.example.org/?next=x", "query"),
            ("https://ecm.example.org#frag", "fragment"),
            (USERINFO_ORIGIN, "credentials"),
            ("https://ecm example.org", "whitespace"),
            ("https://ecm\n.example.org", "whitespace"),
            ("https://ecm.example.org:not-a-port", "port"),
        ],
    )
    def test_rejects_with_a_reason(self, raw, reason_fragment):
        normalized, err = normalize_public_base_url(raw)
        assert err is not None, f"unexpectedly accepted {raw!r}"
        assert reason_fragment in err
        assert normalized == ""


class TestUnsetWarningCadence:
    """Actionable, and not once per request."""

    def test_warns_once_per_process_and_re_arms_on_a_settings_save(
        self, stub_settings, caplog
    ):
        stub_settings(_settings(public_base_url=""))

        with caplog.at_level("WARNING"):
            assert config.get_public_base_url() == ""
            assert config.get_public_base_url() == ""

        warnings = [r for r in caplog.records if "public_base_url is not set" in r.message]
        assert len(warnings) == 1, "the unset WARN is per-call, not once per process"
        assert "qsqfv" in warnings[0].message, "the WARN does not name the bead"

        # clear_settings_cache() runs on every settings save, so an operator who
        # saves settings while it is still unset is told again.
        caplog.clear()
        stub_settings(_settings(public_base_url=""))
        with caplog.at_level("WARNING"):
            assert config.get_public_base_url() == ""
        assert [r for r in caplog.records if "public_base_url is not set" in r.message]

    def test_configured_value_is_returned_and_does_not_warn(self, stub_settings, caplog):
        installed = stub_settings(
            _settings(public_base_url=f"{CONFIGURED_ORIGIN}/")
        )

        assert config.get_settings() is installed

        with caplog.at_level("WARNING"):
            resolved = config.get_public_base_url()

        assert resolved == CONFIGURED_ORIGIN
        assert not [r for r in caplog.records if "public_base_url" in r.message]


class TestSettingsSaveValidation:
    """POST /api/settings is where an operator's value is accepted or refused."""

    @staticmethod
    def _body(**overrides) -> dict:
        # The Settings UI POSTs the whole blob, so the body must carry the
        # stored values of the other admin-only fields; anything that differs
        # is a change attempt and the field-level admin gate would 403 on it
        # for reasons unrelated to what these tests are pinning.
        body = {
            "url": "",
            "auth_method": "password",
            "username": "",
            "password": "",
            "smtp_host": SMTP_HOST,
            "smtp_from_email": SMTP_FROM_EMAIL,
        }
        body.update(overrides)
        return body

    @pytest.mark.asyncio
    async def test_rejects_an_invalid_value_with_400_and_stores_nothing(
        self, admin_client, stub_settings
    ):
        stub_settings(_settings(public_base_url=CONFIGURED_ORIGIN))

        with patch("routers.settings.save_settings") as saver:
            response = await admin_client.post(
                "/api/settings",
                json=self._body(public_base_url="https://ecm.example.org/subpath"),
            )

        assert response.status_code == 400
        assert "public base URL" in response.json()["detail"]
        saver.assert_not_called()

    @pytest.mark.asyncio
    async def test_stores_the_normalized_value(self, admin_client, stub_settings):
        stub_settings(_settings(public_base_url=""))

        with patch("routers.settings.save_settings") as saver:
            response = await admin_client.post(
                "/api/settings",
                json=self._body(public_base_url="HTTPS://ECM.Example.ORG/"),
            )

        assert response.status_code == 200
        saver.assert_called_once()
        assert saver.call_args[0][0].public_base_url == "https://ecm.example.org"

    @pytest.mark.asyncio
    async def test_a_body_without_the_field_preserves_the_stored_value(
        self, admin_client, stub_settings
    ):
        """A cached frontend bundle that predates the field must not clear it."""
        stub_settings(_settings(public_base_url=CONFIGURED_ORIGIN))

        with patch("routers.settings.save_settings") as saver:
            response = await admin_client.post("/api/settings", json=self._body())

        assert response.status_code == 200
        saver.assert_called_once()
        assert saver.call_args[0][0].public_base_url == CONFIGURED_ORIGIN

    @pytest.mark.asyncio
    async def test_a_non_admin_cannot_change_it(self, non_admin_client, stub_settings):
        stub_settings(_settings(public_base_url=CONFIGURED_ORIGIN))

        with patch("routers.settings.save_settings") as saver:
            response = await non_admin_client.post(
                "/api/settings",
                json=self._body(public_base_url="https://ecm.attacker-chosen.invalid"),
            )

        assert response.status_code == 403
        saver.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_non_admin_save_without_the_field_is_not_a_change(
        self, non_admin_client, stub_settings
    ):
        """The gate must not turn preserve-on-omit into a 403 on every save."""
        stub_settings(_settings(public_base_url=CONFIGURED_ORIGIN))

        with patch("routers.settings.save_settings"):
            response = await non_admin_client.post("/api/settings", json=self._body())

        assert response.status_code == 200


class TestSessionCookieTransportWarning:
    """bead 04c0u.9: the same unset value also leaves session cookies open.

    The warning above is about password-reset links. An operator reading it has
    no reason to connect it to cookie policy, so the residual — auth on, ECM
    terminating no TLS, no canonical https origin — stayed invisible. This is
    the second, separately-worded WARN that names that consequence.
    """

    _FRAGMENT = "Session cookies are UNPROTECTED"

    @staticmethod
    def _tls_off():
        return patch(
            "config._session_cookies_travel_in_cleartext", return_value=True
        )

    @staticmethod
    def _tls_on():
        return patch(
            "config._session_cookies_travel_in_cleartext", return_value=False
        )

    def test_warns_once_per_process_when_nothing_protects_the_cookies(
        self, stub_settings, caplog
    ):
        stub_settings(_settings(public_base_url=""))

        with self._tls_off(), caplog.at_level("WARNING"):
            assert config.get_public_base_url() == ""
            assert config.get_public_base_url() == ""

        warnings = [r for r in caplog.records if self._FRAGMENT in r.message]
        assert len(warnings) == 1, [r.message for r in caplog.records]
        assert "04c0u.9" in warnings[0].message

    def test_silent_when_ecm_terminates_tls(self, stub_settings, caplog):
        stub_settings(_settings(public_base_url=""))

        with self._tls_on(), caplog.at_level("WARNING"):
            assert config.get_public_base_url() == ""

        assert not [r for r in caplog.records if self._FRAGMENT in r.message]

    def test_silent_when_a_canonical_https_origin_is_configured(
        self, stub_settings, caplog
    ):
        stub_settings(_settings(public_base_url=CONFIGURED_ORIGIN))

        with self._tls_off(), caplog.at_level("WARNING"):
            assert config.get_public_base_url() == CONFIGURED_ORIGIN

        assert not [r for r in caplog.records if self._FRAGMENT in r.message]

    def test_the_predicate_is_false_when_auth_is_disabled(self):
        """Guard the guard: the helper must be able to answer both ways."""
        with patch("auth.settings.get_auth_settings") as auth_mock:
            auth_mock.return_value.require_auth = False
            assert config._session_cookies_travel_in_cleartext() is False

    def test_the_predicate_is_true_when_auth_is_on_and_tls_is_off(self, tmp_path):
        with patch("auth.settings.get_auth_settings") as auth_mock, \
                patch("tls.settings.get_tls_settings") as tls_mock:
            auth_mock.return_value.require_auth = True
            tls_mock.return_value.enabled = False
            assert config._session_cookies_travel_in_cleartext() is True
