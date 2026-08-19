"""bead 9kwzp.8 — POST/DELETE /api/settings/mcp-api-key must be human-admin.

THE GAP
-------

Both handlers took NO parameters and carried NO dependency of any kind, so any
authenticated caller reached them. That is not merely a missing admin tier: the
static MCP key this endpoint mints is admin-equivalent. ``auth.dependencies``
builds the MCP service principal with ``is_admin=True``, and the global
``auth_middleware`` in ``main.py`` accepts the static key as an alternative to a
JWT for the whole ``/api/`` surface. So an authenticated NON-admin could mint
itself a credential the rest of the app treats as admin — privilege escalation,
not a policy gap. The DELETE half is the mirror image: a denial of service on
every legitimate sidecar integration.

WHY THE HUMAN-ADMIN FAMILY, AND WHY A NEW SIBLING
-------------------------------------------------

The MCP principal is refused for a different reason than it is on the outbound
probes (i4qrp / 9kwzp.6 / 9kwzp.7). Nothing here reaches the network. What is
wrong is that the bearer of a credential would be rotating and revoking THAT
SAME credential: minting returns the new key in the response body, so the only
party that learns it is the caller, and an attacker holding one-shot use of a
leaked key could mint a successor that survives the operator's own rotation.
Key lifecycle belongs to the human operator who owns the credential, not to the
bearer. That is the same principle ``reject_mcp_service_principal_mutation``
already applies to the self-mutation auth routes.

The behaviour is identical to ``RequireHumanAdminForOutboundTest`` but the
403 body is not, which is the whole reason ``mcp_denial_detail`` is a
per-call-site parameter: a caller refused here being told it "cannot run
connection tests" sends incident triage at a network probe that does not exist.
Hence ``RequireHumanAdminForServiceCredential``.
"""
from unittest.mock import AsyncMock, patch

import pytest

from config import DispatcharrSettings
from models import User


MCP_KEY = "mcp-secret-key-9kwzp8"

MCP_API_KEY_PATH = "/api/settings/mcp-api-key"


@pytest.fixture(autouse=True)
def _isolate_private_sidecar_rotation():
    with patch("routers.settings.rotate_mcp_service_credentials"):
        yield

# The key the ROUTER reads as already stored. An angle-bracket placeholder
# rather than a credential-shaped literal: nothing here depends on its shape,
# only on it being distinct from whatever the handler mints, so it follows the
# placeholder half of the convention in ``docs/pytest_conventions.md``
# § "Credential Fixtures in Security Tests".
STORED_KEY_PLACEHOLDER = "<stored-mcp-key-9kwzp8>"

# The two halves of the credential lifecycle, exercised identically.
LIFECYCLE_METHODS = ["POST", "DELETE"]


def _mcp_runtime_settings():
    """Runtime settings whose ``mcp_api_key`` the auth layer will recognize."""
    return DispatcharrSettings(
        url="http://dispatcharr:8000",
        username="u",
        password="p",
        mcp_api_key=MCP_KEY,
    )


def _stored_settings():
    """Settings the ROUTER reads and writes. Distinct object from the runtime
    settings above so a positive control can assert what was persisted without
    the auth layer's copy interfering."""
    return DispatcharrSettings(
        url="http://dispatcharr:8000",
        username="u",
        password="p",
        mcp_api_key=STORED_KEY_PLACEHOLDER,
    )


def _non_admin_user():
    return User(
        id=7180,
        username="regular-user",
        is_admin=False,
        is_active=True,
        auth_provider="local",
    )


def _admin_user():
    return User(
        id=7181,
        username="admin-user",
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )


def _router_mocks():
    """Patch the router's persistence calls at their import site.

    A refused request must reach none of them: ``save_settings`` is the write
    that mints or erases the credential, so asserting it was never called is
    the proof the gate answered before the handler ran.
    """
    return (
        patch("routers.settings.get_settings", return_value=_stored_settings()),
        patch("routers.settings.save_settings"),
        patch("routers.settings.clear_settings_cache"),
    )


# ---------------------------------------------------------------------------
# The authenticated non-admin is refused (the privilege-escalation half)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", LIFECYCLE_METHODS)
@pytest.mark.asyncio
async def test_non_admin_refused_on_mcp_api_key(async_client, method):
    get_mock, save_mock, cache_mock = _router_mocks()
    with patch("auth.dependencies.get_auth_settings") as auth_mock, \
         patch("auth.dependencies.get_current_user",
               new=AsyncMock(return_value=_non_admin_user())), \
         get_mock, save_mock as save_settings, cache_mock:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await async_client.request(method, MCP_API_KEY_PATH)

    assert response.status_code == 403, response.json()
    save_settings.assert_not_called()


