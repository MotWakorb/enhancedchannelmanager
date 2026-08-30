"""Unit tests for DispatcharrClient user helpers
(enhancedchannelmanager-l1p4p, Phase 2 Users import — crown-jewel restore).

The Users importer restores Dispatcharr (Django) user accounts. Per spike
tsfv0 (live-confirmed vs Dispatcharr 0.26.0):

* No password/hash ever crosses the boundary. ``create_user`` POSTs to
  /api/accounts/users/ OMITTING ``password`` -> Django stores an unusable
  password; the operator resets it out-of-band (force-reset).
* The current operator is identified by AUTH SUBJECT via
  ``get_current_user`` (/api/accounts/users/me/) — never by username/id, which
  a cross-instance restore remaps and which a malicious archive can collide.
* A startup capability check reads the User schema and fails the users
  category CLOSED if a ``password_hash`` (or similar) WRITE field ever appears,
  so a future Dispatcharr that accepts pre-hashed passwords cannot silently
  cause ECM to start writing hashes.

Mocking pattern follows test_dispatcharr_client_stream_crud.py: construct a real
``DispatcharrClient`` and ``patch.object(client, "_request", AsyncMock(...))``.
"""
import json
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

import httpx

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient

_RECORDED_OPENAPI = json.loads(
    (
        Path(__file__).parent.parent / "fixtures" / "dispatcharr_openapi_recorded.json"
    ).read_text()
)


def _response(status_code: int, json_body=None, text: str = ""):
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_body if json_body is not None else {}
    resp.text = text

    def _raise_for_status():
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {status_code}", request=None, response=resp
            )

    resp.raise_for_status = _raise_for_status
    return resp


def _make_client():
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="api_key",
        dispatcharr_api_key="key-123",
    )
    return DispatcharrClient(settings)


# ---------------------------------------------------------------------------
# create_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user_posts_to_accounts_users_endpoint_and_returns_created():
    """create_user POSTs to /api/accounts/users/ and returns the created body."""
    client = _make_client()
    try:
        created = {"id": 42, "username": "alice", "is_superuser": False}
        request_mock = AsyncMock(return_value=_response(201, created))
        with patch.object(client, "_request", request_mock):
            result = await client.create_user({"username": "alice"})

        assert result == created
        method, path = request_mock.await_args.args
        assert method == "POST"
        assert path == "/api/accounts/users/"
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_create_user_payload_omits_password_entirely():
    """The payload sent upstream MUST NOT carry a password (or any hash) field —
    create-with-no-usable-password is the mandated policy. Even if a caller
    passes one, create_user strips it so a password never crosses the boundary."""
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(201, {"id": 1, "username": "bob"}))
        with patch.object(client, "_request", request_mock):
            await client.create_user(
                {
                    "username": "bob",
                    "password": "should-be-dropped",
                    "password_hash": "pbkdf2$...",
                }
            )

        sent = request_mock.await_args.kwargs["json"]
        assert "password" not in sent
        assert "password_hash" not in sent
        assert sent["username"] == "bob"
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_create_user_accepts_200_status():
    """200 (some DRF endpoints) is treated as success like 201."""
    client = _make_client()
    try:
        created = {"id": 1, "username": "c"}
        request_mock = AsyncMock(return_value=_response(200, created))
        with patch.object(client, "_request", request_mock):
            result = await client.create_user({"username": "c"})
        assert result == created
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_create_user_raises_with_status_and_body_on_4xx():
    """A 4xx raises an Exception matching the '<thing> failed: <status> - <body>'
    shape that upstream_http_exception parses."""
    client = _make_client()
    try:
        body = '{"username": ["A user with that username already exists."]}'
        request_mock = AsyncMock(return_value=_response(400, text=body))
        with patch.object(client, "_request", request_mock):
            with pytest.raises(Exception) as exc_info:
                await client.create_user({"username": "dup"})
        msg = str(exc_info.value)
        assert "User creation failed" in msg
        assert "400" in msg
        assert body in msg
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_create_user_rejects_non_dict_payload():
    """create_user requires a dict payload; non-dict raises before any HTTP."""
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(201, {"id": 1}))
        with patch.object(client, "_request", request_mock):
            with pytest.raises(ValueError):
                await client.create_user(None)  # type: ignore[arg-type]
            with pytest.raises(ValueError):
                await client.create_user([{"username": "x"}])  # type: ignore[arg-type]
        request_mock.assert_not_awaited()
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_create_user_requires_username():
    """A payload without a username raises before any HTTP (Django requires it)."""
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(201, {"id": 1}))
        with patch.object(client, "_request", request_mock):
            with pytest.raises(ValueError):
                await client.create_user({"email": "x@example.com"})
        request_mock.assert_not_awaited()
    finally:
        await client._client.aclose()


# ---------------------------------------------------------------------------
# delete_user (rollback compensation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_user_issues_delete_to_id_endpoint():
    """delete_user DELETEs /api/accounts/users/{id}/ and returns None."""
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(204))
        with patch.object(client, "_request", request_mock):
            result = await client.delete_user(42)
        assert result is None
        method, path = request_mock.await_args.args
        assert method == "DELETE"
        assert path == "/api/accounts/users/42/"
    finally:
        await client._client.aclose()


