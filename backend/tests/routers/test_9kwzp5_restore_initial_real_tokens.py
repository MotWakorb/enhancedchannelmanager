"""bead enhancedchannelmanager-9kwzp.5 item 2 — restore-initial against REAL tokens.

THE PROPERTY THIS FILE PINS
---------------------------

    **is_admin is resolved from the DATABASE ROW, not from the token body.**

That is the reviewer's wording, attached to the bead as a condition on its
framing, and it is the whole point of the file. It is not "add better tests".

The security review of the lf29s branch traced the property by hand and
confirmed it holds: ``routers.backup._caller_is_human_admin`` ->
``auth.dependencies.get_current_user`` -> ``auth.tokens.decode_token`` gives
HS256-pinned decode, expiry enforcement, jti revocation, ``sub`` -> DB lookup,
``is_active`` enforcement, ``is_admin`` read off the loaded row, and MCP-service
-principal rejection.

The gap was that nothing would FAIL if a refactor swapped ``get_current_user``
for something like ``decode_token_safe(token).get("is_admin")``. Every case in
``test_backup.py::TestRestoreInitialIdentityGate`` patches
``routers.backup.get_current_user`` AND ``routers.backup.get_token_from_request``,
so no test in the suite ever presented a real signed token to this endpoint.
Those tests pin the WIRING of every branch and still do; this file adds the
BEHAVIOUR underneath them. ``test_jy006_auth_disabled_identity_primitives.py``
owns the auth-disabled decision for the other two identity primitives and does
so with the same mocks; the auth-disabled cases here are its real-token
counterpart for restore-initial specifically, not a second copy.

WHAT IS AND IS NOT MOCKED HERE
------------------------------

NOT mocked, on any test in this file: ``get_token_from_request``,
``get_current_user``, ``decode_token``, ``is_mcp_service_principal``,
``_is_mcp_service_token``, or the users table. Tokens are minted with
``auth.tokens.create_access_token`` and travel in a real ``Authorization``
header; the user rows are real rows in the test session.

Mocked: the auth POSTURE (``get_auth_settings``), Dispatcharr's
``is_configured``, and — on the paths that reach the handler body — the
filesystem/database side effects of the restore itself. Those are inputs to the
scenario, not the mechanism under test.

``main.get_auth_settings`` is pinned to ``setup_complete=False`` deliberately.
That leaves ``main.auth_middleware`` inactive, so every refusal below is
provably ``_guard_initial_restore``'s own verdict rather than the middleware
answering first — and it is not a contrived posture: ``setup_complete`` False
with real user rows present is the qg14z state of every instance that ran the
setup wizard, and the state in which the live anonymous-takeover reproduction
succeeded.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from jose import jwt

from auth.tokens import ALGORITHM, _get_secret_key, create_access_token
from models import User

from .test_backup import _make_backup_zip


MCP_KEY = "9kwzp5-static-mcp-service-key"


def _auth_settings(require_auth=True, setup_complete=False):
    stub = MagicMock()
    stub.require_auth = require_auth
    stub.setup_complete = setup_complete
    return stub


def _seed_user(session, username, is_admin, is_active=True) -> User:
    """Create a REAL user row and return it, ids assigned by the database."""
    user = User(
        username=username,
        email="%s@example.com" % username,
        is_admin=is_admin,
        is_active=is_active,
        auth_provider="local",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _unconfigured_settings() -> MagicMock:
    stub = MagicMock()
    stub.is_configured.return_value = False
    return stub


def _post(client, token=None):
    """POST a backup ZIP, optionally bearing ``token``."""
    headers = {"Authorization": "Bearer %s" % token} if token else {}
    return client.post(
        "/api/backup/restore-initial",
        files={"file": ("backup.zip", _make_backup_zip(), "application/zip")},
        headers=headers,
    )


class _Scenario:
    """Context manager assembling one posture, with nothing auth-related mocked.

    ``allow_restore`` additionally neutralises the restore's side effects, which
    is required on any case expected to reach the handler body — otherwise the
    test would rewrite the real config directory.
    """

    def __init__(self, tmp_path=None, require_auth=True, allow_restore=False):
        self.tmp_path = tmp_path
        self.require_auth = require_auth
        self.allow_restore = allow_restore
        self._patches = []

    def __enter__(self):
        posture = _auth_settings(require_auth=self.require_auth)
        self._patches = [
            patch("routers.backup.get_settings", return_value=_unconfigured_settings()),
            patch("auth.dependencies.get_auth_settings", return_value=posture),
            patch("main.get_auth_settings", return_value=posture),
        ]
        if self.allow_restore:
            self._patches += [
                patch("routers.backup.CONFIG_DIR", self.tmp_path),
                patch("routers.backup.CONFIG_FILE", self.tmp_path / "settings.json"),
                patch("routers.backup.JOURNAL_DB_FILE", self.tmp_path / "journal.db"),
                patch("routers.backup.close_db"),
                patch("routers.backup.init_db"),
                patch("routers.backup.clear_settings_cache"),
                patch("routers.backup.reset_client"),
            ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


# ---------------------------------------------------------------------------
# The reviewer's minimum to close: a real token each side of the admin line
# ---------------------------------------------------------------------------
class TestRealTokenAdminLine:
    @pytest.mark.asyncio
    async def test_real_token_for_a_non_admin_row_is_refused(
        self, async_client, test_session
    ):
        """A genuinely valid, genuinely signed token that is simply not an admin's."""
        user = _seed_user(test_session, "regular", is_admin=False)
        token = create_access_token(user.id, user.username)

        with _Scenario():
            response = await _post(async_client, token)

        assert response.status_code == 403
        assert "admin" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_real_token_for_an_admin_row_is_allowed(
        self, async_client, test_session, tmp_path
    ):
        """The positive control — without it the 403 above could be a hard lockout."""
        user = _seed_user(test_session, "operator", is_admin=True)
        token = create_access_token(user.id, user.username)

        with _Scenario(tmp_path, allow_restore=True):
            response = await _post(async_client, token)

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# The named property, from both directions
# ---------------------------------------------------------------------------
class TestIsAdminComesFromTheRow:
    """Same token, opposite verdicts, because only the ROW changed.

    A differential pair is what makes this unfakeable by a token-body read. In
    both cases the JWT is minted once and never re-issued: nothing in the token
    differs between the request that is allowed and the request that is not.
    """

    @pytest.mark.asyncio
    async def test_demoting_the_row_refuses_a_token_minted_while_admin(
        self, async_client, test_session
    ):
        user = _seed_user(test_session, "demoted", is_admin=True)
        token = create_access_token(user.id, user.username)

        user.is_admin = False
        test_session.commit()

        with _Scenario():
            response = await _post(async_client, token)

        assert response.status_code == 403, (
            "restore-initial honoured a token minted while its subject was an "
            "admin after the row was demoted — is_admin is being read from "
            "somewhere other than the database row"
        )

    @pytest.mark.asyncio
    async def test_promoting_the_row_admits_a_token_minted_while_non_admin(
        self, async_client, test_session, tmp_path
    ):
        user = _seed_user(test_session, "promoted", is_admin=False)
        token = create_access_token(user.id, user.username)

        user.is_admin = True
        test_session.commit()

        with _Scenario(tmp_path, allow_restore=True):
            response = await _post(async_client, token)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_an_is_admin_claim_in_the_token_body_confers_nothing(
        self, async_client, test_session
    ):
        """A VALIDLY SIGNED token that simply asserts it is an admin's.

        Signed with the real key via ``auth.tokens._get_secret_key`` — the
        private accessor is used on purpose, because a token signed with
        anything else would be rejected at the signature and would prove
        nothing about where the authorization decision reads from. Signature
        verification passes here; the request is refused on the row.
        """
        user = _seed_user(test_session, "claimant", is_admin=False)
        forged = jwt.encode(
            {
                "sub": str(user.id),
                "username": user.username,
                "type": "access",
                "is_admin": True,
                "admin": True,
                "exp": jwt.get_unverified_claims(
                    create_access_token(user.id, user.username)
                )["exp"],
            },
            _get_secret_key(),
            algorithm=ALGORITHM,
        )

        with _Scenario():
            response = await _post(async_client, forged)

        assert response.status_code == 403, (
            "an is_admin claim in the token body was honoured — the gate must "
            "read is_admin from the users row, never from the JWT payload"
        )


