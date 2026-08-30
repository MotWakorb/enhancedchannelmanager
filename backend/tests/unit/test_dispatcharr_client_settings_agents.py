"""Unit tests for the DispatcharrClient settings/agents helpers
(enhancedchannelmanager-0i2vt.13 — Phase 2 settings/agents importer).

These client methods were ADDED by 0i2vt.13 to back the settings_agents restore
importer (none existed before). They cover four categories' upstream calls:

* user agents — GET / POST / DELETE on the core useragents endpoint.
* DVR rules   — GET / POST / DELETE on the recurring-recording-rules endpoint
  (retargeted from the non-existent ``/api/dvr/rules/`` by
  enhancedchannelmanager-lsa0s; see the DVR section below).
* core settings — GET (read) + per-key PATCH (apply ONE key conservatively).

Mocking pattern follows test_dispatcharr_client_user_crud.py: construct a real
``DispatcharrClient`` and ``patch.object(client, "_request", AsyncMock(...))`` so
the exact method + path forwarded upstream is asserted without a live instance.
"""
import json
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

import httpx

from config import DispatcharrSettings
from dispatcharr_client import (
    DispatcharrClient,
    _CORE_SETTINGS_PATH,
    _DVR_RECURRING_RULES_PATH,
    _USER_AGENTS_PATH,
)

_RECORDED_CORE_SETTINGS = json.loads(
    (
        Path(__file__).parent.parent
        / "fixtures"
        / "dispatcharr_core_settings_recorded.json"
    ).read_text()
)

_RECORDED_OPENAPI = json.loads(
    (
        Path(__file__).parent.parent / "fixtures" / "dispatcharr_openapi_recorded.json"
    ).read_text()
)

_RECORDED_DVR = json.loads(
    (
        Path(__file__).parent.parent
        / "fixtures"
        / "dispatcharr_dvr_recurring_rules_recorded.json"
    ).read_text()
)


def test_recorded_schema_keys_core_settings_detail_route_by_integer_id():
    """The RECORDED Dispatcharr 0.28.2 OpenAPI document is the authority for the
    core-settings detail route — and it is keyed by an INTEGER id.

    This is the premise the ``update_core_setting`` fix rests on
    (enhancedchannelmanager-q6xjl). If a future Dispatcharr adds a key-string
    lookup, re-record the fixture and this test says so out loud.
    """
    paths = _RECORDED_OPENAPI["schema"]["paths"]
    assert "/api/core/settings/{id}/" in paths
    # No key-string detail route exists — the 404 the restore hit.
    assert "/api/core/settings/{key}/" not in paths
    patch_params = paths["/api/core/settings/{id}/"]["patch"]["parameters"]
    id_param = next(p for p in patch_params if p["name"] == "id")
    assert id_param["in"] == "path"
    assert id_param["schema"]["type"] == "integer"


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
#
# lsa0s: the DVR-rule methods used to point at ``/api/dvr/rules/``, a path that
# has NO route on Dispatcharr 0.28.2 — it fell through to the SPA catch-all and
# answered 200 text/html, so ``get_dvr_rules()`` raised a JSONDecodeError and
# the backup exporter wrote a ``_warning`` stub for the category on EVERY run.
# The tests below are pinned to the RECORDED live surface, not to a guess.


def test_recorded_schema_has_no_route_for_the_old_dvr_rules_path():
    """The former ``_DVR_RULES_PATH`` is absent from the live 0.28.2 document.

    The recorded capture also keeps what that path actually answered: 200 with
    ``text/html`` (the SPA shell), which is why the JSON parse blew up rather
    than surfacing an honest 404.
    """
    dead = _RECORDED_DVR["dead_dvr_rules_path"]
    assert dead["path"] == "/api/dvr/rules/"
    assert dead["present_in_openapi_paths"] is False
    assert dead["status_code"] == 200
    assert dead["content_type"].startswith("text/html")


def test_recorded_schema_keys_dvr_rules_to_the_recurring_rules_resource():
    """The RECORDED 0.28.2 OpenAPI document is the authority for the DVR-rule
    resource ECM's client targets: ``/api/channels/recurring-rules/``.

    Pins the three operations the importer needs (list, create, delete-by-id)
    and the two contract properties the importer relies on: a ``name`` identity
    field and a single integer ``channel`` FK.
    """
    paths = _RECORDED_DVR["openapi_slice"]["paths"]
    assert _DVR_RECURRING_RULES_PATH in paths
    assert set(paths[_DVR_RECURRING_RULES_PATH]) >= {"get", "post"}
    detail = _DVR_RECURRING_RULES_PATH + "{id}/"
    assert "delete" in paths[detail]

    schema = _RECORDED_DVR["openapi_slice"]["components"]["schemas"][
        "RecurringRecordingRule"
    ]
    assert schema["properties"]["name"]["type"] == "string"
    assert schema["properties"]["channel"]["type"] == "integer"
    # ``name`` is NOT required upstream — the importer must survive a blank one.
    assert "name" not in schema["required"]


