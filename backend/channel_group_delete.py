"""Association-based hide policy shared by immediate and Edit Mode deletes."""

import re
from dataclasses import dataclass

import httpx
from sqlalchemy.exc import IntegrityError

from channel_group_reparent import (
    UNGROUPED_TARGET_GROUP_NAME,
    UngroupedTargetUnavailable,
)
from database import get_session
from log_utils import get_registered_sensitive_value_forms
from models import HiddenChannelGroup
from tls.redaction import redact_secret_values

_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+")
_VALIDATION_FIELDS = ("error", "detail", "non_field_errors", "channel_group_id")
_GROUP_VALIDATION_CAUSES = (
    "Cannot delete group with associated channels",
    "Cannot delete group with M3U account associations",
)


@dataclass(frozen=True)
class HiddenGroupResult:
    """The persisted hide outcome; repeated hides create no extra journal row."""

    name: str
    created: bool


async def hide_associated_group(client, group_id: int) -> HiddenGroupResult | None:
    """Hide an M3U-associated group before any upstream mutation.

    Args:
        client: Dispatcharr client used to resolve current associations and name.
        group_id: Dispatcharr channel group ID.

    Returns:
        The hidden group name and whether a row was inserted, or None when no
        association exists. Sync/enabled flags do not change this policy.

    Raises:
        Exception: An upstream read or database write failed. Callers must not
            proceed with deletion when protection could not be established.
    """
    settings = await client.get_all_m3u_group_settings()
    if group_id not in settings:
        return None
    groups = await client.get_channel_groups()
    name = next(
        (g.get("name") for g in groups if g.get("id") == group_id), f"Group {group_id}"
    )
    with get_session() as db:
        existing = db.query(HiddenChannelGroup).filter_by(group_id=group_id).first()
        if existing is not None:
            return HiddenGroupResult(name=existing.group_name, created=False)
        db.add(HiddenChannelGroup(group_id=group_id, group_name=name))
        try:
            db.commit()
        except IntegrityError:
            # Another request/process can insert after our read. Recover only
            # when the requested hidden state actually exists after rollback.
            db.rollback()
            existing = db.query(HiddenChannelGroup).filter_by(group_id=group_id).first()
            if existing is None:
                raise
            return HiddenGroupResult(name=existing.group_name, created=False)
    return HiddenGroupResult(name=name, created=True)


def _redact_detail(text: str) -> str:
    # Discard entire URLs, including unknown credential-bearing path/query forms.
    # Identity/pattern redaction is the existing TLS/persistent-log policy.
    text = redact_secret_values(text, get_registered_sensitive_value_forms())
    return " ".join(_URL_RE.sub("[URL omitted]", text).split())[:500]


def group_delete_failure_detail(
    error: Exception, group_id: int, channels_moved: int = 0
) -> str:
    """Build bounded, scrubbed group guidance without exposing raw exceptions.

    Args:
        error: Failure from the protection, reparent or delete step.
        group_id: Group identity, included even when its name was unavailable.
        channels_moved: Writes already confirmed by the reparent callback.

    Returns:
        Operator-safe validation context and any landed-move advisory. Unknown
        body shapes/server faults use fixed guidance instead of raw bodies.
    """
    detail = "Could not complete group deletion. Check Dispatcharr and the group associations."
    if isinstance(error, UngroupedTargetUnavailable):
        detail = _redact_detail(str(error))
    elif isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        detail = f"Dispatcharr returned HTTP {status}. Check the group's channels and M3U account associations."
        if 400 <= status < 500:
            try:
                body = error.response.json()
            except ValueError:
                body = None
            # Never forward arbitrary metadata, nested objects, HTML, or the
            # exception string (HTTPX includes the request URL there).
            messages = []
            if isinstance(body, dict):
                for field in _VALIDATION_FIELDS:
                    value = body.get(field)
                    values = value if isinstance(value, list) else [value]
                    messages.extend(v for v in values if isinstance(v, str))
            # Normalize known producer reasons to fixed prose, rather than
            # trusting a validation message not to contain unregistered secrets.
            causes = [
                cause
                for cause in _GROUP_VALIDATION_CAUSES
                if any(message.startswith(cause) for message in messages)
            ]
            if causes:
                detail = f"Dispatcharr returned HTTP {status}: {'; '.join(causes)}."
                detail += " Refresh the group and check its channels and M3U account associations before making further changes."
    detail = f"Group {group_id}: {detail}"
    if channels_moved:
        detail += (
            f" The group still exists; {channels_moved} channel(s) already moved to "
            f"'{UNGROUPED_TARGET_GROUP_NAME}' and stay moved. Check the Journal "
            "and reconcile before retrying; this operation was not retried."
        )
    return detail
