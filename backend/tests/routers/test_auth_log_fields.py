"""Synthetic caller fields at real auth routes (nymj6, authored 2026-09-13)."""

import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

from auth.password import hash_password
from auth.providers.dispatcharr import (
    DispatcharrAuthenticationError,
    DispatcharrNetworkPolicyError,
    DispatcharrRateLimitError,
)
from auth.settings import AuthSettings
from models import User
from observability import JsonFormatter


_CANARY = "synthetic-auth-20260913-" + "S" * 48
_USERNAME = "caller " + _CANARY + "\r\n\x1b\u202e " + "label " * 40
_PASSWORD = "synthetic-local-20260913-" + "P" * 16


def _assert_bounded_auth_record(caplog, prefix):
    records = [r for r in caplog.records if r.name == "auth.routes" and prefix in r.getMessage()]
    assert len(records) == 1
    record = records[0]
    field = record.args[0]
    assert len(field) <= 80
    assert all(" " <= char <= "~" for char in field)
    assert field.startswith("caller [redacted]")
    rendered = JsonFormatter().format(record)
    assert _CANARY not in rendered
    assert _PASSWORD not in rendered
    assert json.loads(rendered)["msg"].endswith("from 127.0.0.1")


@pytest.mark.parametrize("state,detail,prefix", [
    ("missing", "Invalid username or password", "nonexistent user"),
    ("nonlocal", "Please use your configured authentication provider to log in", "Non-local user"),
    ("wrong_password", "Invalid username or password", "Failed login attempt"),
    ("disabled", "User account is disabled", "disabled user"),
])
@pytest.mark.asyncio
async def test_local_denials_bound_username_without_changing_auth(
    async_client, test_session, caplog, state, detail, prefix,
):
    if state != "missing":
        test_session.add(User(
            username=_USERNAME,
            auth_provider="oidc" if state == "nonlocal" else "local",
            password_hash=hash_password(_PASSWORD),
            is_active=state != "disabled",
        ))
        test_session.commit()
    caplog.set_level(logging.WARNING, logger="auth.routes")
    response = await async_client.post("/api/auth/login", json={
        "username": _USERNAME,
        "password": "synthetic-wrong-password" if state == "wrong_password" else _PASSWORD,
    })
    assert response.status_code == 401
    assert response.json() == {"detail": detail}
    _assert_bounded_auth_record(caplog, prefix)


@pytest.mark.parametrize("error_type,status,prefix", [
    (DispatcharrAuthenticationError, 401, "Dispatcharr auth failed"),
    (DispatcharrNetworkPolicyError, 403, "network policy rejected"),
    (DispatcharrRateLimitError, 429, "rate-limited login"),
])
@pytest.mark.asyncio
async def test_dispatcharr_denials_bound_username_and_keep_public_response(
    async_client, caplog, error_type, status, prefix,
):
    settings = AuthSettings()
    settings.dispatcharr.enabled = True
    client = AsyncMock()
    client.authenticate.side_effect = error_type("Synthetic provider refusal")
    caplog.set_level(logging.WARNING, logger="auth.routes")
    with patch("auth.routes.get_auth_settings", return_value=settings), patch(
        "auth.providers.dispatcharr.DispatcharrClient",
    ) as factory:
        factory.return_value.__aenter__.return_value = client
        response = await async_client.post("/api/auth/dispatcharr/login", json={
            "username": _USERNAME, "password": _PASSWORD,
        })
    assert response.status_code == status
    assert response.json() == {"detail": "Synthetic provider refusal"}
    assert response.headers.get("Retry-After") == ("60" if status == 429 else None)
    client.authenticate.assert_awaited_once_with(_USERNAME, _PASSWORD)
    _assert_bounded_auth_record(caplog, prefix)
