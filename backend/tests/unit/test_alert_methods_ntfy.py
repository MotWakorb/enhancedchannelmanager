"""Unit contract for the first-class ntfy alert method."""
import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from alert_methods import AlertMessage, create_method, get_method_types
import alert_methods_ntfy
from alert_methods_ntfy import NtfyMethod


def _method(**config):
    values = {"server_url": "https://ntfy.example.test", "topic": "ecm-alerts"}
    values.update(config)
    return NtfyMethod(1, "ntfy", values)


def test_ntfy_is_registered_with_config_metadata():
    # Existing registry tests deliberately clear the process-global registry.
    # Reloading proves the module import itself restores registration.
    module = importlib.reload(alert_methods_ntfy)
    metadata = next(item for item in get_method_types() if item["type"] == "ntfy")

    assert metadata == {
        "type": "ntfy",
        "display_name": "ntfy",
        "required_fields": ["server_url", "topic"],
        "optional_fields": {"access_token": ""},
    }
    assert isinstance(create_method("ntfy", 7, "Home alerts", {
        "server_url": "http://192.168.1.20:8080/ntfy/",
        "topic": "ECM_home-1",
    }), module.NtfyMethod)


@pytest.mark.parametrize("server_url", [
    "https://ntfy.sh",
    "http://ntfy.internal:8080",
    "http://192.168.1.20:8080",
    "https://push.example.test/services/ntfy/",
])
def test_valid_public_and_self_hosted_server_urls(server_url):
    assert NtfyMethod.validate_config({"server_url": server_url, "topic": "ECM_home-1"}) == (True, "")


@pytest.mark.parametrize("server_url", [
    None,
    42,
    "",
    "ntfy.example.test",
    "/relative",
    "ftp://ntfy.example.test",
    "https://",
    "https://user:pass@ntfy.example.test",
    "https://ntfy.example.test?token=value",
    "https://ntfy.example.test/#fragment",
    "https://ntfy.example.test:bad-port",
    "https://ntfy.example.test/path with spaces",
])
def test_rejects_invalid_server_urls(server_url):
    valid, error = NtfyMethod.validate_config({"server_url": server_url, "topic": "ecm"})
    assert valid is False
    assert "server URL" in error or "server_url" in error


@pytest.mark.parametrize("topic", [
    None,
    42,
    "",
    "contains space",
    "slash/topic",
    "topic?query",
    "a" * 65,
    "ümlaut",
])
def test_rejects_invalid_topics(topic):
    valid, error = NtfyMethod.validate_config({
        "server_url": "https://ntfy.example.test",
        "topic": topic,
    })
    assert valid is False
    assert "topic" in error


@pytest.mark.parametrize("token", [None, 42, "", "line\nbreak", "carriage\rreturn"])
def test_rejects_invalid_access_tokens_when_supplied(token):
    valid, error = NtfyMethod.validate_config({
        "server_url": "https://ntfy.example.test",
        "topic": "ecm",
        "access_token": token,
    })
    assert valid is False
    assert "access_token" in error


def test_rejects_access_token_over_plaintext_http():
    valid, error = NtfyMethod.validate_config({
        "server_url": "http://ntfy.internal:8080",
        "topic": "ecm",
        "access_token": "<opaque-token>",
    })
    assert valid is False
    assert "HTTPS" in error


def test_rejects_api_mask_literal_as_access_token():
    valid, error = NtfyMethod.validate_config({
        "server_url": "https://ntfy.example.test",
        "topic": "ecm",
        "access_token": "********",
    })
    assert valid is False
    assert "masked" in error


def _session_for_status(status=200, post_error=None):
    response = AsyncMock()
    response.status = status
    response_context = AsyncMock()
    response_context.__aenter__.return_value = response
    session = MagicMock()
    if post_error:
        session.post.side_effect = post_error
    else:
        session.post.return_value = response_context
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    return session, session_context


