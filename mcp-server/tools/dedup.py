"""MCP tools for interactive stream-to-channel dedup operations.

Implements the three tools locked in ADR-008 §D7:

  mcp__ecm__list_pending_channel_merges  — paginated queue reader
  mcp__ecm__accept_channel_merge         — confirms a merge (POST /accept)
  mcp__ecm__dismiss_channel_merge        — rejects a candidate (POST /dismiss)

All tools wrap the /api/channel-merges/* REST surface via the endpoint-
contract registry (_endpoint_contracts.ENDPOINTS).  4xx responses from the
backend are returned as structured {error: {code, message}} envelopes so the
AI agent can self-recover without catching exceptions:

  - accept 404 (stale target): TARGET_NOT_FOUND — agent should call
    dismiss_channel_merge to clean up the stale pending row.
  - accept 409 (invalid state): INVALID_STATE — row already in a terminal
    state; agent should not retry.
  - dismiss 409 (invalid state): INVALID_STATE — same as above.

5xx responses are re-raised (uncatchable upstream error; the agent cannot
recover automatically).

Auth note (ADR-008 §D6): the actor_token_id recorded in the audit journal is
resolved *server-side* from the bearer token the MCP server sends on every
HTTP request (mcp_api_key from /config/settings.json, mapped by the ECM auth
layer to the corresponding User/token DB id).  The MCP tools do not need to
pass actor identity explicitly — it flows through the Authorization header.

BD-O (bd-70ylc).
"""
import logging
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def _http_status_code(exc: Exception) -> int | None:
    """Extract the HTTP status code from an httpx.HTTPStatusError buried inside
    a RuntimeError raised by ecm_client._http_error(), or from a bare
    httpx.HTTPStatusError if one leaks through.

    ecm_client wraps HTTPStatusError as RuntimeError(f"<METHOD> <path> ->
    HTTP <code> <reason>[: <detail>]").  We parse the embedded code rather than
    relying on the exception type being preserved, because call_endpoint always
    re-wraps via _http_error.  Returns None when the status cannot be extracted
    (so callers can fall through to re-raise).
    """
    msg = str(exc)
    # Fast-path: look for "-> HTTP <digits>" marker from _http_error.
    marker = "-> HTTP "
    idx = msg.find(marker)
    if idx != -1:
        tail = msg[idx + len(marker):]
        code_str = tail.split()[0] if tail.split() else ""
        if code_str.isdigit():
            return int(code_str)
    # Direct httpx.HTTPStatusError (shouldn't normally surface, but handle
    # defensively so tests that inject it directly still work).
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return exc.response.status_code
    return None


