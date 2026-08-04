"""Unit tests for the non-blocking Dispatcharr version advisory (ADR-014 option c).

The recorded contract fixtures — the paths manifest swept by
``test_dispatcharr_client_contract_sweep.py`` and the deep response fixtures —
are all validated against a *recorded* Dispatcharr version. That leaves a
silent-upgrade gap: an operator upgrades Dispatcharr underneath ECM, CI stays
green, and nothing says a word until something breaks.

``dispatcharr_version_advisory`` closes the gap with a WARNING, never a block
(bead ``enhancedchannelmanager-ax0kf``, PO decision 2026-08-03: home-lab tier —
never lock the operator out of their own tool over a version tuple).
"""
import pytest
from unittest.mock import AsyncMock, patch

import httpx

from config import DispatcharrSettings
from dispatcharr_client import (
    TESTED_DISPATCHARR_SERIES,
    DispatcharrClient,
    clamp_dispatcharr_version,
    dispatcharr_version_advisory,
    dispatcharr_version_series,
)


def _make_client():
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="api_key",
        dispatcharr_api_key="key-123",
    )
    return DispatcharrClient(settings)


def _response(status_code: int, json_body=None):
    response = AsyncMock(spec=httpx.Response)
    response.status_code = status_code
    response.json = lambda: json_body if json_body is not None else {}

    def _raise_for_status():
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {status_code}", request=None, response=response
            )

    response.raise_for_status = _raise_for_status
    return response


# ---------------------------------------------------------------------------
# The tested-version constant
# ---------------------------------------------------------------------------


def test_tested_series_constant_is_usable():
    """The constant an operator/maintainer edits must be a non-empty MAJOR.MINOR list."""
    assert TESTED_DISPATCHARR_SERIES, "at least one tested Dispatcharr series must be listed"
    for series in TESTED_DISPATCHARR_SERIES:
        major, _, minor = series.partition(".")
        assert major.isdigit() and minor.isdigit(), (
            f"{series!r} is not a MAJOR.MINOR series — the advisory compares series, "
            "not full versions, so a patch release never nags the operator."
        )


# ---------------------------------------------------------------------------
# Series parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0.28.2", "0.28"),
        ("0.28", "0.28"),
        ("v0.29.0", "0.29"),
        ("  1.0.0  ", "1.0"),
        ("0.28.2-rc1", "0.28"),
        ("", None),
        ("unknown", None),
        (None, None),
        (0.28, None),
    ],
)
def test_version_series_parsing(raw, expected):
    assert dispatcharr_version_series(raw) == expected


# ---------------------------------------------------------------------------
# The advisory
# ---------------------------------------------------------------------------


def test_no_advisory_for_a_tested_series():
    """A version in the tested set produces no notice at all."""
    tested = TESTED_DISPATCHARR_SERIES[0]
    assert dispatcharr_version_advisory(f"{tested}.99") is None


def test_advisory_for_an_untested_newer_series():
    """An untested series names the running version and the tested set."""
    advisory = dispatcharr_version_advisory("9.9.9")
    assert advisory is not None
    assert "9.9.9" in advisory
    assert TESTED_DISPATCHARR_SERIES[0] in advisory


def test_advisory_for_an_untested_older_series():
    """Older-than-tested is equally untested — the advisory is not one-directional."""
    advisory = dispatcharr_version_advisory("0.1.0")
    assert advisory is not None
    assert "0.1.0" in advisory


def test_advisory_never_reads_as_a_block():
    """Wording must not imply the operator is prevented from proceeding."""
    advisory = dispatcharr_version_advisory("9.9.9").lower()
    for forbidden in ("blocked", "unsupported", "refus", "cannot connect", "not allowed"):
        assert forbidden not in advisory, (
            f"advisory wording contains {forbidden!r} — it is a heads-up, not a gate"
        )


@pytest.mark.parametrize("unknown", [None, "", "   ", "unknown", 3, {"version": "0.1"}])
def test_no_advisory_when_the_version_cannot_be_determined(unknown):
    """An undeterminable version produces silence, not a nag the operator can't act on.

    Dispatcharr versions old enough to lack ``GET /api/core/version/`` — and any
    timeout or unparseable body — land here. The connection test's job is to
    test the connection.
    """
    assert dispatcharr_version_advisory(unknown) is None


# ---------------------------------------------------------------------------
# Clamping upstream-controlled text (PR #773 review, N2)
# ---------------------------------------------------------------------------


def test_clamp_truncates_a_long_version_string():
    clamped = clamp_dispatcharr_version("9.9.9" + "A" * 5000)
    assert len(clamped) <= 40, "an upstream blob must not become a 5 kB notification"
    assert clamped.startswith("9.9.9")
    assert clamped.endswith("...")


@pytest.mark.parametrize("hostile", ["0.29.0\nINJECTED", "0.29.0\r\nINJECTED", "0.29.0\x00x"])
def test_clamp_removes_control_characters(hostile):
    """A newline in the version must not be able to forge a second log record."""
    clamped = clamp_dispatcharr_version(hostile)
    assert "\n" not in clamped and "\r" not in clamped and "\x00" not in clamped


