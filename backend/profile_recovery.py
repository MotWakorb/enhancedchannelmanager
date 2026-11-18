"""Bind post-create profile recovery to the request's operator and Dispatcharr client."""

import hmac
import json

from fastapi import HTTPException, Request

from auth.dependencies import get_current_user, get_token_from_request, is_mcp_service_principal
from auth.settings import get_auth_settings, get_jwt_secret_key
from config import DispatcharrSettings
from database import get_session
from dispatcharr_client import DispatcharrClient


async def recovery_operator(request: Request) -> str:
    """Resolve signed-in users even when ordinary routes allow anonymous access."""
    settings = get_auth_settings()
    if not get_token_from_request(request) and not (settings.require_auth and settings.setup_complete):
        return "anonymous"
    session = get_session()
    try:
        user = await get_current_user(request, session)
        if is_mcp_service_principal(user):
            raise HTTPException(403, "Profile recovery requires an ECM operator")
        return f"{user.auth_provider}#{user.id}"
    finally:
        session.close()


def _connection_scope(settings: DispatcharrSettings, base_url: str) -> list:
    auth = (
        [settings.dispatcharr_api_key or settings.api_key]
        if settings.auth_method == "api_key"
        else [settings.username, settings.password]
    )
    return [base_url, settings.auth_method, auth]


def _scope_digest(domain: bytes, scope: list) -> str:
    return hmac.digest(get_jwt_secret_key().encode(), domain + b"\0" + json.dumps(scope).encode(), "sha256").hex()


def recovery_destination(settings: DispatcharrSettings, *, base_url: str | None = None) -> str:
    """Non-authorizing connection identity available before any create."""
    return _scope_digest(
        b"ecm.profile-destination.v1",
        _connection_scope(settings, settings.url.rstrip("/") if base_url is None else base_url),
    )


def recovery_destination_matches(expected: str, client: DispatcharrClient) -> bool:
    return len(expected) == 64 and expected.isascii() and hmac.compare_digest(
        expected, recovery_destination(client.settings, base_url=client.base_url),
    )


def recovery_binding(client: DispatcharrClient, operator: str) -> str:
    """Return an opaque scope identifier, not a credential or authorization grant.

    Use the actual client, not a second settings lookup. A persistent ECM
    secret keeps bindings usable across restarts on the same configuration.
    A distinct domain separates the MAC from other uses of the JWT key.
    """
    return _scope_digest(b"ecm.profile-recovery.v1", [operator, *_connection_scope(client.settings, client.base_url)])
