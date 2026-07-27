"""Event Sync operator-exclusion tools (bead enhancedchannelmanager-ti939.3.5).

MCP surface for the "never attach this provider stream to that event"
standing orders backing ``/api/event-sync-exclusions``.

Fingerprint-keyed semantics (epic ti939.3 HARD constraint): an exclusion's
identity is the content fingerprint — ``(rule_id, provider_id,
stream_name_hash, event_key)`` — NEVER stream or channel ids (stream ids
churn on every provider refresh; channel ids live only while an event's
channel exists). That is why an exclusion survives refreshes and stream-ID
churn by construction, and why creating one requires the fingerprint
components rather than ids: copy them from a ``preview_event_sync`` row's
context or an ``/api/event-sync-reviews`` record.

The shared resolver consults exclusions BEFORE the attach band on every
run and preview — an excluded pairing reports as ``excluded_by_operator``
and an exclusion OUTRANKS a prior review-queue accept for the same
fingerprint. Removal is the undo: the pairing becomes matchable again on
the next run/preview.
"""
import logging

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)


def _pairing_line(row: dict) -> str:
    """One human-readable line for an exclusion row (evidence-first)."""
    ev = row.get("evidence") or {}
    stream = ev.get("stream_name") or (
        f"fingerprint {str(row.get('stream_name_hash', ''))[:12]}…"
    )
    master = ev.get("master_channel_name") or row.get("event_key")
    parts = [
        f"  #{row.get('id')} [rule {row.get('rule_id')}] "
        f"'{stream}' NEVER attaches to '{master}'"
    ]
    if ev.get("provider"):
        parts.append(f"provider={ev.get('provider')}")
    if row.get("note"):
        parts.append(f"note: {row.get('note')}")
    return " | ".join(parts)