def _error_envelope(code: str, message: str) -> dict:
    """Return the structured error envelope the AI agent consumes.

    Per ADR-008 §D7: return {error: {code, message}} — do NOT raise.
    """
    return {"error": {"code": code, "message": message}}


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_pending_channel_merges(
        group_id: Optional[int] = None,
        status: Optional[str] = None,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> dict:
        """List pending channel-merge candidates from the dedup queue.

        Returns a paginated list of pending merge rows that need operator or
        AI-agent review.  Each row represents a stream that was flagged as a
        probable duplicate of an existing channel during M3U import, drag-drop,
        or an Add Stream action.

        Args:
            group_id: Filter by channel group id. Omit for all groups.
            status: One of 'pending' (default), 'merged', or 'dismissed'.
                    Pass 'merged' or 'dismissed' to browse the resolved
                    history.
            page: Page number, 1-based (default 1).
            page_size: Rows per page (default 50, max 200).

        Returns:
            {merges: [...], total, page, page_size, total_pages}
            Each merge row contains: id, stream_name, group_id,
            candidate_channel_id, confidence, status, created_at,
            resolved_at, resolution_source, trigger_context.
        """
        client = get_ecm_client()
        query: dict = {}
        # Build query params — only pass what was explicitly provided.
        if status is not None:
            query["status"] = status
        else:
            query["status"] = "pending"  # ADR-008 §D7: default status='pending'
        if group_id is not None:
            query["group_id"] = group_id
        if page is not None:
            query["page"] = page
        if page_size is not None:
            query["page_size"] = page_size

        try:
            return await client.call_endpoint(
                ENDPOINTS["channel_merges_list"],
                query=query,
            )
        except Exception as e:
            logger.error("[MCP-DEDUP] list_pending_channel_merges failed: %s", e)
            raise

    @mcp.tool()
    async def accept_channel_merge(merge_id: int) -> dict:
        """Accept a pending channel merge — triggers the Dispatcharr merge.

        Confirms the dedup decision for the given merge row: adds the stream to
        the candidate channel in Dispatcharr and transitions the pending row to
        'merged' with a full audit trail entry.

        The merge is idempotent: calling accept on a row that is already merged
        returns the original outcome envelope without error.

        On success, returns:
            {merged_into_channel_id, journal_entry_id, source_stream_id,
             confidence, status}

        On 4xx (returns structured error envelope — does NOT raise):
            404 TARGET_NOT_FOUND: The candidate channel no longer exists in
                Dispatcharr.  The pending row stays 'pending' so you can call
                dismiss_channel_merge to clean it up, then re-trigger the
                original import or drag-drop.
            409 INVALID_STATE: The pending row is already in a terminal state
                that prevents this transition (e.g. already dismissed).
                Do not retry.

        On 5xx: raises (uncatchable upstream error).

        Args:
            merge_id: The id of the pending_merges row to accept.
        """
        client = get_ecm_client()
        try:
            result = await client.call_endpoint(
                ENDPOINTS["channel_merges_accept"],
                path_args={"merge_id": merge_id},
            )
            # Backend returns a flat outcome envelope (ADR-008 §D1 flat-outcome
            # pattern).  Add status='merged' to match the declared return shape.
            if isinstance(result, dict) and "status" not in result:
                result = dict(result)
                result["status"] = "merged"
            return result
        except Exception as e:
            status_code = _http_status_code(e)
            if status_code == 404:
                logger.warning(
                    "[MCP-DEDUP] accept_channel_merge id=%s: target not found (404): %s",
                    merge_id, e,
                )
                return _error_envelope(
                    "TARGET_NOT_FOUND",
                    (
                        f"Pending merge id={merge_id}: target channel no longer "
                        "exists in Dispatcharr. Call dismiss_channel_merge to "
                        "clean up this stale row, then re-trigger the original "
                        "import or drag-drop to get a fresh candidate."
                    ),
                )
            if status_code == 409:
                logger.warning(
                    "[MCP-DEDUP] accept_channel_merge id=%s: invalid state (409): %s",
                    merge_id, e,
                )
                return _error_envelope(
                    "INVALID_STATE",
                    (
                        f"Pending merge id={merge_id} is already in a terminal "
                        "state that does not allow acceptance. "
                        "Do not retry this operation."
                    ),
                )
            # 5xx or unexpected: re-raise so the agent sees the upstream error.
            logger.error(
                "[MCP-DEDUP] accept_channel_merge id=%s failed: %s", merge_id, e
            )
            raise

    @mcp.tool()
    async def dismiss_channel_merge(merge_id: int) -> dict:
        """Dismiss a pending channel merge — rejects the dedup candidate.

        Records the operator / agent decision to NOT merge the stream into the
        candidate channel.  No Dispatcharr call is made; the decision is
        recorded in the audit journal only.

        Dismissal is idempotent: calling dismiss on an already-dismissed row
        returns the original outcome envelope without error.

        Dismiss is the recovery action for a stale-target 404 from
        accept_channel_merge (ADR-008 §D4): if accept returns
        {error: {code: 'TARGET_NOT_FOUND', ...}}, call dismiss to clean up
        the stale pending row.

        On success, returns: {journal_entry_id, status: 'dismissed'}

        On 4xx (returns structured error envelope — does NOT raise):
            409 INVALID_STATE: The pending row is already in a terminal state
                that prevents dismissal (e.g. already merged).  Do not retry.

        On 5xx: raises (uncatchable upstream error).

        Args:
            merge_id: The id of the pending_merges row to dismiss.
        """
        client = get_ecm_client()
        try:
            result = await client.call_endpoint(
                ENDPOINTS["channel_merges_dismiss"],
                path_args={"merge_id": merge_id},
            )
            # Backend returns {journal_entry_id, ...}.  Add status='dismissed'.
            if isinstance(result, dict) and "status" not in result:
                result = dict(result)
                result["status"] = "dismissed"
            return result
        except Exception as e:
            status_code = _http_status_code(e)
            if status_code == 409:
                logger.warning(
                    "[MCP-DEDUP] dismiss_channel_merge id=%s: invalid state (409): %s",
                    merge_id, e,
                )
                return _error_envelope(
                    "INVALID_STATE",
                    (
                        f"Pending merge id={merge_id} is already in a terminal "
                        "state that does not allow dismissal (e.g. already "
                        "merged). Do not retry this operation."
                    ),
                )
            # 5xx or unexpected: re-raise.
            logger.error(
                "[MCP-DEDUP] dismiss_channel_merge id=%s failed: %s", merge_id, e
            )
            raise
