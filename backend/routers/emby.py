"""Emby action endpoints (GH #475, bd-v9tp7).

Distinct from the Emby *settings* surface (``routers/settings.py`` holds the
config fields + ``/api/settings/emby/test-connection``). This router owns
operator-triggered Emby *actions* — currently just "Clear Emby Logos".

Clear Emby Logos
----------------
Emby caches channel logos and keeps serving a stale image even after the
upstream logo changes in Dispatcharr. Channel Identifiarr solves this by
deleting the cached image so Emby re-fetches it on next access. We replicate
that, reusing ECM's existing Emby connectivity (saved ``emby_base_url`` +
``emby_api_key``, ``X-Emby-Token`` auth) — no new credentials, no
username/password login (a static Emby API key authenticates the
``GET /LiveTv/Channels`` enumeration and the ``DELETE /Items/{id}/Images/{type}``
calls just as well).

A full lineup can be hundreds of channels × up to 3 image types = hundreds of
sequential DELETEs, which would blow past the 30s ``ECM_REQUEST_TIMEOUT_SECONDS``
middleware. So this follows the same 202+poll background-job contract as
bulk-commit (bd-ggxks): ``POST /clear-logos`` returns ``202 {job_id}`` and the
client polls ``GET /clear-logos/{job_id}`` until terminal.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth import RequireAdminIfEnabled, ResolveIsMcpServicePrincipalIfEnabled
from config import get_settings
from emby_client import EmbyClient, EmbyClientError, VALID_LOGO_IMAGE_TYPES
from services.notification_service import (
    create_notification_internal,
    update_notification_internal,
    delete_notifications_by_source_internal,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/emby", tags=["Emby"])

# Notification source for the clear-logos progress notification. The ``task_``
# prefix is what the frontend NotificationCenter keys on to treat a
# notification as a live progress item (same mechanism as stream probe), so
# this MUST keep that prefix.
_CLEAR_LOGOS_NOTIFY_SOURCE = "task_clear_emby_logos"
# Update the progress notification at most this often (channels) or this many
# seconds — whichever comes first — so a big lineup doesn't hammer the DB.
_NOTIFY_EVERY_N_CHANNELS = 10
_NOTIFY_EVERY_SECONDS = 3.0


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class ClearEmbyLogosRequest(BaseModel):
    """Body for ``POST /api/emby/clear-logos``.

    Attributes:
        logo_types: Which Emby image types to delete per channel. Defaults to
            all three channel-logo variants (matches the UI's "all checked"
            default). Validated against :data:`VALID_LOGO_IMAGE_TYPES`.
        channel_ids: Optional allowlist of Emby channel ids to target. ``None``
            (default) clears **all** Live TV channels. Present now so the API is
            forward-compatible with a per-group/per-channel filter in the UI
            (PO decision, GH #475) without a breaking change later.
    """

    logo_types: list[str] = Field(
        default_factory=lambda: sorted(VALID_LOGO_IMAGE_TYPES)
    )
    channel_ids: Optional[list[str]] = None
    plan_id: Optional[str] = None
    plan_hash: Optional[str] = None


# ---------------------------------------------------------------------------
# Background-job state (mirrors channels.py bulk-commit, bd-ggxks)
# ---------------------------------------------------------------------------

_CLEAR_LOGOS_JOB_TTL_SECONDS = 1800  # 30 min — matches bulk-commit TTL
_CLEAR_LOGOS_BACKGROUND_TASKS: set[asyncio.Task] = set()


class _ClearLogosJob:
    """In-memory state for one clear-logos run."""

    __slots__ = ("status", "created_at", "completed_at", "error", "result")

    def __init__(self) -> None:
        self.status: str = "running"  # running | completed | failed
        self.created_at: float = time.time()
        self.completed_at: Optional[float] = None
        self.error: Optional[str] = None
        self.result: Optional[dict] = None


_CLEAR_LOGOS_JOBS: dict[str, _ClearLogosJob] = {}


def _prune_old_clear_logos_jobs() -> None:
    """Drop clear-logos jobs older than the TTL so the dict can't grow unbounded."""
    cutoff = time.time() - _CLEAR_LOGOS_JOB_TTL_SECONDS
    stale = [jid for jid, job in _CLEAR_LOGOS_JOBS.items() if job.created_at < cutoff]
    for jid in stale:
        _CLEAR_LOGOS_JOBS.pop(jid, None)
    if stale:
        logger.debug("[EMBY] Pruned %s expired clear-logos jobs", len(stale))


# ---------------------------------------------------------------------------
# Core worker
# ---------------------------------------------------------------------------


def _progress_meta(current, total, deleted, missing, errors, status, current_name):
    """Build the ``metadata.progress`` payload the NotificationCenter renders.

    Field names mirror the stream-probe progress shape so the existing progress
    UI (bar + success/failed/skipped chips + current-item line) renders this
    with no frontend-specific casing: ``success``=deleted, ``failed``=errors,
    ``skipped``=missing (no such image).
    """
    return {
        "progress": {
            "current": current,
            "total": total,
            "success": deleted,
            "failed": errors,
            "skipped": missing,
            "status": status,  # 'clearing' (active) | 'completed' | 'failed'
            "current_stream": current_name,
        }
    }


async def _run_clear_logos(
    base_url: str,
    api_key: str,
    logo_types: list[str],
    channel_ids: Optional[list[str]],
) -> dict:
    """Enumerate Emby Live TV channels and delete the selected logo images.

    Drives a live progress notification (source ``task_clear_emby_logos``) the
    same way stream probe does: create one notification, update it as channels
    are processed (rate-limited), and finalize it success/warning/error. The
    NotificationCenter polls notifications and renders the progress bar.

    The ``get_livetv_channels`` call is the auth gate — a bad key raises here
    (before any DELETE), emits a failed notification, and fails the whole job.
    Per-channel/per-type delete failures are counted and the run continues
    (matches CI's resilient behavior), so one channel lacking a
    ``LogoLightColor`` never aborts the rest.

    Returns a summary dict surfaced to the operator under the job ``result``
    (used by the MCP tool, which polls the job rather than the notifications).
    """
    # One progress notification at a time — drop any stale one from a prior run.
    await delete_notifications_by_source_internal(_CLEAR_LOGOS_NOTIFY_SOURCE)

    client = EmbyClient(base_url=base_url, api_key=api_key)
    notif_id: Optional[int] = None
    try:
        try:
            channels = await client.get_livetv_channels()
        except EmbyClientError as exc:
            # Surface the auth/network failure as a notification too, not just
            # the (MCP-facing) job error.
            await create_notification_internal(
                notification_type="error",
                title="Clear Emby Logos",
                message=f"Clear Emby logos failed: {exc}",
                source=_CLEAR_LOGOS_NOTIFY_SOURCE,
                source_id=str(int(time.time())),
                metadata=_progress_meta(0, 0, 0, 0, 0, "failed", ""),
            )
            raise

        if channel_ids is not None:
            wanted = set(channel_ids)
            channels = [c for c in channels if c.channel_id in wanted]
        total = len(channels)

        created = await create_notification_internal(
            notification_type="info",
            title="Clear Emby Logos",
            message=f"Clearing Emby logos… (0/{total})",
            source=_CLEAR_LOGOS_NOTIFY_SOURCE,
            source_id=str(int(time.time())),
            metadata=_progress_meta(0, total, 0, 0, 0, "clearing", ""),
        )
        notif_id = created.get("id") if isinstance(created, dict) else None

        images_deleted = 0
        images_missing = 0
        errors = 0
        last_update = time.time()
        for idx, ch in enumerate(channels, start=1):
            for logo_type in logo_types:
                try:
                    deleted = await client.delete_item_image(ch.channel_id, logo_type)
                    if deleted:
                        images_deleted += 1
                    else:
                        images_missing += 1
                except EmbyClientError as exc:
                    errors += 1
                    logger.warning(
                        "[EMBY] clear-logos delete failed channel=%s type=%s: %s",
                        ch.channel_id, logo_type, exc,
                    )

            # Rate-limited progress update: every N channels, every few seconds,
            # or on the last channel.
            now = time.time()
            if notif_id and (
                idx % _NOTIFY_EVERY_N_CHANNELS == 0
                or (now - last_update) >= _NOTIFY_EVERY_SECONDS
                or idx == total
            ):
                await update_notification_internal(
                    notification_id=notif_id,
                    notification_type="info",
                    message=f"Clearing Emby logos… ({idx}/{total})",
                    metadata=_progress_meta(
                        idx, total, images_deleted, images_missing, errors,
                        "clearing", ch.name,
                    ),
                )
                last_update = now

        # Finalize: warning if any deletes errored, else success.
        if notif_id:
            final_type = "warning" if errors else "success"
            final_msg = (
                f"Clear Emby logos complete: {images_deleted} logo(s) deleted "
                f"across {total} channel(s)"
                + (f" — {errors} error(s)" if errors else "")
            )
            await update_notification_internal(
                notification_id=notif_id,
                notification_type=final_type,
                message=final_msg,
                metadata=_progress_meta(
                    total, total, images_deleted, images_missing, errors,
                    "completed", "",
                ),
            )

        summary = {
            "channels_processed": total,
            "images_deleted": images_deleted,
            "images_missing": images_missing,
            "errors": errors,
            "logo_types": logo_types,
        }
        logger.info(
            "[EMBY] clear-logos done: channels=%s deleted=%s missing=%s errors=%s types=%s",
            total, images_deleted, images_missing, errors, logo_types,
        )
        return summary
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/clear-logos")
async def clear_emby_logos(
    request: ClearEmbyLogosRequest,
    _admin=RequireAdminIfEnabled,
    caller_is_mcp: bool = ResolveIsMcpServicePrincipalIfEnabled,
):
    """Clear cached Emby channel logos so Emby re-fetches them (GH #475).

    Admin-only (operator action that mutates the Emby server). Reuses the saved
    Emby connection (``emby_base_url`` + ``emby_api_key``) — the same settings
    the resolver/cache already use — so no credentials cross the wire here.

    Returns **202** ``{job_id, status: "running"}``; poll
    ``GET /api/emby/clear-logos/{job_id}`` until terminal. The work runs in a
    supervised background task to stay clear of the 30s request timeout on large
    lineups.

    Errors (synchronous, before the job starts):
    - 400 if ``logo_types`` is empty or contains a value outside
      :data:`VALID_LOGO_IMAGE_TYPES`.
    - 400 if Emby is not enabled / not configured in Settings.
    """
    if caller_is_mcp and not (request.plan_id and request.plan_hash):
        raise HTTPException(status_code=409, detail="MCP execution requires a prepared plan")
    # Validate logo types up front — reject the whole request rather than
    # silently dropping unknown types (defense against an arbitrary image type
    # reaching the DELETE path).
    if not request.logo_types:
        raise HTTPException(status_code=400, detail="At least one logo type is required")
    invalid = [t for t in request.logo_types if t not in VALID_LOGO_IMAGE_TYPES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid logo type(s): {', '.join(invalid)}. "
                f"Allowed: {', '.join(sorted(VALID_LOGO_IMAGE_TYPES))}"
            ),
        )
    # De-dup while preserving the caller's order.
    logo_types = list(dict.fromkeys(request.logo_types))

    settings = get_settings()
    emby_enabled = bool(getattr(settings, "emby_enabled", False))
    base_url = getattr(settings, "emby_base_url", "") or ""
    api_key = getattr(settings, "emby_api_key", "") or ""
    if not emby_enabled or not base_url or not api_key:
        raise HTTPException(
            status_code=400,
            detail="Emby is not enabled/configured — set it up in Settings first",
        )

    if request.plan_id or request.plan_hash:
        if not request.plan_id or not request.plan_hash:
            raise HTTPException(status_code=409, detail="plan_id and plan_hash are both required")
        from services.mutation_plan_store import mutation_plan_store
        try:
            principal = str(getattr(_admin, "id", None) or getattr(_admin, "role", None) or "api")
            plan = mutation_plan_store.consume(
                request.plan_id, "clear_emby_logos", request.plan_hash,
                principal=principal,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if plan.payload["logo_types"] != logo_types:
            raise HTTPException(status_code=409, detail="logo types differ from prepared plan")
        verify_client = EmbyClient(base_url=base_url, api_key=api_key)
        try:
            live_ids = sorted(channel.channel_id for channel in await verify_client.get_livetv_channels())
        finally:
            await verify_client.close()
        if live_ids != plan.payload["channel_ids"]:
            raise HTTPException(status_code=409, detail="Emby channel set drifted; prepare a new plan")
        accounting = {
            "write_count": len(logo_types) * len(live_ids),
            "unique_target_count": len(live_ids),
        }
        if accounting != plan.payload.get("accounting") or max(accounting.values()) >= 500:
            raise HTTPException(status_code=409, detail="Emby plan accounting drifted")
        request.channel_ids = list(plan.payload["channel_ids"])

    _prune_old_clear_logos_jobs()
    job_id = uuid.uuid4().hex
    _CLEAR_LOGOS_JOBS[job_id] = _ClearLogosJob()

    async def _runner() -> None:
        job = _CLEAR_LOGOS_JOBS.get(job_id)
        if job is None:
            logger.warning("[EMBY] clear-logos job %s missing before start", job_id)
            return
        try:
            result = await _run_clear_logos(base_url, api_key, logo_types, request.channel_ids)
            job.result = result
            job.status = "completed"
            job.completed_at = time.time()
        except asyncio.CancelledError:
            job.status = "failed"
            job.error = "Background task cancelled"
            job.completed_at = time.time()
            logger.warning("[EMBY] clear-logos job %s cancelled", job_id)
            raise
        except Exception as e:  # noqa: BLE001 — supervisor must catch broadly
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
            job.completed_at = time.time()
            logger.exception("[EMBY] clear-logos job %s failed: %s", job_id, e)

    task = asyncio.create_task(_runner(), name=f"clear-emby-logos-{job_id}")
    _CLEAR_LOGOS_BACKGROUND_TASKS.add(task)
    task.add_done_callback(_CLEAR_LOGOS_BACKGROUND_TASKS.discard)

    logger.info("[EMBY] clear-logos job %s enqueued (types=%s)", job_id, logo_types)
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "running",
            "message": (
                f"Clear Emby logos started; poll /api/emby/clear-logos/{job_id} "
                "for status"
            ),
        },
    )