# ---------------------------------------------------------------------------
# get_current_user (auth subject — the load-bearing security control)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_user_reads_users_me_endpoint():
    """get_current_user GETs /api/accounts/users/me/ — the auth subject of the
    credentials in use, NOT a username from the archive."""
    client = _make_client()
    try:
        me = {"id": 7, "username": "operator", "is_superuser": True}
        request_mock = AsyncMock(return_value=_response(200, me))
        with patch.object(client, "_request", request_mock):
            result = await client.get_current_user()
        assert result == me
        method, path = request_mock.await_args.args
        assert method == "GET"
        assert path == "/api/accounts/users/me/"
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_get_current_user_raises_on_error():
    """A non-2xx from /users/me/ raises — the importer must NOT proceed without
    a confirmed operator identity (fail closed rather than guess)."""
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(401, text="unauth"))
        with patch.object(client, "_request", request_mock):
            with pytest.raises(httpx.HTTPStatusError):
                await client.get_current_user()
    finally:
        await client._client.aclose()


# ---------------------------------------------------------------------------
# get_user_schema_write_fields (capability check source)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_schema_write_fields_extracts_writable_request_props():
    """get_user_schema_write_fields reads /api/schema/ and returns the set of
    WRITABLE request-body property names for the User-create endpoint, so the
    importer can detect a password_hash write field appearing in a future
    Dispatcharr."""
    client = _make_client()
    try:
        # Minimal OpenAPI doc: the POST /api/accounts/users/ requestBody
        # references a UserRequest schema with writable props.
        schema_doc = {
            "paths": {
                "/api/accounts/users/": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/UserRequest"}
                                }
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "UserRequest": {
                        "type": "object",
                        "properties": {
                            "username": {"type": "string"},
                            "password": {"type": "string", "writeOnly": True},
                            "is_superuser": {"type": "boolean"},
                            "id": {"type": "integer", "readOnly": True},
                        },
                    }
                }
            },
        }
        request_mock = AsyncMock(return_value=_response(200, schema_doc))
        with patch.object(client, "_request", request_mock):
            fields = await client.get_user_schema_write_fields()

        method, path = request_mock.await_args.args
        assert method == "GET"
        assert path == "/api/schema/"
        # Writable request props are present; the read-only id is excluded.
        assert "username" in fields
        assert "password" in fields
        assert "is_superuser" in fields
        assert "id" not in fields
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_get_user_schema_write_fields_requests_json_rendering():
    """The schema fetch must ask for the JSON rendering explicitly.

    Regression pin for enhancedchannelmanager-q6xjl: drf-spectacular's default
    renderer is YAML (``application/vnd.oai.openapi``), so a bare
    ``GET /api/schema/`` body cannot be ``.json()``-parsed. That raised
    ``Expecting value: line 1 column 1 (char 0)`` and failed the whole
    dispatcharr_users category CLOSED on every restore.
    """
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(200, {"paths": {}}))
        with patch.object(client, "_request", request_mock):
            await client.get_user_schema_write_fields()
        method, path = request_mock.await_args.args
        assert (method, path) == ("GET", "/api/schema/")
        assert request_mock.await_args.kwargs["params"] == {"format": "json"}
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_get_user_schema_write_fields_parses_recorded_dispatcharr_schema():
    """The extraction logic runs against a RECORDED slice of the real Dispatcharr
    0.28.2 OpenAPI document, not a hand-invented shape.

    Proves the ``$ref`` hop into ``components.schemas.User`` resolves against the
    real document and yields a NON-EMPTY write-field set — the condition the
    Users capability check requires to not fail closed.
    """
    client = _make_client()
    try:
        request_mock = AsyncMock(
            return_value=_response(200, _RECORDED_OPENAPI["schema"])
        )
        with patch.object(client, "_request", request_mock):
            fields = await client.get_user_schema_write_fields()

        assert fields, "recorded real schema must yield a non-empty write-field set"
        # Real writable fields present.
        assert {"username", "password", "email", "is_superuser"} <= fields
        # Real readOnly fields excluded.
        assert "id" not in fields
        assert "api_key" not in fields
        # Dispatcharr 0.28.2 exposes NO pre-hashed-password write field, so the
        # capability check passes rather than failing the category closed.
        assert "password_hash" not in fields
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_get_user_schema_write_fields_surfaces_password_hash_when_present():
    """If a future schema exposes a writable password_hash field, it appears in
    the returned set so the capability check can fail the category closed."""
    client = _make_client()
    try:
        schema_doc = {
            "paths": {
                "/api/accounts/users/": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "username": {"type": "string"},
                                            "password_hash": {"type": "string"},
                                        },
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        request_mock = AsyncMock(return_value=_response(200, schema_doc))
        with patch.object(client, "_request", request_mock):
            fields = await client.get_user_schema_write_fields()
        assert "password_hash" in fields
    finally:
        await client._client.aclose()
