"""``POST /api/tls/configure`` persistence and activation side effects.

Bead enhancedchannelmanager-04c0u.9 remediation. Nothing in the repo covered the
persistence path for ``allow_http_session_cookies`` in either direction: QA
proved the blindness by mutating the handler to hardcode ``False`` and watching
all 232 TLS tests and all 16 new tests pass.

Three properties live here:

* a request that OMITS the flag preserves it (it used to silently clear it);
* a request that SENDS it writes it, in both directions, with a log line; and
* switching TLS on with key material present revokes every existing browser
  session, because a jar holding a pre-activation non-``Secure``
  ``refresh_token`` otherwise keeps sending it to the plain-HTTP port for the
  remaining 7 days.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import tls.settings as tls_settings_module
from models import User, UserSession
from tls.settings import TLSSettings, clear_tls_settings_cache, get_tls_settings


BASE_BODY = {
    "enabled": False,
    "mode": "manual",
    "domain": "ecm.test",
    "https_port": 6143,
    "acme_email": "",
    "use_staging": False,
    "dns_provider": "",
    "dns_api_token": "",
    "dns_zone_id": "",
    "aws_access_key_id": "",
    "aws_secret_access_key": "",
    "aws_region": "us-east-1",
    "auto_renew": True,
    "renew_days_before_expiry": 30,
}


@pytest.fixture
def tls_config_root(tmp_path, monkeypatch):
    """Relocate ``tls_settings.json`` and the TLS key directory into tmp_path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    tls_dir = config_dir / "tls"
    tls_dir.mkdir()

    monkeypatch.setattr(tls_settings_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(tls_settings_module, "TLS_CONFIG_FILE", config_dir / "tls_settings.json")
    monkeypatch.setattr(tls_settings_module, "TLS_DIR", tls_dir)
    # ``tls.routes`` binds TLS_DIR by value at import.
    import tls.routes as tls_routes
    monkeypatch.setattr(tls_routes, "TLS_DIR", tls_dir)
    # So does ``auth.routes``, and ``GET /api/tls/status`` now reports the
    # session-cookie verdict through the auth-side predicate, so the relocation
    # has to reach that copy too or the status field reads the real /config.
    import auth.routes as auth_routes
    monkeypatch.setattr(auth_routes, "TLS_DIR", tls_dir)

    clear_tls_settings_cache()
    yield config_dir
    clear_tls_settings_cache()


def _issue_certificate(tls_config_root):
    tls_dir = tls_config_root / "tls"
    (tls_dir / "cert.pem").write_text("certificate fixture")
    (tls_dir / "key.pem").write_text("private-key fixture")


def _store(**fields):
    from tls.settings import save_tls_settings
    settings = TLSSettings(**fields)
    assert save_tls_settings(settings) is True
    return settings


def _admin_user() -> User:
    return User(
        id=9410,
        username="tls-admin",
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )


class _AsAdmin:
    """Reach the handler as a human admin, with the HTTPS manager stubbed out."""

    def __init__(self):
        manager = MagicMock()
        manager.is_running = False
        manager.start = AsyncMock(return_value=(True, None))
        manager.stop = AsyncMock(return_value=None)
        self.manager = manager
        self._contexts = [
            patch("tls.routes.https_server_manager", new=manager),
            patch("auth.dependencies.get_auth_settings"),
            patch(
                "auth.dependencies.get_current_user",
                new=AsyncMock(return_value=_admin_user()),
            ),
        ]

    def __enter__(self):
        entered = [ctx.__enter__() for ctx in self._contexts]
        auth_mock = entered[1]
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        return self

    def __exit__(self, *exc):
        for ctx in reversed(self._contexts):
            ctx.__exit__(*exc)
        return False


# ---------------------------------------------------------------------------
# Persistence of the break-glass flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_request_omitting_the_flag_preserves_it(async_client, tls_config_root):
    """A stale tab must not silently disable the operator's recovery path.

    ``allow_http_session_cookies`` was a ``bool = False`` Pydantic default with
    an unconditional overwrite, so any POST that did not carry the field — a
    cached pre-04c0u.9 bundle in an open tab, a scripted caller, an API client
    written against the previous contract — turned break-glass OFF with no
    error and no signal. Same shape as bead ``enhancedchannelmanager-iij6s``,
    on a security-relevant field.
    """
    _store(enabled=True, mode="manual", allow_http_session_cookies=True)

    with _AsAdmin():
        response = await async_client.post(
            "/api/tls/configure", json={**BASE_BODY, "enabled": True}
        )

    assert response.status_code == 200, response.text
    assert get_tls_settings().allow_http_session_cookies is True


@pytest.mark.asyncio
async def test_a_request_omitting_the_flag_preserves_it_off(async_client, tls_config_root):
    """Preserve-on-omit is symmetric: omission never flips the stored value."""
    _store(enabled=True, mode="manual", allow_http_session_cookies=False)

    with _AsAdmin():
        response = await async_client.post(
            "/api/tls/configure", json={**BASE_BODY, "enabled": True}
        )

    assert response.status_code == 200, response.text
    assert get_tls_settings().allow_http_session_cookies is False


@pytest.mark.asyncio
async def test_an_explicit_true_turns_break_glass_on_and_logs_it(
    async_client, tls_config_root, caplog
):
    _store(enabled=True, mode="manual", allow_http_session_cookies=False)

    with _AsAdmin(), caplog.at_level(logging.WARNING, logger="tls.routes"):
        response = await async_client.post(
            "/api/tls/configure",
            json={**BASE_BODY, "enabled": True, "allow_http_session_cookies": True},
        )

    assert response.status_code == 200, response.text
    assert get_tls_settings().allow_http_session_cookies is True
    assert [r for r in caplog.records if "turned ON" in r.getMessage()]


@pytest.mark.asyncio
async def test_an_explicit_false_turns_break_glass_off_and_logs_it(
    async_client, tls_config_root, caplog
):
    _store(enabled=True, mode="manual", allow_http_session_cookies=True)

    with _AsAdmin(), caplog.at_level(logging.WARNING, logger="tls.routes"):
        response = await async_client.post(
            "/api/tls/configure",
            json={**BASE_BODY, "enabled": True, "allow_http_session_cookies": False},
        )

    assert response.status_code == 200, response.text
    assert get_tls_settings().allow_http_session_cookies is False
    assert [r for r in caplog.records if "turned OFF" in r.getMessage()]


@pytest.mark.asyncio
async def test_omitting_the_flag_while_it_is_on_is_logged(
    async_client, tls_config_root, caplog
):
    """Preserve-on-omit is quiet about the value but not about the omission."""
    _store(enabled=True, mode="manual", allow_http_session_cookies=True)

    with _AsAdmin(), caplog.at_level(logging.WARNING, logger="tls.routes"):
        await async_client.post(
            "/api/tls/configure", json={**BASE_BODY, "enabled": True}
        )

    assert [
        r for r in caplog.records
        if "omitted allow_http_session_cookies" in r.getMessage()
    ]


# ---------------------------------------------------------------------------
# GET /api/tls/status surfaces both inputs to the escape hatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_surfaces_the_stored_break_glass_flag(async_client, tls_config_root):
    _issue_certificate(tls_config_root)
    _store(enabled=True, mode="manual", allow_http_session_cookies=True)

    with _AsAdmin():
        response = await async_client.get("/api/tls/status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["allow_http_session_cookies"] is True
    assert body["http_session_cookies_env_override"] is False
    assert body["session_cookies_plaintext"] is True


@pytest.mark.asyncio
async def test_status_surfaces_the_environment_override(
    async_client, tls_config_root, monkeypatch
):
    """The stored flag alone was not enough to see the hazard.

    An operator who recovered with ``ECM_ALLOW_HTTP_SESSION_COOKIES`` and then
    forgot the line saw an UNCHECKED checkbox and an "Encrypted" badge while
    every session cookie shipped without ``Secure`` indefinitely.
    """
    _issue_certificate(tls_config_root)
    _store(enabled=True, mode="manual", allow_http_session_cookies=False)
    monkeypatch.setenv("ECM_ALLOW_HTTP_SESSION_COOKIES", "true")

    with _AsAdmin():
        response = await async_client.get("/api/tls/status")

    body = response.json()
    assert body["allow_http_session_cookies"] is False
    assert body["http_session_cookies_env_override"] is True
    assert body["session_cookies_plaintext"] is True


@pytest.mark.asyncio
async def test_status_is_quiet_when_no_hatch_is_open(async_client, tls_config_root):
    _issue_certificate(tls_config_root)
    _store(enabled=True, mode="manual", allow_http_session_cookies=False)

    with _AsAdmin():
        body = (await async_client.get("/api/tls/status")).json()

    assert body["allow_http_session_cookies"] is False
    assert body["http_session_cookies_env_override"] is False
    assert body["session_cookies_plaintext"] is False


@pytest.mark.asyncio
async def test_status_reports_plaintext_cookies_behind_a_reverse_proxy(
    async_client, tls_config_root
):
    """ECM's own TLS is OFF here, and the cookies are still being downgraded.

    ``public_base_url`` is an https origin, so without break-glass the cookies
    would carry ``Secure``. The field used to require ECM's own TLS, so it read
    False on exactly this deployment: the backend WARNED that live sessions were
    being downgraded while the API said they were not and the banner — which is
    gated entirely on this field — never rendered.
    """
    _store(enabled=False, mode="manual", allow_http_session_cookies=True)

    with _AsAdmin(), patch(
        "auth.routes.get_public_base_url", return_value="https://ecm.example.test"
    ):
        body = (await async_client.get("/api/tls/status")).json()

    assert body["allow_http_session_cookies"] is True
    assert body["session_cookies_plaintext"] is True


@pytest.mark.asyncio
async def test_status_is_quiet_on_a_bare_http_install_with_the_hatch_open(
    async_client, tls_config_root
):
    """No TLS and no https origin: the hatch changes nothing, same as the WARN.

    Reporting True here would put a permanent banner on every plain-HTTP
    install, which is the noise the WARN suppression exists to avoid.
    """
    _store(enabled=False, mode="manual", allow_http_session_cookies=True)

    with _AsAdmin(), patch("auth.routes.get_public_base_url", return_value=""):
        body = (await async_client.get("/api/tls/status")).json()

    assert body["allow_http_session_cookies"] is True
    assert body["session_cookies_plaintext"] is False


# ---------------------------------------------------------------------------
# Activating TLS revokes pre-activation sessions
# ---------------------------------------------------------------------------


def _seed_session(test_session, user_id: int = 9411) -> UserSession:
    from datetime import datetime, timedelta
    from auth.tokens import hash_token

    user = User(
        id=user_id,
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.com",
        auth_provider="local",
        is_active=True,
    )
    test_session.add(user)
    row = UserSession(
        user_id=user_id,
        refresh_token_hash=hash_token(f"pre-activation-token-{user_id}"),
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    test_session.add(row)
    test_session.commit()
    return row


@pytest.mark.asyncio
async def test_activating_tls_revokes_existing_sessions(
    async_client, test_session, tls_config_root
):
    """The bead's criterion is not met for existing sessions without this.

    A browser holding a pre-activation, non-``Secure`` ``refresh_token`` keeps
    sending it to port 6100 for the remaining 7 days, and a token captured in
    cleartext can be rotated forward indefinitely. Logging in once over HTTPS
    overwrites the pair, so this bites the operator who bookmarked the HTTP port
    and never revisits over HTTPS.
    """
    _issue_certificate(tls_config_root)
    _store(enabled=False, mode="manual")
    row = _seed_session(test_session)
    epoch_before = test_session.query(User).filter(User.id == 9411).one().auth_epoch

    with _AsAdmin():
        response = await async_client.post(
            "/api/tls/configure", json={**BASE_BODY, "enabled": True}
        )

    assert response.status_code == 200, response.text
    assert "sign in again over HTTPS" in response.json()["message"]

    test_session.expire_all()
    assert test_session.query(UserSession).filter(
        UserSession.id == row.id
    ).one().is_revoked is True
    assert test_session.query(User).filter(
        User.id == 9411
    ).one().auth_epoch == epoch_before + 1


@pytest.mark.asyncio
async def test_saving_while_tls_is_already_on_does_not_revoke_sessions(
    async_client, test_session, tls_config_root
):
    """Only the OFF -> ON transition cuts sessions.

    Otherwise every unrelated TLS settings save — including a break-glass save
    made mid-recovery — would log the operator out again.
    """
    _issue_certificate(tls_config_root)
    _store(enabled=True, mode="manual")
    row = _seed_session(test_session, user_id=9412)

    with _AsAdmin():
        response = await async_client.post(
            "/api/tls/configure", json={**BASE_BODY, "enabled": True}
        )

    assert response.status_code == 200, response.text
    test_session.expire_all()
    assert test_session.query(UserSession).filter(
        UserSession.id == row.id
    ).one().is_revoked is False


@pytest.mark.asyncio
async def test_enabling_tls_before_a_certificate_exists_does_not_revoke_sessions(
    async_client, test_session, tls_config_root
):
    """Enabled-but-not-yet-issued is not activation, here as everywhere else.

    Cutting sessions here would log the operator out of the very UI they need
    in order to finish issuing the certificate.
    """
    _store(enabled=False, mode="manual")
    row = _seed_session(test_session, user_id=9413)

    with _AsAdmin():
        response = await async_client.post(
            "/api/tls/configure", json={**BASE_BODY, "enabled": True}
        )

    assert response.status_code == 200, response.text
    test_session.expire_all()
    assert test_session.query(UserSession).filter(
        UserSession.id == row.id
    ).one().is_revoked is False


# ---------------------------------------------------------------------------
# ...on EVERY activation route, not only POST /configure
# ---------------------------------------------------------------------------
#
# Round-2 remediation. The revocation above was keyed to ``POST /configure``
# flipping ``enabled``, which is not where TLS activates on either flow an
# operator actually takes:
#
# * ACME — ``enabled`` is switched on FIRST, while ``has_certificate()`` is
#   still false (deliberately, see the test above: cutting sessions there would
#   log the operator out of the UI they need to finish issuing). Termination
#   begins later, when ``/request-cert`` or ``/complete-challenge`` lands the
#   certificate.
# * Manual — ``/upload-cert`` sets ``enabled = True`` itself and starts the
#   listener, and never went near ``/configure``.
#
# So the criterion is stated as a property and not as the ``/configure``
# reproduction: NO session that predates TLS termination survives it, by any
# route. The predicate is the same one ``auth.routes._ecm_terminates_tls``
# reads, so "termination began" here and "cookies must be Secure" there cannot
# drift apart.


def _real_key_pair(cn: str = "ecm.test"):
    """A genuine self-signed EC certificate and its key, both PEM.

    ``/upload-cert`` runs ``validate_pair`` and ``save_certificate`` runs it
    again, so the fixture files the other tests write would be rejected before
    the handler ever reached the code under test. EC rather than RSA: key
    generation is the slow part of this file otherwise.
    """
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before((now - timedelta(days=1)).replace(tzinfo=None))
        .not_valid_after((now + timedelta(days=365)).replace(tzinfo=None))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def _upload_files(cert_pem: bytes, key_pem: bytes) -> dict:
    return {
        "cert_file": ("cert.pem", cert_pem, "application/x-pem-file"),
        "key_file": ("key.pem", key_pem, "application/x-pem-file"),
    }


@pytest.mark.asyncio
async def test_uploading_a_certificate_revokes_existing_sessions(
    async_client, test_session, tls_config_root
):
    """The manual flow activates TLS here and nowhere else.

    ``/upload-cert`` sets ``enabled = True``, saves the key material and starts
    the listener in one call. Before this, every pre-activation non-``Secure``
    ``refresh_token`` survived that transition and stayed replayable against the
    brand-new HTTPS listener for the remaining 7 days.
    """
    _store(enabled=False, mode="letsencrypt")
    row = _seed_session(test_session, user_id=9414)
    epoch_before = test_session.query(User).filter(User.id == 9414).one().auth_epoch
    cert_pem, key_pem = _real_key_pair()

    with _AsAdmin():
        response = await async_client.post(
            "/api/tls/upload-cert", files=_upload_files(cert_pem, key_pem)
        )

    assert response.status_code == 200, response.text
    assert "sign in again over HTTPS" in response.json()["message"]

    test_session.expire_all()
    assert test_session.query(UserSession).filter(
        UserSession.id == row.id
    ).one().is_revoked is True
    assert test_session.query(User).filter(
        User.id == 9414
    ).one().auth_epoch == epoch_before + 1


@pytest.mark.asyncio
async def test_replacing_a_certificate_while_tls_is_live_does_not_revoke_sessions(
    async_client, test_session, tls_config_root
):
    """Narrowness, same as the ``/configure`` case: only the transition cuts.

    Re-uploading on an instance that is already terminating TLS is a routine
    certificate rotation. Every session in the jar was minted with ``Secure``
    already, so there is nothing to cut and logging everyone out would be a
    self-inflicted outage.
    """
    _issue_certificate(tls_config_root)
    _store(enabled=True, mode="manual")
    row = _seed_session(test_session, user_id=9415)
    cert_pem, key_pem = _real_key_pair()

    with _AsAdmin():
        response = await async_client.post(
            "/api/tls/upload-cert", files=_upload_files(cert_pem, key_pem)
        )

    assert response.status_code == 200, response.text
    assert "sign in again over HTTPS" not in response.json()["message"]
    test_session.expire_all()
    assert test_session.query(UserSession).filter(
        UserSession.id == row.id
    ).one().is_revoked is False


@pytest.mark.asyncio
async def test_completing_an_acme_challenge_revokes_existing_sessions(
    async_client, test_session, tls_config_root
):
    """The ACME flow activates here, long after ``/configure`` ran.

    ``enabled`` was switched on while no certificate existed — which does NOT
    revoke, by design — so this call is the moment the instance starts
    terminating TLS.
    """
    _store(enabled=True, mode="letsencrypt", domain="ecm.test")
    row = _seed_session(test_session, user_id=9416)
    epoch_before = test_session.query(User).filter(User.id == 9416).one().auth_epoch
    cert_pem, key_pem = _real_key_pair()

    from datetime import datetime, timedelta
    from types import SimpleNamespace

    acme = MagicMock()
    acme.initialize = AsyncMock(return_value=True)
    acme.complete_challenge = AsyncMock(return_value=SimpleNamespace(
        success=True,
        cert_pem=cert_pem,
        key_pem=key_pem,
        chain_pem=None,
        expires_at=datetime.utcnow() + timedelta(days=90),
        error=None,
    ))

    with _AsAdmin(), \
            patch("tls.routes._acme_available", True), \
            patch("tls.routes.ACMEClient", return_value=acme):
        response = await async_client.post("/api/tls/complete-challenge")

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True

    test_session.expire_all()
    assert test_session.query(UserSession).filter(
        UserSession.id == row.id
    ).one().is_revoked is True
    assert test_session.query(User).filter(
        User.id == 9416
    ).one().auth_epoch == epoch_before + 1