# ---------------------------------------------------------------------------
# The MCP service principal is refused (the half RequireAdminIfEnabled misses)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", LIFECYCLE_METHODS)
@pytest.mark.asyncio
async def test_mcp_principal_refused_on_mcp_api_key(async_client, method):
    """The bearer of the static key may not rotate or revoke that same key.

    ``RequireAdminIfEnabled`` would ADMIT this principal — it carries
    ``is_admin=True`` — so this case is the one that distinguishes a real fix
    from one that only looks like a fix in review.
    """
    get_mock, save_mock, cache_mock = _router_mocks()
    with patch("auth.dependencies.get_settings", return_value=_mcp_runtime_settings()), \
         patch("auth.dependencies.get_auth_settings") as auth_mock, \
         get_mock, save_mock as save_settings, cache_mock:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await async_client.request(
            method, MCP_API_KEY_PATH, headers={"Authorization": f"Bearer {MCP_KEY}"},
        )

    assert response.status_code == 403, response.json()
    detail = response.json()["detail"]
    assert "MCP service principal" in detail
    assert "MCP API key" in detail
    assert "connection test" not in detail
    save_settings.assert_not_called()


# ---------------------------------------------------------------------------
# Positive controls — the human admin still reaches both handlers
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_reaches_mcp_api_key_generate_handler(async_client):
    get_mock, save_mock, cache_mock = _router_mocks()
    with patch("auth.dependencies.get_auth_settings") as auth_mock, \
         patch("auth.dependencies.get_current_user",
               new=AsyncMock(return_value=_admin_user())), \
         get_mock, save_mock as save_settings, cache_mock:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await async_client.post(MCP_API_KEY_PATH)

    assert response.status_code == 200, response.json()
    minted = response.json()["mcp_api_key"]
    assert minted and minted != STORED_KEY_PLACEHOLDER
    save_settings.assert_called_once()


@pytest.mark.asyncio
async def test_admin_reaches_mcp_api_key_revoke_handler(async_client):
    get_mock, save_mock, cache_mock = _router_mocks()
    with patch("auth.dependencies.get_auth_settings") as auth_mock, \
         patch("auth.dependencies.get_current_user",
               new=AsyncMock(return_value=_admin_user())), \
         get_mock, save_mock as save_settings, cache_mock:
        auth_mock.return_value.require_auth = True
        auth_mock.return_value.setup_complete = True
        response = await async_client.delete(MCP_API_KEY_PATH)

    assert response.status_code == 200, response.json()
    assert response.json()["status"] == "revoked"
    save_settings.assert_called_once()
    assert save_settings.call_args.args[0].mcp_api_key == ""


class TestSetupModeStillAllowed:
    """An UNOWNED instance must keep working, whatever its auth flags say.

    No first-run path calls either endpoint today — ``SetupPage.tsx`` imports
    exactly one API function (``completeSetup``) and the only caller of
    ``generateMCPApiKey`` / ``revokeMCPApiKey`` is ``MCPSettingsSection``, which
    is rendered from the post-auth Settings tab behind its own ``isAdmin``
    guard. This pins the behaviour anyway, because an instance running with
    ``require_auth=False`` is a supported configuration and the gate must not
    lock its operator out of MCP setup.

    NARROWED BY BEAD jy006. This class used to parametrize a third posture,
    ``require_auth=False, setup_complete=True``, and assert 200 there too. That
    combination now means "auth is off AND this instance has an operator
    identity", which is precisely the case the PO decided must require a real
    human admin: the key this route mints is a persistent, admin-equivalent
    bearer credential that survives the operator turning authentication back
    on. The two postures left here both describe an instance with NO operator
    identity, where the gate still no-ops.

    The full jy006 matrix for this route — anonymous, non-admin, admin and MCP
    principal, against both identity signals — is in
    ``test_jy006_auth_disabled_identity_primitives.py``.
    """

    @pytest.mark.parametrize("method", LIFECYCLE_METHODS)
    @pytest.mark.parametrize(
        "require_auth,setup_complete",
        [(True, False), (False, False)],
        ids=["setup-incomplete", "both"],
    )
    @pytest.mark.asyncio
    async def test_anonymous_caller_reaches_handler_in_setup_mode(
        self, async_client, method, require_auth, setup_complete
    ):
        get_mock, save_mock, cache_mock = _router_mocks()
        with patch("auth.dependencies.get_auth_settings") as auth_mock, \
             get_mock, save_mock as save_settings, cache_mock:
            auth_mock.return_value.require_auth = require_auth
            auth_mock.return_value.setup_complete = setup_complete
            response = await async_client.request(method, MCP_API_KEY_PATH)

        assert response.status_code == 200, response.json()
        save_settings.assert_called_once()