@router.post("/clear-logos/prepare")
async def prepare_clear_emby_logos(
    request: ClearEmbyLogosRequest,
    _admin=RequireAdminIfEnabled,
):
    """Enumerate exact Emby targets without creating a job or notification."""
    if not request.logo_types:
        raise HTTPException(status_code=400, detail="At least one logo type is required")
    invalid = [item for item in request.logo_types if item not in VALID_LOGO_IMAGE_TYPES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid logo type(s): {', '.join(invalid)}")
    settings = get_settings()
    base_url = getattr(settings, "emby_base_url", "") or ""
    api_key = getattr(settings, "emby_api_key", "") or ""
    if not getattr(settings, "emby_enabled", False) or not base_url or not api_key:
        raise HTTPException(status_code=400, detail="Emby is not enabled/configured")
    client = EmbyClient(base_url=base_url, api_key=api_key)
    try:
        ids = sorted(channel.channel_id for channel in await client.get_livetv_channels())
    finally:
        await client.close()
    if request.channel_ids is not None:
        requested = set(request.channel_ids)
        ids = [channel_id for channel_id in ids if channel_id in requested]
    from services.mutation_plan_store import canonical_hash, mutation_plan_store
    payload = {"logo_types": list(dict.fromkeys(request.logo_types)), "channel_ids": ids}
    accounting = {
        "write_count": len(payload["logo_types"]) * len(ids),
        "unique_target_count": len(ids),
    }
    if max(accounting.values()) >= 500:
        raise HTTPException(status_code=413, detail={
            "message": "Emby logo plan reaches the 500-operation hard cap", **accounting,
        })
    payload["accounting"] = accounting
    plan = mutation_plan_store.create(
        "clear_emby_logos", payload, canonical_hash(ids),
        str(getattr(_admin, "id", None) or getattr(_admin, "role", None) or "api"),
    )
    return {
        "plan_id": plan.plan_id, "plan_hash": plan.payload_hash,
        "expires_at": plan.expires_at, **payload, **accounting,
    }


@router.get("/clear-logos/{job_id}")
async def get_clear_emby_logos_status(job_id: str):
    """Poll a clear-logos job (GH #475).

    - ``running``   → ``{job_id, status: "running"}``
    - ``failed``    → ``{job_id, status: "failed", error}`` (kept until TTL prune)
    - ``completed`` → ``{job_id, status: "completed", result: <summary>}`` and
      the job is evicted on read (single-shot retrieval).
    - missing job   → 404
    """
    job = _CLEAR_LOGOS_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Clear-logos job not found")
    if job.status == "running":
        return {"job_id": job_id, "status": "running"}
    if job.status == "failed":
        return {
            "job_id": job_id,
            "status": "failed",
            "error": job.error or "unknown error",
        }
    result = job.result or {}
    _CLEAR_LOGOS_JOBS.pop(job_id, None)
    return {"job_id": job_id, "status": "completed", "result": result}
