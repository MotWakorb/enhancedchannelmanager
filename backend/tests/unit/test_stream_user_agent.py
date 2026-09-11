"""Dispatcharr's recorded API envelopes plus source-derived UA resolution cases.

Nested settings values below are synthetic test inputs, NOT recorded live data.
The old recorded settings fixture explicitly redacts value; it cannot prove the
default_user_agent contract. Sources are pinned in docs/dispatcharr_api.md.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from stream_user_agent import (
    USER_AGENT_PRESETS, StreamUserAgentError, StreamUserAgentResolver, validate_user_agent,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"


def recorded(name):
    return json.loads((FIXTURES / name).read_text())


def client():
    result = AsyncMock()
    result.get_core_settings.return_value = []
    result.get_user_agents.return_value = []
    result.get_version.return_value = {"version": "0.30.0", "timestamp": None}
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", list(USER_AGENT_PRESETS))
async def test_override_needs_no_dispatcharr_configuration_reads(selection):
    upstream = client()
    result = await StreamUserAgentResolver(upstream, selection).resolve(stream_id=42)
    assert result == USER_AGENT_PRESETS[selection]
    assert not upstream.mock_calls


@pytest.mark.asyncio
async def test_recorded_stream_integer_account_reference_and_null_account_ua():
    upstream = client()
    stream = recorded("dispatcharr_stream_provider_channel_number.json")
    account = recorded("bd_g8tyd/dispatcharr_v0282_m3u_account_server_group.json")
    # Independent recorded captures; exercise their shapes, not cross-instance IDs.
    upstream.get_m3u_account.return_value = account
    result = await StreamUserAgentResolver(upstream, "dispatcharr").resolve(stream)
    assert result == "Dispatcharr/0.30.0"
    upstream.get_m3u_account.assert_awaited_once_with(stream["m3u_account"])
    upstream.get_user_agents.assert_not_awaited()
    upstream.get_stream_profiles.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_ua_short_circuits_global_and_ignores_is_active_and_stream_profile():
    upstream = client()
    upstream.get_m3u_account.return_value = {"user_agent": 9}
    upstream.get_user_agents.return_value = [{"id": 9, "user_agent": "Provider/1.0", "is_active": False}]
    resolver = StreamUserAgentResolver(upstream, "dispatcharr")
    assert await resolver.resolve({"m3u_account": 1, "stream_profile_id": 42}) == "Provider/1.0"
    assert await resolver.resolve({"m3u_account": 1}) == "Provider/1.0"
    upstream.get_m3u_account.assert_awaited_once_with(1)
    upstream.get_core_settings.assert_not_awaited()
    upstream.get_version.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("account_value", [None, "", "Account/1.0"])
async def test_global_default_in_recorded_list_envelope_with_source_derived_values(account_value):
    upstream = client()
    rows = recorded("dispatcharr_core_settings_recorded.json")["core_settings_list"]
    # Only this value is replaced with a SOURCE-DERIVED synthetic case.
    next(row for row in rows if row["key"] == "stream_settings")["value"] = {"default_user_agent": 8}
    upstream.get_core_settings.return_value = rows
    upstream.get_m3u_account.return_value = {"user_agent": 9}
    upstream.get_user_agents.return_value = [
        {"id": 8, "user_agent": "Global/2.0"}, {"id": 9, "user_agent": account_value},
    ]
    assert await StreamUserAgentResolver(upstream, "dispatcharr").resolve({"m3u_account": 1}) == (account_value or "Global/2.0")
    upstream.get_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_operation_observes_configuration_changes():
    upstream = client()
    upstream.get_core_settings.return_value = [{"key": "stream_settings", "value": {"default_user_agent": 1}}]
    upstream.get_user_agents.side_effect = [[{"id": 1, "user_agent": "Old/1"}], [{"id": 1, "user_agent": "New/2"}]]
    assert await StreamUserAgentResolver(upstream, "dispatcharr").resolve() == "Old/1"
    assert await StreamUserAgentResolver(upstream, "dispatcharr").resolve() == "New/2"


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, {}, {"version": "v-secret\r\nInjected"}, {"version": "x" * 5000}])
async def test_unknown_version_fallback_is_explicit_and_log_safe(version, caplog):
    upstream = client()
    upstream.get_version.return_value = version
    assert await StreamUserAgentResolver(upstream, "dispatcharr").resolve() == "Dispatcharr/unknown"
    assert "version unavailable" in caplog.text
    assert "Injected" not in caplog.text


@pytest.mark.asyncio
async def test_version_endpoint_failure_uses_documented_fallback(caplog):
    upstream = client()
    upstream.get_version.side_effect = RuntimeError("private credentials")
    assert await StreamUserAgentResolver(upstream, "dispatcharr").resolve() == "Dispatcharr/unknown"
    assert "private credentials" not in caplog.text


@pytest.mark.asyncio
async def test_missing_stream_metadata_does_not_silently_choose_a_global_identity():
    upstream = client()
    upstream.get_stream.return_value = None
    with pytest.raises(StreamUserAgentError, match="stream is unavailable"):
        await StreamUserAgentResolver(upstream, "dispatcharr").resolve(stream_id=42)
    upstream.get_core_settings.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [None, "", 99, "99"])
async def test_absent_or_deleted_global_agent_uses_version_fallback(reference):
    upstream = client()
    upstream.get_core_settings.return_value = [{"key": "stream_settings", "value": {"default_user_agent": reference}}]
    assert await StreamUserAgentResolver(upstream, "dispatcharr").resolve() == "Dispatcharr/0.30.0"


@pytest.mark.asyncio
async def test_malformed_account_header_is_rejected_instead_of_global_fallback():
    upstream = client()
    upstream.get_m3u_account.return_value = {"user_agent": 4}
    upstream.get_user_agents.return_value = [{"id": 4, "user_agent": "UA\r\nAuthorization: stolen"}]
    with pytest.raises(StreamUserAgentError, match="configuration is malformed"):
        await StreamUserAgentResolver(upstream, "dispatcharr").resolve({"m3u_account": 1})
    upstream.get_core_settings.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,stream", [
    ("get_core_settings", {}), ("get_m3u_account", {"m3u_account": 1}),
    ("get_user_agents", {"m3u_account": 1}), ("get_stream", None),
])
async def test_configuration_fetch_failure_is_not_optional_absence(method, stream, caplog):
    upstream = client()
    upstream.get_m3u_account.return_value = {"user_agent": 1}
    getattr(upstream, method).side_effect = RuntimeError("https://secret:password@host\r\nInjected")
    with pytest.raises(StreamUserAgentError, match="Could not load"):
        await StreamUserAgentResolver(upstream, "dispatcharr").resolve(stream, stream_id=42)
    upstream.get_version.assert_not_awaited()
    assert "password" not in caplog.text
    assert "Injected" not in caplog.text


@pytest.mark.parametrize("value", ["UA\r\nAuthorization: stolen", "UA\x00", "UA\t", "UA\x7f", "é", "", " " * 2, "a" * 513, 123])
def test_unsafe_header_values_are_rejected_without_echo(value):
    with pytest.raises(StreamUserAgentError) as error:
        validate_user_agent(value)
    assert str(error.value) == "Dispatcharr User-Agent configuration is malformed"


@pytest.mark.asyncio
async def test_redacted_recording_is_not_mistaken_for_absent_configuration():
    upstream = client()
    upstream.get_core_settings.return_value = recorded("dispatcharr_core_settings_recorded.json")["core_settings_list"]
    with pytest.raises(StreamUserAgentError, match="stream settings are malformed"):
        await StreamUserAgentResolver(upstream, "dispatcharr").resolve()
