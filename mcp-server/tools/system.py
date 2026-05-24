"""System management tools (settings, backup, journal)."""
import logging
import re

import httpx
from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
from config import ECM_URL, get_mcp_api_key
from ecm_client import get_ecm_client

logger = logging.getLogger(__name__)

# Backend GET /api/journal clamps page_size to [1, 200]. The MCP tool honors an
# arbitrary ``limit`` by paginating CLIENT-SIDE at this page size — never bump
# the backend cap (bd-0hjrk.1).
_JOURNAL_PAGE_SIZE = 200

# Fallback parse of the merged stream id out of the per-merge journal
# description ("Merged stream <id> into channel '<name>'") when the
# before/after stream-id values are absent (bd-0emgo.5 / bd-0hjrk.1).
_MERGED_STREAM_RE = re.compile(r"Merged stream (\d+) into channel")


def _stream_ids(value) -> list | None:
    """Pull the ``stream_ids`` list out of a journal before/after value, which is
    shaped ``{"stream_ids": [...]}`` (see auto_creation_executor._journal_merge).
    Returns None when absent so callers can detect the fallback path."""
    if isinstance(value, dict):
        ids = value.get("stream_ids")
        if isinstance(ids, list):
            return ids
    return None


def _format_journal_row(e: dict, include_stream_ids: bool) -> str:
    """Render ONE journal entry as a compact single line (bd-0hjrk.1).

    Always: ``[ts] category/action: entity_name`` and, when present,
    ``channel_id=<entity_id>``. For ``merge_stream`` rows also the added
    ``stream_id`` (after - before, falling back to the description) and the
    ``streams: <before>→<after>`` count delta. ``include_stream_ids`` appends
    the full before/after lists for a deep audit.
    """
    ts = e.get("timestamp", "?")
    cat = e.get("category", "?")
    action = e.get("action_type", e.get("action", "?"))
    name = e.get("entity_name")

    head = f"  [{ts}] {cat}/{action}:"
    if name:
        head += f" {name}"

    extras: list[str] = []
    entity_id = e.get("entity_id")
    if entity_id is not None:
        extras.append(f"channel_id={entity_id}")

    if action == "merge_stream":
        before = _stream_ids(e.get("before_value")) or []
        after = _stream_ids(e.get("after_value")) or []
        added = sorted(set(after) - set(before))
        if added:
            extras.append(f"stream_id={added[0]}")
        else:
            # Fallback: parse the merged stream id from the description.
            m = _MERGED_STREAM_RE.search(e.get("description") or "")
            if m:
                extras.append(f"stream_id={m.group(1)}")
        if before or after:
            extras.append(f"streams: {len(before)}→{len(after)}")
        if include_stream_ids:
            extras.append(f"before={before} after={after}")
    else:
        # Non-merge rows: keep the short description for context (None-safe).
        detail = (e.get("description") or e.get("detail") or "")[:80]
        if detail:
            extras.append(detail)

    if extras:
        return f"{head} — {' '.join(extras)}"
    return head


