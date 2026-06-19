"""Unit tests for the DispatcharrClient settings/agents helpers
(enhancedchannelmanager-0i2vt.13 — Phase 2 settings/agents importer).

These client methods were ADDED by 0i2vt.13 to back the settings_agents restore
importer (none existed before). They cover four categories' upstream calls:

* user agents — GET / POST / DELETE on the core useragents endpoint.
* DVR rules   — GET / POST / DELETE on the DVR rules endpoint.
* core settings — GET (read) + per-key PATCH (apply ONE key conservatively).

Mocking pattern follows test_dispatcharr_client_user_crud.py: construct a real
``DispatcharrClient`` and ``patch.object(client, "_request", AsyncMock(...))`` so
the exact method + path forwarded upstream is asserted without a live instance.
"""
import pytest
from unittest.mock import AsyncMock, patch

import httpx

from config import DispatcharrSettings
from dispatcharr_client import (
    DispatcharrClient,
    _CORE_SETTINGS_PATH,
    _DVR_RULES_PATH,
    _USER_AGENTS_PATH,
)


def _response(status_code: int, json_body=None, text: str = "", content=b"x"):
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_body if json_body is not None else {}
    resp.text = text
    resp.content = content

    def _raise_for_status():
        if status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {status_code}", request=None, response=resp)

    resp.raise_for_status = _raise_for_status
    return resp


def _make_client():
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="api_key",
        dispatcharr_api_key="key-123",
    )
    return DispatcharrClient(settings)


# --- user agents -----------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_agents_gets_endpoint():
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(200, [{"id": 1, "name": "VLC"}]))
        with patch.object(client, "_request", rm):
            result = await client.get_user_agents()
        assert result == [{"id": 1, "name": "VLC"}]
        method, path = rm.await_args.args
        assert (method, path) == ("GET", _USER_AGENTS_PATH)
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_create_user_agent_posts_endpoint():
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(201, {"id": 5, "name": "Kodi"}))
        with patch.object(client, "_request", rm):
            result = await client.create_user_agent({"name": "Kodi"})
        assert result == {"id": 5, "name": "Kodi"}
        method, path = rm.await_args.args
        assert (method, path) == ("POST", _USER_AGENTS_PATH)
        assert rm.await_args.kwargs["json"] == {"name": "Kodi"}
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_delete_user_agent_deletes_by_id():
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(204))
        with patch.object(client, "_request", rm):
            await client.delete_user_agent(7)
        method, path = rm.await_args.args
        assert (method, path) == ("DELETE", f"{_USER_AGENTS_PATH}7/")
    finally:
        await client._client.aclose()


# --- DVR rules -------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dvr_rules_gets_endpoint():
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(200, [{"id": 1, "name": "R1"}]))
        with patch.object(client, "_request", rm):
            result = await client.get_dvr_rules()
        assert result == [{"id": 1, "name": "R1"}]
        method, path = rm.await_args.args
        assert (method, path) == ("GET", _DVR_RULES_PATH)
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_create_dvr_rule_posts_endpoint():
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(201, {"id": 9, "name": "R1", "channel": 105}))
        with patch.object(client, "_request", rm):
            result = await client.create_dvr_rule({"name": "R1", "channel": 105})
        assert result["id"] == 9
        method, path = rm.await_args.args
        assert (method, path) == ("POST", _DVR_RULES_PATH)
        assert rm.await_args.kwargs["json"]["channel"] == 105
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_delete_dvr_rule_deletes_by_id():
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(204))
        with patch.object(client, "_request", rm):
            await client.delete_dvr_rule(3)
        method, path = rm.await_args.args
        assert (method, path) == ("DELETE", f"{_DVR_RULES_PATH}3/")
    finally:
        await client._client.aclose()


# --- core settings ---------------------------------------------------------


@pytest.mark.asyncio
async def test_get_core_settings_gets_endpoint():
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(200, {"ui_theme": "dark"}))
        with patch.object(client, "_request", rm):
            result = await client.get_core_settings()
        assert result == {"ui_theme": "dark"}
        method, path = rm.await_args.args
        assert (method, path) == ("GET", _CORE_SETTINGS_PATH)
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_update_core_setting_patches_single_key():
    """A single key is PATCHed at its sub-path with a {value:...} body — never a
    bulk PUT that could clobber unrelated keys."""
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(200, {"key": "ui_theme", "value": "dark"}))
        with patch.object(client, "_request", rm):
            result = await client.update_core_setting("ui_theme", "dark")
        assert result == {"key": "ui_theme", "value": "dark"}
        method, path = rm.await_args.args
        assert (method, path) == ("PATCH", f"{_CORE_SETTINGS_PATH}ui_theme/")
        assert rm.await_args.kwargs["json"] == {"value": "dark"}
    finally:
        await client._client.aclose()
