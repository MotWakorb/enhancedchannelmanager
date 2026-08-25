"""Shared fail-closed observation for reads from a restore destination."""
from __future__ import annotations

import inspect
from typing import Optional

from httpx import HTTPStatusError, RequestError, TimeoutException

from dbas.restore_contracts import RestoreReport
from security.ssrf import SSRFError


# Cheap authenticated read used to distinguish an empty destination from one
# that cannot be read. Importers read this category again as part of their plan.
_DESTINATION_PROBE = "get_channel_groups"
_DESTINATION_READ_PREFIX = "get_"


def _describe_destination_error(exc: BaseException) -> str:
    """Return an actionable diagnostic without exposing exception text or URLs."""
    if isinstance(exc, SSRFError):
        return "the destination is blocked by this instance's outbound SSRF policy"
    if isinstance(exc, HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return (
                "the destination rate-limited this request (HTTP 429) - this is "
                "not a credential problem; wait for its limit window to clear "
                "and retry"
            )
        if status in (401, 403):
            return (
                "authentication to the destination was rejected (HTTP %d) - "
                "check the destination credentials configured on this instance"
                % status
            )
        if status >= 500:
            return "the destination returned a server error (HTTP %d)" % status
        return "the destination returned HTTP %d" % status
    if isinstance(exc, TimeoutException):
        return "the destination did not respond in time (%s)" % type(exc).__name__
    if isinstance(exc, RequestError):
        return "the destination could not be reached (%s)" % type(exc).__name__
    return "the destination could not be read (%s)" % type(exc).__name__


async def destination_read_reason(client) -> Optional[str]:
    """Probe the destination once; return a sanitized refusal reason on failure."""
    probe = getattr(client, _DESTINATION_PROBE, None)
    if probe is None:
        return "the destination client has no authenticated readability check"
    try:
        await probe()
    except Exception as exc:  # noqa: BLE001 - every failure class is a refusal
        return _describe_destination_error(exc)
    return None


def mark_destination_unread(report: RestoreReport, reason: str) -> None:
    """Record the first failed read and retain each failure as an operator note."""
    if report.destination_unreadable is None:
        report.destination_unreadable = reason
    report.notes.append("destination not read: %s" % reason)


class ReadObservingClient:
    """Transparent client proxy that records every failed destination read."""

    def __init__(self, inner, report: RestoreReport) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_report", report)

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if not name.startswith(_DESTINATION_READ_PREFIX) or not callable(attr):
            return attr

        async def _observed_read(*args, **kwargs):
            try:
                result = attr(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result
            except Exception as exc:  # noqa: BLE001 - observe, never swallow
                mark_destination_unread(
                    self._report,
                    "%s could not be read - %s"
                    % (name, _describe_destination_error(exc)),
                )
                raise

        return _observed_read