@pytest.mark.parametrize("empty", [None, "", "   ", "\n\n", 7, {"version": "0.1"}])
def test_clamp_returns_none_for_unusable_values(empty):
    assert clamp_dispatcharr_version(empty) is None


def test_advisory_embeds_the_clamped_version_not_the_raw_one():
    advisory = dispatcharr_version_advisory("9.9.9" + "A" * 5000 + "\nINJECTED")
    assert advisory is not None
    assert "INJECTED" not in advisory
    assert "\n" not in advisory
    assert len(advisory) < 400


def test_series_parsing_survives_a_hostile_suffix():
    """Clamping must not break version detection for an otherwise-valid prefix."""
    assert dispatcharr_version_series("0.29.0\nINJECTED") == "0.29"


# ---------------------------------------------------------------------------
# The client method
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_version_calls_the_core_version_endpoint():
    """get_version() GETs /api/core/version/ and returns the decoded body."""
    client = _make_client()
    try:
        body = {"version": "0.28.2", "timestamp": None}
        request_mock = AsyncMock(return_value=_response(200, body))
        with patch.object(client, "_request", request_mock):
            result = await client.get_version()

        assert result == body
        assert request_mock.await_args.args == ("GET", "/api/core/version/")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_version_forwards_a_caller_supplied_timeout():
    """The connection test passes a short timeout — an advisory is never worth a wait."""
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(200, {"version": "0.28.2"}))
        with patch.object(client, "_request", request_mock):
            await client.get_version(timeout=5.0)

        assert request_mock.await_args.kwargs["timeout"] == 5.0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_version_raises_on_upstream_error():
    """A non-2xx is raised, not swallowed — callers decide whether to degrade."""
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(404))
        with patch.object(client, "_request", request_mock):
            with pytest.raises(httpx.HTTPStatusError):
                await client.get_version()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_version_forwards_the_retry_opt_out_to_request():
    client = _make_client()
    try:
        request_mock = AsyncMock(return_value=_response(200, {"version": "0.28.2"}))
        with patch.object(client, "_request", request_mock):
            await client.get_version(retry_on_401=False)

        assert request_mock.await_args.kwargs["retry_on_401"] is False
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# The 401-retry opt-out (PR #773 review, BLOCK)
# ---------------------------------------------------------------------------
#
# ``_request``'s 401 branch refreshes the token and, with no refresh token to
# use, falls through to a full ``_login()`` — a fresh
# ``POST /api/accounts/token/`` with the operator's username and password.
# Dispatcharr rate-limits login at 3/min per IP, so a *best-effort advisory*
# that can do that is able to burn the operator's login budget and fail their
# NEXT real connection test. ``retry_on_401=False`` makes that structurally
# impossible for the probe; the pair of tests below pins both directions, so
# the opt-out is proven load-bearing rather than decorative.


def _password_client():
    """A password-mode client — the only mode where a 401 can reach ``_login``."""
    settings = DispatcharrSettings(
        url="http://dispatcharr:8000",
        auth_method="password",
        username="admin",
        password="secret",
    )
    return DispatcharrClient(settings)


def _mock_transport(*, request_response, post_response=None):
    """Stand in for ``client._client`` (the raw ``httpx.AsyncClient``)."""
    transport = AsyncMock()
    transport.request = AsyncMock(return_value=request_response)
    transport.post = AsyncMock(
        return_value=post_response
        if post_response is not None
        else _response(200, {"access": "jwt-fresh", "refresh": "jwt-ref"})
    )
    return transport


@pytest.mark.asyncio
async def test_a_401_reaches_login_when_the_retry_is_enabled():
    """The default path DOES re-authenticate — this is what the probe must avoid."""
    client = _password_client()
    try:
        client.access_token = "jwt-abc"
        transport = _mock_transport(request_response=_response(401))
        client._client = transport

        with pytest.raises(httpx.HTTPStatusError):
            await client.get_version()

        assert transport.request.await_count == 2, "the 401 retry re-issues the request"
        assert transport.post.await_count == 1, (
            "with no refresh token, _refresh_access_token falls through to a full "
            "_login() — the second POST /api/accounts/token/ this opt-out exists to stop"
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_401_cannot_reach_login_when_the_retry_is_disabled():
    """``retry_on_401=False`` makes a second login unreachable, not merely unlikely."""
    client = _password_client()
    try:
        client.access_token = "jwt-abc"
        transport = _mock_transport(request_response=_response(401))
        client._client = transport

        with pytest.raises(httpx.HTTPStatusError):
            await client.get_version(retry_on_401=False)

        assert transport.request.await_count == 1, "no retry may be issued"
        assert transport.post.await_count == 0, (
            "the probe must not be able to issue POST /api/accounts/token/"
        )
    finally:
        await client.close()