def register(mcp: FastMCP):
    @mcp.tool()
    async def list_event_sync_exclusions(
        rule_id: int | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> str:
        """List Event Sync never-attach exclusions (newest first).

        Each row is a standing operator order: that exact (provider,
        stream-name identity, event identity) pairing never attaches — on
        any run or preview — until the row is deleted. Rows are keyed on
        content fingerprints, never stream/channel ids, so they survive
        provider refreshes and stream-ID churn.

        Args:
            rule_id: Scope to one event_sync rule (default: all rules)
            page: Page number (default 1)
            page_size: Results per page (default 50, max 200)
        """
        try:
            client = get_ecm_client()
            query = {"page": page, "page_size": min(page_size, 200)}
            if rule_id is not None:
                query["rule_id"] = rule_id
            result = await client.call_endpoint(
                ENDPOINTS["es_list_exclusions"], query=query
            )
            if not isinstance(result, dict):
                return f"Unexpected response: {result!r}"

            rows = result.get("exclusions") or []
            total = result.get("total", len(rows))
            if not rows:
                scope = f" for rule {rule_id}" if rule_id is not None else ""
                return (
                    f"No Event Sync exclusions{scope}. Excluded pairings "
                    f"would report as 'excluded_by_operator' in previews."
                )
            lines = [
                f"Event Sync never-attach exclusions "
                f"(page {result.get('page', page)}/"
                f"{result.get('total_pages', 1)}, total {total}):"
            ]
            lines.extend(_pairing_line(row) for row in rows)
            lines.append(
                "Remove one with delete_event_sync_exclusion(exclusion_id) "
                "to make the pairing matchable again."
            )
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_event_sync_exclusions failed: %s", e)
            return f"Error listing event sync exclusions: {e}"

    @mcp.tool()
    async def create_event_sync_exclusion(
        rule_id: int,
        provider_id: int,
        stream_name_hash: str,
        event_key: str,
        note: str | None = None,
        stream_name: str | None = None,
        master_channel_name: str | None = None,
    ) -> str:
        """Create a never-attach exclusion for one exact event pairing.

        Records a durable operator "never": the pairing can neither
        auto-attach (threshold OR a prior review-queue accept — the
        exclusion wins) nor re-enter the review queue, on every future run
        and preview, until deleted.

        Identity is the CONTENT FINGERPRINT — never stream/channel ids:
        copy ``provider_id`` (M3U account id; 0 = unknown-provider
        sentinel), ``stream_name_hash`` (SHA-256 of the normalized
        secondary stream name) and ``event_key`` (normalized master event
        identity) from an ``/api/event-sync-reviews`` record for the
        pairing. That keying is what makes the exclusion survive provider
        refreshes and stream-ID churn.

        Idempotent: excluding an already-excluded fingerprint returns the
        existing row and refreshes its note.

        Args:
            rule_id: The event_sync rule the exclusion is scoped to
            provider_id: M3U account id of the secondary stream (0 = unknown)
            stream_name_hash: SHA-256 hex of the normalized stream name
            event_key: Normalized master event identity string
            note: Optional operator note ("why never")
            stream_name: Optional raw stream name, display-only (shown in
                the exclusions list instead of the bare fingerprint)
            master_channel_name: Optional raw master name, display-only
        """
        try:
            client = get_ecm_client()
            body = {
                "rule_id": rule_id,
                "provider_id": provider_id,
                "stream_name_hash": stream_name_hash,
                "event_key": event_key,
            }
            if note is not None:
                body["note"] = note
            evidence = {}
            if stream_name:
                evidence["stream_name"] = stream_name
            if master_channel_name:
                evidence["master_channel_name"] = master_channel_name
            if evidence:
                body["evidence"] = evidence
            result = await client.call_endpoint(
                ENDPOINTS["es_create_exclusion"], body=body
            )
            if not isinstance(result, dict):
                return f"Unexpected response: {result!r}"
            prefix = (
                "Already excluded (existing standing order returned)"
                if result.get("already_existed")
                else "Exclusion created"
            )
            return (
                f"{prefix}:\n{_pairing_line(result)}\n"
                f"This pairing now reports as 'excluded_by_operator' in "
                f"previews and never auto-attaches — the exclusion also "
                f"outranks any review-queue accept for the same pairing. "
                f"Undo with delete_event_sync_exclusion({result.get('id')})."
            )
        except Exception as e:
            logger.error("[MCP] create_event_sync_exclusion failed: %s", e)
            return f"Error creating event sync exclusion: {e}"

    @mcp.tool()
    async def delete_event_sync_exclusion(
        exclusion_id: int, confirm: bool = False
    ) -> str:
        """Remove a never-attach exclusion (two-step, mirrors other deletes).

        The first call (``confirm=False``, the default) shows the standing
        order that would be removed, deleting NOTHING. Re-invoke with
        ``confirm=True`` to delete. After removal the pairing becomes
        matchable again on the next pipeline run or preview — nothing is
        re-attached immediately (the idempotent run is the applier).

        Args:
            exclusion_id: The exclusion row id (see list_event_sync_exclusions)
            confirm: Set True on the second call to perform the removal.
        """
        try:
            client = get_ecm_client()
            if not confirm:
                result = await client.call_endpoint(
                    ENDPOINTS["es_list_exclusions"],
                    query={"page": 1, "page_size": 200},
                )
                rows = (
                    result.get("exclusions", [])
                    if isinstance(result, dict) else []
                )
                row = next(
                    (r for r in rows if r.get("id") == exclusion_id), None
                )
                if row is None:
                    return (
                        f"Exclusion {exclusion_id} not found (it may already "
                        f"be removed, or lies beyond the first 200 rows — "
                        f"use list_event_sync_exclusions to locate it)."
                    )
                return (
                    f"PREVIEW — nothing removed yet:\n{_pairing_line(row)}\n"
                    f"Removing it makes the pairing matchable again on the "
                    f"next run/preview. Re-invoke with confirm=True to "
                    f"remove."
                )
            await client.call_endpoint(
                ENDPOINTS["es_delete_exclusion"],
                path_args={"exclusion_id": exclusion_id},
            )
            return (
                f"Exclusion {exclusion_id} removed. The pairing becomes "
                f"matchable again on the next pipeline run or preview."
            )
        except Exception as e:
            logger.error("[MCP] delete_event_sync_exclusion failed: %s", e)
            return f"Error deleting event sync exclusion: {e}"
