"""Shared destructive-action guardrails for MCP tools (bd-onazy / bd-p8fx9).

Two cooperating mechanisms live here, both consumed by the destructive channel /
account / rule tools:

W2 — confirm / dry-run gating
-----------------------------
MCP tools are STATELESS thin HTTP wrappers: there is no server-side session
between two tool calls, so a "issue a token server-side then redeem it later"
flow is impossible — there is nowhere to keep the issued token. Instead the
confirm token is DERIVED DETERMINISTICALLY from the resolved target set
(:func:`derive_token`). The tool:

  1. First call (no token): resolves the full target set, returns a preview that
     INCLUDES the token, mutates nothing.
  2. Second call (token echoed back): RE-derives the token from the *current*
     resolved target set and proceeds only if it matches
     (:func:`token_matches`).

Because the token is a digest over the sorted target ids, a target set that has
changed since the preview (a channel added/removed, ids shifted) yields a
different token — the echoed token is stale and the tool refuses, closing the
TOCTOU gap. The caller cannot fabricate a valid token without first seeing the
server-resolved id set, because it's a hash over ids the caller may not know.

W4 — batch-size caps
--------------------
Two-level, settings-driven (mirrors ``max_auto_created_channels_per_run``): a
SOFT cap that forces the W2 token even on the first call, and a HARD cap that
REFUSES outright. Never silent truncation. Caps are read from the backend
``GET /api/settings`` response when present, otherwise fall back to the
conservative defaults below. :func:`evaluate_batch` returns a small decision
object the tool acts on.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cap defaults (settings-overridable, mirrors the auto-creation cap pattern).
# Keys are the settings names the backend exposes on GET /api/settings.
# ---------------------------------------------------------------------------
DEFAULT_BULK_DELETE_SOFT_CAP = 25
DEFAULT_BULK_DELETE_HARD_CAP = 500
DEFAULT_CLEAR_AUTO_CREATED_GROUPS_SOFT_CAP = 10
DEFAULT_BULK_MERGE_SOFT_CAP = 20
DEFAULT_BULK_MERGE_HARD_CAP = 200

# Settings keys (read off the GET /api/settings dict when present).
SETTING_BULK_DELETE_SOFT_CAP = "mcp_bulk_delete_soft_cap"
SETTING_BULK_DELETE_HARD_CAP = "mcp_bulk_delete_hard_cap"
SETTING_CLEAR_AUTO_CREATED_GROUPS_SOFT_CAP = "mcp_clear_auto_created_group_soft_cap"
SETTING_BULK_MERGE_SOFT_CAP = "mcp_bulk_merge_soft_cap"
SETTING_BULK_MERGE_HARD_CAP = "mcp_bulk_merge_hard_cap"


# ---------------------------------------------------------------------------
# W2 — content-derived confirm token
# ---------------------------------------------------------------------------
def derive_token(target_ids) -> str:
    """Return a short deterministic digest over a resolved target id set.

    The token is ``"{count}-{sha1(sorted_ids)[:8]}"``. It is stable for the same
    set regardless of input order (the ids are sorted first), and changes if any
    id is added or removed — that change-detection IS the staleness / TOCTOU
    guard. ``target_ids`` may be any iterable of ints; duplicates are collapsed.
    """
    ids = sorted({int(i) for i in target_ids})
    digest = hashlib.sha1(",".join(map(str, ids)).encode()).hexdigest()[:8]
    return f"{len(ids)}-{digest}"


def token_matches(supplied: str | None, target_ids) -> bool:
    """True iff ``supplied`` equals the token re-derived from ``target_ids``.

    A ``None`` / empty supplied token never matches (the caller hasn't confirmed
    yet). A non-matching token means the target set drifted since the preview —
    the caller must re-preview and confirm against the fresh token.
    """
    if not supplied:
        return False
    return str(supplied).strip() == derive_token(target_ids)


# ---------------------------------------------------------------------------
# W4 — batch-size caps
# ---------------------------------------------------------------------------
def _read_cap(settings: dict | None, key: str, default: int) -> int:
    """Read an int cap from a settings dict, falling back to ``default``.

    Defensive: a missing key, a ``None``, or a non-int value all fall back to the
    conservative module default rather than letting a malformed setting disable
    the guard.
    """
    if not isinstance(settings, dict):
        return default
    value = settings.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("[GUARDRAILS] Non-int cap setting %s=%r — using default %d", key, value, default)
        return default


@dataclass(frozen=True)
class BatchDecision:
    """Outcome of a batch-size cap evaluation.

    * ``action == "proceed"`` — below the soft cap; no token required.
    * ``action == "confirm"`` — between soft and hard cap; the W2 token is
      required even on the first call.
    * ``action == "refuse"`` — at/above the hard cap; refuse outright with
      ``message`` (which names the limit and tells the operator to split).
    """
    action: str  # "proceed" | "confirm" | "refuse"
    soft_cap: int
    hard_cap: int | None
    message: str = ""


def evaluate_batch(
    count: int,
    *,
    soft_cap: int,
    hard_cap: int | None,
    unit: str = "items",
) -> BatchDecision:
    """Classify a batch of ``count`` ``unit`` against soft/hard caps.

    A ``hard_cap`` of ``None`` means there is no numeric hard refusal (used for
    the always-token-gated ``clear_auto_created(all_groups=True)`` path, which is
    gated regardless of count). A non-positive ``soft_cap`` means "always require
    the token" (the all-groups path passes ``soft_cap=0``).
    """
    if hard_cap is not None and count >= hard_cap > 0:
        msg = (
            f"Refusing: {count} {unit} exceeds the hard cap of {hard_cap}. "
            f"Narrow your selection or split the batch into chunks under {hard_cap}. "
            f"(Raise the cap deliberately via settings if this large operation is intended.)"
        )
        return BatchDecision("refuse", soft_cap, hard_cap, msg)
    if soft_cap <= 0 or count >= soft_cap:
        return BatchDecision("confirm", soft_cap, hard_cap)
    return BatchDecision("proceed", soft_cap, hard_cap)


async def get_caps_settings(client) -> dict:
    """Fetch the backend settings dict for cap lookups; ``{}`` on any failure.

    Caps are advisory guardrails — if settings can't be read we fall back to the
    conservative module defaults rather than blocking the operator entirely, so a
    settings-fetch failure must NOT raise.
    """
    try:
        from _endpoint_contracts import ENDPOINTS

        result = await client.call_endpoint(ENDPOINTS["settings_get"])
        return result if isinstance(result, dict) else {}
    except Exception as e:  # noqa: BLE001 — advisory read, never fatal
        logger.warning("[GUARDRAILS] Could not read settings for caps: %s", e)
        return {}


def bulk_delete_caps(settings: dict | None) -> tuple[int, int]:
    """(soft, hard) caps for bulk_delete_channels."""
    return (
        _read_cap(settings, SETTING_BULK_DELETE_SOFT_CAP, DEFAULT_BULK_DELETE_SOFT_CAP),
        _read_cap(settings, SETTING_BULK_DELETE_HARD_CAP, DEFAULT_BULK_DELETE_HARD_CAP),
    )


def clear_auto_created_group_soft_cap(settings: dict | None) -> int:
    """Soft cap on number of groups for clear_auto_created(group_ids=[...])."""
    return _read_cap(
        settings,
        SETTING_CLEAR_AUTO_CREATED_GROUPS_SOFT_CAP,
        DEFAULT_CLEAR_AUTO_CREATED_GROUPS_SOFT_CAP,
    )


def bulk_merge_caps(settings: dict | None) -> tuple[int, int]:
    """(soft, hard) caps for bulk_merge_duplicate_channels (counted in merge ops)."""
    return (
        _read_cap(settings, SETTING_BULK_MERGE_SOFT_CAP, DEFAULT_BULK_MERGE_SOFT_CAP),
        _read_cap(settings, SETTING_BULK_MERGE_HARD_CAP, DEFAULT_BULK_MERGE_HARD_CAP),
    )