# ---------------------------------------------------------------------------
# The rest of the chain the reviewer verified by hand and left unpinned
# ---------------------------------------------------------------------------
class TestRealTokenValidationChain:
    @pytest.mark.asyncio
    async def test_token_signed_with_the_wrong_key_is_refused(
        self, async_client, test_session
    ):
        """Well-formed, unexpired, correct claims — only the signature is wrong."""
        user = _seed_user(test_session, "operator", is_admin=True)
        real = create_access_token(user.id, user.username)
        payload = jwt.get_unverified_claims(real)
        forged = jwt.encode(payload, "not-the-ecm-signing-key", algorithm=ALGORITHM)
        assert forged != real

        with _Scenario():
            response = await _post(async_client, forged)

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_valid_signature_for_a_nonexistent_uid_is_refused(
        self, async_client, test_session
    ):
        """ECM's own signature over a subject that has no row."""
        _seed_user(test_session, "operator", is_admin=True)
        token = create_access_token(987654321, "ghost-admin")

        with _Scenario():
            response = await _post(async_client, token)

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_expired_admin_token_is_refused(self, async_client, test_session):
        user = _seed_user(test_session, "operator", is_admin=True)
        token = create_access_token(
            user.id, user.username, expires_delta=timedelta(minutes=-5)
        )

        with _Scenario():
            response = await _post(async_client, token)

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_deactivated_admin_row_is_refused(self, async_client, test_session):
        """``is_active`` is enforced on the row, same as ``is_admin``."""
        user = _seed_user(test_session, "suspended", is_admin=True, is_active=False)
        token = create_access_token(user.id, user.username)

        with _Scenario():
            response = await _post(async_client, token)

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_the_real_static_mcp_key_is_refused(
        self, async_client, test_session
    ):
        """Not the mocked principal — the actual configured key, over the wire.

        ``auth.dependencies._is_mcp_service_token`` recognises it and
        ``get_current_user`` returns the admin-equivalent service principal;
        ``_caller_is_human_admin`` then rejects it as non-human (bead 6n76m).
        Nothing on this path is patched except the key's configured VALUE.
        """
        _seed_user(test_session, "operator", is_admin=True)
        mcp_settings = MagicMock()
        mcp_settings.mcp_api_key = MCP_KEY

        with _Scenario(), \
             patch("auth.dependencies.get_settings", return_value=mcp_settings):
            response = await _post(async_client, MCP_KEY)

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_no_token_at_all_is_refused(self, async_client, test_session):
        """The anonymous baseline, reached through the real extraction path."""
        _seed_user(test_session, "operator", is_admin=True)

        with _Scenario():
            response = await _post(async_client)

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# The same property under require_auth: false (bead jy006's decision)
# ---------------------------------------------------------------------------
class TestRealTokensWhenAuthIsDisabled:
    """jy006 made this route refuse anonymity even with authentication off.

    ``test_jy006_auth_disabled_identity_primitives.py`` and
    ``TestRestoreInitialIdentityGate`` establish that decision with mocked
    identity; these two cases show the real token path behaves identically,
    which is what makes the mode's refusal a refusal of ANONYMITY rather than a
    short-circuit that happens to fall the right way.
    """

    @pytest.mark.asyncio
    async def test_non_admin_row_still_refused(self, async_client, test_session):
        user = _seed_user(test_session, "regular", is_admin=False)
        token = create_access_token(user.id, user.username)

        with _Scenario(require_auth=False):
            response = await _post(async_client, token)

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_row_still_allowed(
        self, async_client, test_session, tmp_path
    ):
        user = _seed_user(test_session, "operator", is_admin=True)
        token = create_access_token(user.id, user.username)

        with _Scenario(tmp_path, require_auth=False, allow_restore=True):
            response = await _post(async_client, token)

        assert response.status_code == 200