@pytest.mark.asyncio
async def test_send_posts_exact_unauthenticated_request_to_normalized_url():
    session, session_context = _session_for_status()
    message = AlertMessage("Task complete", "Everything finished", "success")

    with patch("alert_methods_ntfy.aiohttp.ClientSession", return_value=session_context):
        assert await _method(server_url="https://push.example.test/base/").send(message) is True

    session.post.assert_called_once_with(
        "https://push.example.test/base/ecm-alerts",
        data=b"Everything finished",
        headers={"Title": "Task complete", "Priority": "3"},
        timeout=aiohttp.ClientTimeout(total=10),
        allow_redirects=False,
    )


@pytest.mark.asyncio
async def test_send_adds_bearer_header_without_putting_token_in_url_or_body():
    session, session_context = _session_for_status()
    token = "<opaque-ntfy-token>"

    with patch("alert_methods_ntfy.aiohttp.ClientSession", return_value=session_context):
        assert await _method(access_token=token).send(AlertMessage("Alert", "Body", "warning")) is True

    call = session.post.call_args
    assert call.args[0] == "https://ntfy.example.test/ecm-alerts"
    assert call.kwargs["data"] == b"Body"
    assert call.kwargs["headers"] == {
        "Title": "Alert",
        "Priority": "4",
        "Authorization": f"Bearer {token}",
    }
    assert token not in call.args[0]
    assert token.encode() not in call.kwargs["data"]


@pytest.mark.parametrize("notification_type,priority", [
    ("info", "3"),
    ("success", "3"),
    ("warning", "4"),
    ("error", "5"),
])
@pytest.mark.asyncio
async def test_send_maps_severity_to_ntfy_priority(notification_type, priority):
    session, session_context = _session_for_status()
    with patch("alert_methods_ntfy.aiohttp.ClientSession", return_value=session_context):
        assert await _method().send(AlertMessage("Title", "Body", notification_type)) is True
    assert session.post.call_args.kwargs["headers"]["Priority"] == priority


@pytest.mark.parametrize("status", [201, 204, 301, 400, 401, 500])
@pytest.mark.asyncio
async def test_only_http_200_is_success_and_upstream_body_is_not_read(status):
    session, session_context = _session_for_status(status)
    with patch("alert_methods_ntfy.aiohttp.ClientSession", return_value=session_context):
        assert await _method().send(AlertMessage("Title", "Body")) is False
    response = session.post.return_value.__aenter__.return_value
    response.text.assert_not_awaited()
    response.json.assert_not_awaited()


@pytest.mark.parametrize("error", [asyncio.TimeoutError(), aiohttp.ClientError("secret upstream detail")])
@pytest.mark.asyncio
async def test_timeout_and_client_failures_return_false(error):
    session, session_context = _session_for_status(post_error=error)
    with patch("alert_methods_ntfy.aiohttp.ClientSession", return_value=session_context):
        assert await _method().send(AlertMessage("Title", "Body")) is False


@pytest.mark.asyncio
async def test_send_rejects_empty_or_non_utf8_message_without_network_call():
    session, session_context = _session_for_status()
    with patch("alert_methods_ntfy.aiohttp.ClientSession", return_value=session_context):
        assert await _method().send(AlertMessage("Title", "")) is False
        assert await _method().send(AlertMessage("Title", "\ud800")) is False
    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_connection_publishes_a_real_test_notification():
    method = _method()
    method.send = AsyncMock(return_value=True)

    assert await method.test_connection() == (True, "Test notification sent successfully")
    sent = method.send.await_args.args[0]
    assert sent.title == "Connection Test"
    assert sent.message
    assert sent.notification_type == "info"


@pytest.mark.asyncio
async def test_connection_returns_safe_actionable_failure():
    method = _method()
    method.send = AsyncMock(return_value=False)
    assert await method.test_connection() == (
        False,
        "Failed to send test notification; check the server URL, topic, token, and server availability",
    )