@pytest.mark.asyncio
async def test_get_dvr_rules_parses_the_recorded_live_list_response():
    """The recorded live GET body is a BARE JSON array, not a paginated
    envelope — ``get_dvr_rules`` returns it as a list."""
    recorded = _RECORDED_DVR["recurring_rules_list_response"]
    assert recorded["path"] == _DVR_RECURRING_RULES_PATH
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(200, recorded["body"]))
        with patch.object(client, "_request", rm):
            result = await client.get_dvr_rules()
        assert result == []
        method, path = rm.await_args.args
        assert (method, path) == ("GET", _DVR_RECURRING_RULES_PATH)
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_get_dvr_rules_gets_endpoint():
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(200, [{"id": 1, "name": "R1"}]))
        with patch.object(client, "_request", rm):
            result = await client.get_dvr_rules()
        assert result == [{"id": 1, "name": "R1"}]
        method, path = rm.await_args.args
        assert (method, path) == ("GET", _DVR_RECURRING_RULES_PATH)
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_get_dvr_rules_unwraps_a_paginated_envelope():
    """A destination that paginates this list must not degrade to "no rules".

    A bare dict would make the collision index empty and duplicate every rule on
    restore, so the client normalizes both DRF shapes to a list.
    """
    client = _make_client()
    try:
        envelope = {"count": 1, "next": None, "results": [{"id": 1, "name": "R1"}]}
        rm = AsyncMock(return_value=_response(200, envelope))
        with patch.object(client, "_request", rm):
            result = await client.get_dvr_rules()
        assert result == [{"id": 1, "name": "R1"}]
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
        assert (method, path) == ("POST", _DVR_RECURRING_RULES_PATH)
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
        assert (method, path) == ("DELETE", f"{_DVR_RECURRING_RULES_PATH}3/")
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
async def test_get_core_setting_id_map_from_recorded_response():
    """The key->id map is built from the RECORDED Dispatcharr 0.28.2 list payload.

    Regression pin for enhancedchannelmanager-q6xjl: the detail route is keyed by
    the integer row id, and the recorded ids are non-contiguous and unrelated to
    list position — so the map must come from the row's own ``id``, never from an
    index or the key string.
    """
    recorded = _RECORDED_CORE_SETTINGS["core_settings_list"]
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(200, recorded))
        with patch.object(client, "_request", rm):
            id_map = await client.get_core_setting_id_map()
        method, path = rm.await_args.args
        assert (method, path) == ("GET", _CORE_SETTINGS_PATH)
        assert id_map == {
            "network_access": 6,
            "dvr_settings": 18,
            "backup_settings": 19,
            "user_limit_settings": 21,
            "stream_settings": 17,
            "system_settings": 20,
            "proxy_settings": 7,
        }
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_get_core_setting_id_map_handles_paginated_envelope():
    """A DRF-paginated ``{"results": [...]}`` body resolves the same as a bare list."""
    client = _make_client()
    try:
        body = {"count": 1, "results": [{"id": 42, "key": "ui_theme", "value": "dark"}]}
        rm = AsyncMock(return_value=_response(200, body))
        with patch.object(client, "_request", rm):
            id_map = await client.get_core_setting_id_map()
        assert id_map == {"ui_theme": 42}
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_get_core_setting_id_map_follows_next_across_multiple_pages():
    """A DRF-paginated envelope with a non-null ``next`` is followed to
    completion — the docstring has long claimed paginated-envelope support, but
    only page 1 was ever actually read. A future/larger settings list must not
    silently lose every key past page 1 (which would fail those keys
    DEPENDENCY_UNRESOLVED even though the destination genuinely has them)."""
    client = _make_client()
    try:
        page1 = _response(
            200,
            {
                "count": 2,
                "next": "http://dispatcharr:8000/api/core/settings/?page=2",
                "results": [{"id": 1, "key": "network_access", "value": "x"}],
            },
        )
        page2 = _response(
            200,
            {
                "count": 2,
                "next": None,
                "results": [{"id": 2, "key": "ui_theme", "value": "dark"}],
            },
        )
        rm = AsyncMock(side_effect=[page1, page2])
        with patch.object(client, "_request", rm):
            id_map = await client.get_core_setting_id_map()

        assert id_map == {"network_access": 1, "ui_theme": 2}
        assert rm.await_count == 2
        # Second request advances the page param — never re-requests page 1.
        second_call_kwargs = rm.await_args_list[1].kwargs
        assert second_call_kwargs.get("params") == {"page": 2}
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_get_core_setting_id_map_page_cap_exceeded_fails_loudly():
    """A ``next`` chain that never terminates is bounded, not followed forever.

    Exceeding the defensive page cap raises loudly (the resolver's broad
    ``except Exception`` then fails every key UPSTREAM_API_ERROR) rather than
    hanging the restore run or silently returning a partial map.
    """
    client = _make_client()
    try:
        def _endless_page(*args, **kwargs):
            return _response(
                200,
                {
                    "count": 9999,
                    "next": "http://dispatcharr:8000/api/core/settings/?page=999",
                    "results": [{"id": 1, "key": "k", "value": "v"}],
                },
            )

        rm = AsyncMock(side_effect=_endless_page)
        with patch.object(client, "_request", rm):
            with pytest.raises(RuntimeError):
                await client.get_core_setting_id_map()

        # Bounded: it gave up at the cap, it did not loop forever.
        assert rm.await_count <= 21
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_get_core_setting_id_map_drops_unusable_rows():
    """Rows without a usable string key or an integer id are dropped, not guessed.

    Mirrors ``routers.backup._normalize_core_settings``: a row we cannot key is
    better absent (the caller then fails that key explicitly) than mapped to a
    wrong id that would PATCH an unrelated setting.
    """
    client = _make_client()
    try:
        body = [
            {"id": 1, "key": "ui_theme"},
            {"id": 2},  # no key
            {"key": "no_id"},  # no id
            {"id": "not-an-int", "key": "bad_id"},
            "not-a-row",
        ]
        rm = AsyncMock(return_value=_response(200, body))
        with patch.object(client, "_request", rm):
            id_map = await client.get_core_setting_id_map()
        assert id_map == {"ui_theme": 1}
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_update_core_setting_patches_by_integer_row_id():
    """A single setting is PATCHed at its INTEGER-id sub-path with a {value:...}
    body — never a bulk PUT, and never a key-string URL.

    Regression pin for enhancedchannelmanager-q6xjl: Dispatcharr's
    ``CoreSettingsViewSet`` is a plain ModelViewSet with the default ``pk``
    lookup, so ``/api/core/settings/<key>/`` matches no route and 404s. The
    destination row id must be resolved first and used in the path.
    """
    client = _make_client()
    try:
        rm = AsyncMock(return_value=_response(200, {"key": "ui_theme", "value": "dark"}))
        with patch.object(client, "_request", rm):
            result = await client.update_core_setting(42, "dark")
        assert result == {"key": "ui_theme", "value": "dark"}
        method, path = rm.await_args.args
        assert (method, path) == ("PATCH", f"{_CORE_SETTINGS_PATH}42/")
        assert rm.await_args.kwargs["json"] == {"value": "dark"}
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_update_core_setting_error_carries_no_payload():
    """On upstream failure the re-raised error message must be GENERIC — it must
    NOT echo the setting name, the setting value, or any upstream response body.

    This is the clear-text-logging hygiene contract (bead 0i2vt.13): a setting
    value can be a secret, and the shared ``_request`` exception sink logs the
    raised exception's text. ``update_core_setting`` therefore re-raises a
    payload-free message so nothing sensitive can propagate to that log.
    """
    client = _make_client()
    secret_value = "super-secret-token-value-9f3a"
    setting_name = "smtp_relay_endpoint"
    try:
        # _request raises with an exception text that echoes the value+name —
        # exactly the leak we must not let propagate.
        leaky = RuntimeError(
            f"500 applying {setting_name}=value '{secret_value}' upstream body"
        )
        rm = AsyncMock(side_effect=leaky)
        with patch.object(client, "_request", rm):
            with pytest.raises(Exception) as excinfo:
                await client.update_core_setting(31, secret_value)
        message = str(excinfo.value)
        assert secret_value not in message
        assert setting_name not in message
        # And the original leaky exception is not chained back in (from None).
        assert excinfo.value.__cause__ is None
    finally:
        await client._client.aclose()
