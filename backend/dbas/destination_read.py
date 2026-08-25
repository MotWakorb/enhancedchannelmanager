"""Shared fail-closed observation for reads from a restore destination."""
from __future__ import annotations

import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

from httpx import HTTPStatusError, RequestError, TimeoutException

from dbas.restore_contracts import RestoreReport
from security.ssrf import SSRFError


# Cheap authenticated read used to distinguish an empty destination from one
# that cannot be read. Importers read this category again as part of their plan.
_DESTINATION_PROBE = "get_channel_groups"
_DESTINATION_READ_PREFIX = "get_"
_DESTINATION_MUTATION_PREFIXES = (
    "bulk_",
    "create_",
    "delete_",
    "patch_",
    "refresh_",
    "trigger_",
    "update_",
    "upload_",
)
_COMPENSATING_DELETE_PREFIXES = ("bulk_delete_", "delete_")


class DestinationUnreadableError(RuntimeError):
    """Raised when an importer tries to mutate after a failed destination read."""


class DestinationReadError(DestinationUnreadableError):
    """Sanitized destination read failure safe for reports and logs."""

    def __init__(
        self,
        operation: str,
        *,
        category: str,
        diagnostic: str,
        status_code: int | None = None,
    ) -> None:
        self.operation = operation
        self.category = category
        self.status_code = status_code
        self.diagnostic = diagnostic
        status = "" if status_code is None else ", status=%d" % status_code
        super().__init__(
            "destination read %s failed (category=%s%s): %s"
            % (operation, category, status, diagnostic)
        )


def _destination_error_category(exc: BaseException) -> tuple[str, int | None]:
    """Classify a destination failure without retaining its unsafe text."""
    if isinstance(exc, SSRFError):
        return "ssrf_policy", None
    if isinstance(exc, HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "rate_limit", status
        if status in (401, 403):
            return "authentication", status
        if status >= 500:
            return "server_error", status
        return "http_error", status
    if isinstance(exc, TimeoutException):
        return "timeout", None
    if isinstance(exc, RequestError):
        return "network", None
    return "unexpected", None


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

    def __init__(
        self,
        inner,
        report: RestoreReport,
        *,
        reject_mutations: bool = False,
    ) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_report", report)
        object.__setattr__(self, "_reject_mutations", reject_mutations)
        object.__setattr__(
            self,
            "_compensation_active",
            ContextVar("destination_compensation_active", default=False),
        )

    @contextmanager
    def compensation(self):
        """Allow rollback DELETEs in this task while forward writes stay blocked."""
        token = self._compensation_active.set(True)
        try:
            yield self
        finally:
            self._compensation_active.reset(token)

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        if self._reject_mutations and name.startswith(_DESTINATION_MUTATION_PREFIXES):

            async def _guarded_mutation(*args, **kwargs):
                reason = self._report.destination_unreadable
                compensating_delete = (
                    name.startswith(_COMPENSATING_DELETE_PREFIXES)
                    and self._compensation_active.get()
                )
                if reason is not None and not compensating_delete:
                    raise DestinationUnreadableError(
                        "refusing %s after destination read failure: %s"
                        % (name, reason)
                    )
                result = attr(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result

            return _guarded_mutation

        if not name.startswith(_DESTINATION_READ_PREFIX):
            return attr

        async def _observed_read(*args, **kwargs):
            try:
                result = attr(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result
            except Exception as exc:  # noqa: BLE001 - observe, never swallow
                category, status_code = _destination_error_category(exc)
                diagnostic = _describe_destination_error(exc)
                mark_destination_unread(
                    self._report,
                    "%s could not be read - %s"
                    % (name, diagnostic),
                )
                raise DestinationReadError(
                    name,
                    category=category,
                    diagnostic=diagnostic,
                    status_code=status_code,
                ) from None

        return _observed_read