def register(mcp: FastMCP):
    @mcp.tool()
    async def get_settings() -> str:
        """Get current ECM settings (connection status, preferences, probe configuration)."""
        try:
            client = get_ecm_client()
            s = await client.call_endpoint(ENDPOINTS["settings_get"])

            lines = [
                "ECM Settings:",
                f"  Dispatcharr URL: {s.get('url', 'Not configured')}",
                f"  Connected: {s.get('configured', False)}",
                f"  Theme: {s.get('theme', 'dark')}",
                f"  Timezone: {s.get('user_timezone', 'UTC') or 'UTC'}",
                "",
                "Probe Settings:",
                f"  Timeout: {s.get('stream_probe_timeout', 30)}s",
                f"  Parallel: {s.get('parallel_probing_enabled', True)}",
                f"  Max Concurrent: {s.get('max_concurrent_probes', 8)}",
                f"  Schedule: {s.get('stream_probe_schedule_time', '03:00')}",
                "",
                "Notifications:",
                f"  SMTP: {'configured' if s.get('smtp_configured') else 'not configured'}",
                f"  Discord: {'configured' if s.get('discord_configured') else 'not configured'}",
                f"  Telegram: {'configured' if s.get('telegram_configured') else 'not configured'}",
            ]

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_settings failed: %s", e)
            return f"Error getting settings: {e}"

    @mcp.tool()
    async def create_backup() -> str:
        """Create a backup of all ECM configuration (settings, database, logos)."""
        try:
            # /api/backup/create streams a binary ZIP — reading it via the shared
            # ECMClient would call r.json() on binary bytes and crash with a
            # UnicodeDecodeError (lq38l.10).  Use a short-lived httpx client to
            # consume the raw bytes instead.
            url = f"{ECM_URL.rstrip('/')}/api/backup/create"
            headers = {"Authorization": f"Bearer {get_mcp_api_key()}"}
            async with httpx.AsyncClient(timeout=60.0) as http:
                r = await http.get(url, headers=headers)
                r.raise_for_status()
                size_kb = len(r.content) / 1024
            return (
                f"Backup created successfully ({size_kb:.1f} KB). "
                "Download it from the ECM Settings page."
            )
        except Exception as e:
            logger.error("[MCP] create_backup failed: %s", e)
            return f"Error creating backup: {e}"

    @mcp.tool()
    async def get_export_sections() -> str:
        """List available YAML export sections (for selective backup)."""
        try:
            client = get_ecm_client()
            sections = await client.call_endpoint(ENDPOINTS["backup_export_sections"])
            if not sections:
                return "No export sections available."
            lines = ["Available export sections:"]
            for s in sections:
                lines.append(f"  - {s['key']}: {s['label']}")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_export_sections failed: %s", e)
            return f"Error: {e}"

    @mcp.tool()
    async def list_saved_backups() -> str:
        """List saved YAML backup files on the server (created by scheduled backup task)."""
        try:
            client = get_ecm_client()
            backups = await client.call_endpoint(ENDPOINTS["backup_list_saved"])
            if not backups:
                return "No saved backups."
            lines = [f"Saved backups ({len(backups)}):"]
            for b in backups:
                size_kb = b.get("size_bytes", 0) / 1024
                lines.append(f"  {b['filename']} — {size_kb:.1f} KB ({b.get('created_at', '?')})")
            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] list_saved_backups failed: %s", e)
            return f"Error: {e}"

    @mcp.tool()
    async def delete_saved_backup(filename: str) -> str:
        """Delete a saved YAML backup file from the server.

        Args:
            filename: The backup filename (e.g., ecm-backup-2026-04-07_120000.yaml)
        """
        try:
            client = get_ecm_client()
            await client.call_endpoint(ENDPOINTS["backup_delete_saved"], path_args={"filename": filename})
            # Read-back: confirm the file no longer appears in the saved list.
            try:
                backups = await client.call_endpoint(ENDPOINTS["backup_list_saved"])
                still_present = any(
                    isinstance(b, dict) and b.get("filename") == filename for b in (backups or [])
                )
            except Exception:
                still_present = None
            if still_present is True:
                return f"WARNING: requested deletion of {filename} but it still appears in the saved-backups list."
            return f"Deleted backup: {filename}"
        except Exception as e:
            logger.error("[MCP] delete_saved_backup failed: %s", e)
            return f"Error: {e}"

    @mcp.tool()
    async def get_journal(
        limit: int = 20,
        category: str | None = None,
        batch_id: str | None = None,
        offset: int = 0,
        include_stream_ids: bool = False,
    ) -> str:
        """Get entries from the ECM activity journal/audit log.

        ``limit`` is now honored in full via CLIENT-SIDE pagination: the backend
        GET /api/journal clamps ``page_size`` to 200, so this tool loops pages
        (200 rows each, filters threaded through every request) until it has
        ``limit`` rows or the results are exhausted. The old 200-row cap is gone
        — a 752-merge auto-creation run is fully reachable (bd-0hjrk.1).

        Output is COMPACT — one line per row — so a large response does not blow
        the MCP token budget. Each row shows the timestamp, ``category/action``,
        and entity_name; ``channel_id=<entity_id>`` is always included when
        present. For ``merge_stream`` rows (auto-creation merges) it also
        surfaces the added ``stream_id`` (= after.stream_ids - before.stream_ids,
        falling back to parsing the description) plus the ``streams: <before>→
        <after>`` count delta, so an operator can drive the journal →
        bulk_remove_streams recovery path without a second lookup.

        Args:
            limit: Total number of entries to return. Honored via pagination —
                no longer capped at the backend's 200-row page size (default 20).
            category: Filter by category (e.g., 'channels', 'm3u', 'epg',
                'settings', 'auto_creation').
            batch_id: Filter to a single batched operation's rows. For an
                auto-creation run this is the run's execution_id (as a string),
                so an operator recovering from a bad merge can list every
                (channel_id, stream_id) pair the run touched (bd-0emgo.5).
                Also matches the 8-char batch_id from bulk handlers.
            offset: Number of leading (newest-first) rows to skip before
                collecting ``limit`` rows. Lets an operator page through a large
                batch (default 0).
            include_stream_ids: When True, append the full before/after
                stream-id lists per row for a deep audit. Default False keeps the
                output compact (counts only) to protect the token budget.
        """
        try:
            client = get_ecm_client()
            offset = max(offset, 0)
            limit = max(limit, 0)

            # ----------------------------------------------------------------
            # Paginate client-side. The backend serves newest-first and clamps
            # page_size to [1, 200]; we request _JOURNAL_PAGE_SIZE per call,
            # starting at the page that covers ``offset``, and accumulate rows
            # until we have ``limit`` of them or the envelope says we are done.
            # Filters (category/batch_id) ride on EVERY page.
            # ----------------------------------------------------------------
            base_query: dict = {"page_size": _JOURNAL_PAGE_SIZE}
            if category:
                base_query["category"] = category
            if batch_id:
                # Indexed filter (idx_journal_batch_id) — passed through to the
                # backend journal_list endpoint's batch_id query param.
                base_query["batch_id"] = batch_id

            start_page = (offset // _JOURNAL_PAGE_SIZE) + 1
            skip_in_first_page = offset % _JOURNAL_PAGE_SIZE

            items: list = []
            total = 0
            page = start_page
            first = True
            while limit == 0 or len(items) < limit:
                query = dict(base_query, page=page)
                envelope = await client.call_endpoint(
                    ENDPOINTS["journal_list"], query=query
                )

                # Backend returns {"count","page","page_size","total_pages",
                # "results":[...]} (backend/journal.py get_entries). Older drafts
                # used "entries"; a bare list is also tolerated.
                if isinstance(envelope, list):
                    results = envelope
                    total = max(total, len(results))
                else:
                    results = envelope.get(
                        "results", envelope.get("entries", [])
                    )
                    total = envelope.get("count", total)

                # Page fullness must be judged BEFORE the offset slice — slicing
                # off the first ``skip_in_first_page`` rows would otherwise make
                # a full page look exhausted and stop the loop early.
                page_was_full = len(results) >= _JOURNAL_PAGE_SIZE

                if first and skip_in_first_page:
                    results = results[skip_in_first_page:]
                first = False

                items.extend(results)

                # Stop when the backend page was not full (results exhausted) or
                # we've already collected enough rows.
                if not page_was_full:
                    break
                if limit and len(items) >= limit:
                    break
                page += 1

            if limit:
                items = items[:limit]

            if not items:
                return "No journal entries found."

            shown = len(items)
            header_total = total if total else shown
            lines = [f"Journal entries (showing {shown} of {header_total} total):"]
            for e in items:
                lines.append(_format_journal_row(e, include_stream_ids))

            return "\n".join(lines)
        except Exception as e:
            logger.error("[MCP] get_journal failed: %s", e)
            return f"Error getting journal: {e}"
