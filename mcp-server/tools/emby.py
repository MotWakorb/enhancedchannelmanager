"""Emby tools (GH #475, bd-v9tp7).

Currently one tool: ``clear_emby_logos`` — flush Emby's cached channel logos so
Emby re-fetches them from its source on next access. Wraps the backend
``POST /api/emby/clear-logos`` 202+poll job (same contract shape as
bulk-commit, bd-ggxks): submit, then poll
``GET /api/emby/clear-logos/{job_id}`` until terminal.
"""
import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)

# The three Emby channel-logo image types the backend accepts. Kept in sync
# with backend ``emby_client.VALID_LOGO_IMAGE_TYPES``.
_VALID_LOGO_TYPES = ("Primary", "LogoLight", "LogoLightColor")

_CLEAR_LOGOS_POLL_INTERVAL_S = 1.0
_CLEAR_LOGOS_POLL_MAX_WAIT_S = 1800.0  # 30 min — matches backend job TTL


async def _clear_logos_with_wait(client, payload: dict) -> dict:
    """POST /api/emby/clear-logos and unwrap the 202+poll envelope.

    Returns the job ``result`` summary dict on success; raises on poll timeout
    or backend-reported failure.
    """
    response = await client.call_endpoint(ENDPOINTS["emby_clear_logos"], body=payload)
    if not isinstance(response, dict) or response.get("status") != "running":
        # Defensive: a non-202 (e.g. synchronous error envelope) — pass through.
        return response if isinstance(response, dict) else {}

    job_id = response.get("job_id")
    if not job_id:
        raise RuntimeError("Clear-logos returned 202 without a job_id")

    deadline = asyncio.get_event_loop().time() + _CLEAR_LOGOS_POLL_MAX_WAIT_S
    while asyncio.get_event_loop().time() < deadline:
        status = await client.get(f"/api/emby/clear-logos/{job_id}")  # contract-exempt: status-poll for GH #475
        if not isinstance(status, dict):
            raise RuntimeError(f"Clear-logos poll returned non-dict: {type(status).__name__}")
        if status.get("status") == "completed":
            return status.get("result") or {}
        if status.get("status") == "failed":
            raise RuntimeError(
                f"Clear Emby logos failed: {status.get('error', 'unknown error')}"
            )
        await asyncio.sleep(_CLEAR_LOGOS_POLL_INTERVAL_S)
    raise TimeoutError(
        f"Clear-logos job {job_id} did not terminate within {_CLEAR_LOGOS_POLL_MAX_WAIT_S}s"
    )


def register(mcp: FastMCP):
    @mcp.tool()
    async def clear_emby_logos(
        logo_types: list[str] | None = None,
        plan_id: str | None = None,
        plan_hash: str | None = None,
    ) -> str:
        """Clear cached Emby channel logos so Emby re-fetches fresh ones.

        Emby caches channel logos and keeps serving a stale image even after the
        logo changes upstream in Dispatcharr. This deletes the cached image from
        every Emby Live TV channel for the selected image types; Emby
        re-downloads the logo from its source the next time the channel is
        accessed. Requires Emby to be enabled/configured in ECM Settings (reuses
        that saved connection — no credentials needed here).

        This is a background job; the tool waits for it to finish and reports the
        totals.

        Args:
            logo_types: Which Emby image types to clear. Any of
                ``Primary``, ``LogoLight``, ``LogoLightColor``. Omit (or pass an
                empty list) to clear all three (the default).

        Returns:
            A summary line with channels processed and images deleted / missing /
            errored, or an error message.
        """
        types = list(logo_types) if logo_types else list(_VALID_LOGO_TYPES)
        invalid = [t for t in types if t not in _VALID_LOGO_TYPES]
        if invalid:
            return (
                f"Invalid logo type(s): {', '.join(invalid)}. "
                f"Allowed: {', '.join(_VALID_LOGO_TYPES)}"
            )
        try:
            client = get_ecm_client()
            body = {"logo_types": types}
            if plan_id and plan_hash:
                body.update({"plan_id": plan_id, "plan_hash": plan_hash})
            result = await _clear_logos_with_wait(client, body)
            processed = result.get("channels_processed", 0)
            deleted = result.get("images_deleted", 0)
            missing = result.get("images_missing", 0)
            errors = result.get("errors", 0)
            return (
                f"Cleared Emby logos: {deleted} image(s) deleted across "
                f"{processed} channel(s) (types: {', '.join(types)}). "
                f"{missing} had no such image; {errors} error(s). "
                "Emby will re-download logos on next channel access."
            )
        except Exception as e:
            logger.error("[MCP] clear_emby_logos failed: %s", e)
            return f"Error clearing Emby logos: {e}"
