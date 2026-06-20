"""System management tools (settings, backup, journal)."""
import logging
import re

from mcp.server.fastmcp import FastMCP

from _endpoint_contracts import ENDPOINTS
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
        """Create and PERSIST a full backup of all ECM configuration (settings,
        database, logos) to the server.

        Unlike a plain download, this saves the backup on the server with a
        stable timestamped filename so it is discoverable via
        ``list_saved_backups`` and restorable via ``restore_backup``. Returns
        that filename — use it to drive the list/restore workflow.
        """
        try:
            # POST /api/backup/save persists the ZIP server-side and returns
            # JSON metadata ({filename, size_bytes, created_at}). The legacy GET
            # /create still streams a binary ZIP for the UI download and is
            # unchanged (bd-0hjrk.5).
            client = get_ecm_client()
            result = await client.call_endpoint(ENDPOINTS["backup_save"], timeout=120.0)
            filename = result.get("filename", "?") if isinstance(result, dict) else "?"
            size_kb = (result.get("size_bytes", 0) / 1024) if isinstance(result, dict) else 0
            return (
                f"Backup saved: {filename} ({size_kb:.0f} KB). "
                "List with list_saved_backups, restore with restore_backup."
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
        """List saved backup files on the server, newest first.

        Includes both full on-demand ZIP archives (type=zip, created by
        create_backup) and scheduled YAML exports (type=yaml). The filename is
        the stable id — pass a type=zip filename to restore_backup, or any to
        delete_saved_backup.
        """
        try:
            client = get_ecm_client()
            backups = await client.call_endpoint(ENDPOINTS["backup_list_saved"])
            if not backups:
                return "No saved backups."
            lines = [f"Saved backups ({len(backups)}):"]
            for b in backups:
                size_kb = b.get("size_bytes", 0) / 1024
                btype = b.get("type", "?")
                lines.append(
                    f"  [{btype}] {b['filename']} — {size_kb:.1f} KB ({b.get('created_at', '?')})"
                )
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
    async def restore_backup(filename: str) -> str:
        """Restore ECM configuration from a saved backup ZIP on the server.

        WARNING: this OVERWRITES the current ECM state — settings, the journal
        database, and logos are all replaced with the contents of the backup.
        This is destructive and cannot be undone except by restoring another
        backup. Confirm with the operator before calling.

        Only full ZIP archives (type=zip from list_saved_backups, created by
        create_backup) can be restored here; scheduled YAML exports use a
        different selective-restore path and are rejected.

        Args:
            filename: The saved ZIP backup filename, e.g.
                ecm-backup-2026-05-24_120000.zip (from list_saved_backups).
        """
        try:
            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["backup_restore_saved"],
                body={"filename": filename},
                timeout=120.0,
            )
            if isinstance(result, dict):
                restored = result.get("restored_files", [])
                version = result.get("backup_version", "unknown")
                return (
                    f"Restored {filename} (backup version {version}): "
                    f"{len(restored)} file(s) restored — {', '.join(restored)}. "
                    "ECM state was overwritten from the backup."
                )
            return f"Restored {filename}."
        except Exception as e:
            # Surface backend validation errors (e.g. HTTP 400 Invalid filename)
            # verbatim so the operator sees WHY a restore was rejected.
            logger.error("[MCP] restore_backup failed: %s", e)
            return f"Error restoring backup: {e}"

    @mcp.tool()
    async def create_dbas_backup(
        passphrase: str | None = None,
        include_credentials: bool = False,
        acknowledge_unrecoverable: bool = False,
    ) -> str:
        """Create a v0.18.0 DBAS backup artifact (ecm-backup-<ts>.zip) on the
        server, optionally PASSPHRASE-ENCRYPTED and credential-carrying.

        Triggers the ``dbas_backup`` task. By default (no passphrase) the
        artifact is a REDACTED backup — settings-class credentials (SMTP
        password, API keys, bot tokens) are scrubbed — suitable for review and
        host-failure recovery.

        ENCRYPTED MODE (ADR-012 D12): to produce a backup that PRESERVES
        credentials, you MUST pass BOTH ``passphrase`` AND
        ``acknowledge_unrecoverable=True`` AND ``include_credentials=True``. The
        whole artifact is encrypted under the passphrase.

        WARNING — UNRECOVERABLE: if the passphrase is lost, the encrypted backup
        CANNOT be decrypted or restored by anyone. There is no recovery, reset,
        or backdoor. Store the passphrase somewhere safe BEFORE relying on the
        backup. ``acknowledge_unrecoverable=True`` is your confirmation that you
        understand this.

        SECURITY: the passphrase is sent to the server only; it is NEVER logged,
        echoed in this tool's result, or returned in any response. The result
        reports only the artifact filename and status.

        Args:
            passphrase: Optional. When set (with the two acknowledgement flags),
                encrypts the whole artifact under this secret. Omit for a
                redacted, unencrypted backup. NEVER logged or echoed back.
            include_credentials: Preserve settings-class credentials in the
                artifact instead of redacting them. Only honored with a
                passphrase (an unencrypted cred-carrying backup is refused by
                the backend). Default False.
            acknowledge_unrecoverable: Required True for an encrypted backup —
                your acknowledgement that a lost passphrase means the backup is
                permanently unrecoverable. Default False.
        """
        try:
            # Build the ad-hoc task parameters. The passphrase rides in the JSON
            # body of POST /api/tasks/dbas_backup/run; the backend task_engine
            # redacts it from logs/journal and the task excludes it from
            # get_config so it is never persisted.
            parameters: dict = {
                "include_credentials": bool(include_credentials),
                "acknowledge_unrecoverable": bool(acknowledge_unrecoverable),
            }
            encrypted = bool(passphrase)
            if encrypted:
                parameters["passphrase"] = passphrase

            # Log WITHOUT the passphrase value — only that an encrypted backup
            # was requested. NEVER interpolate the passphrase into a log line.
            logger.info(
                "[MCP] create_dbas_backup requested (encrypted=%s, include_credentials=%s)",
                encrypted, bool(include_credentials),
            )

            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["tasks_run"],
                path_args={"task_id": "dbas_backup"},
                body={"parameters": parameters},
                timeout=300.0,
            )

            # Surface ONLY the filename + status/message — never the passphrase.
            if isinstance(result, dict):
                status = result.get("status", "")
                details = result.get("details") if isinstance(result.get("details"), dict) else {}
                filename = details.get("filename", "?")
                mode = "encrypted" if encrypted else "redacted"
                status_info = f" Status: {status}." if status else ""
                return (
                    f"DBAS backup ({mode}) created: {filename}.{status_info} "
                    "List with list_saved_backups."
                ).rstrip()
            return "DBAS backup task started."
        except Exception as e:
            # NB: the passphrase is NOT in `e` — call_endpoint surfaces the
            # endpoint path + HTTP status + backend detail, never the body.
            logger.error("[MCP] create_dbas_backup failed: %s", e)
            return f"Error creating DBAS backup: {e}"

    @mcp.tool()
    async def restore_dbas_backup_saved(
        filename: str,
        confirm_apply: bool = False,
        passphrase: str | None = None,
    ) -> str:
        """Restore from a SAVED v0.18.0 DBAS artifact on the server, by filename.

        DRY-RUN BY DEFAULT: with ``confirm_apply`` omitted/False this runs a
        counts-only PREVIEW that makes ZERO mutation — use it to see what a
        restore WOULD do. Pass ``confirm_apply=True`` only when you intend to
        APPLY the restore, which OVERWRITES current ECM state from the artifact.
        This is destructive and cannot be undone except by restoring another
        backup. Confirm with the operator before applying.

        ENCRYPTED ARTIFACTS: a passphrase-encrypted backup (created via
        create_dbas_backup with a passphrase) requires that SAME passphrase here
        to decrypt. Omit ``passphrase`` for a plain/redacted artifact.

        This is the SAVED-file restore path (no file upload over MCP). The
        artifact must already be on the server — list candidates with
        list_saved_backups. The saved file is NOT deleted by the restore.

        SECURITY: the passphrase is sent to the server only; it is NEVER logged,
        echoed in this tool's result, or returned in any response.

        Triggers the async ``dbas_restore`` task and returns its task_id; poll
        task history / status for the terminal RestoreReport.

        Args:
            filename: The saved DBAS artifact filename, e.g.
                ecm-backup-2026-05-24_120000.zip (from list_saved_backups).
            confirm_apply: False (default) = dry-run preview (no changes). True =
                APPLY the restore (OVERWRITES current state).
            passphrase: Required for an encrypted artifact; the same passphrase
                used to create it. NEVER logged or echoed back. Omit for a plain
                artifact.
        """
        try:
            body: dict = {
                "filename": filename,
                "confirm_apply": bool(confirm_apply),
            }
            encrypted = bool(passphrase)
            if encrypted:
                body["passphrase"] = passphrase

            # Log WITHOUT the passphrase value.
            logger.info(
                "[MCP] restore_dbas_backup_saved requested (filename=%s, confirm_apply=%s, encrypted=%s)",
                filename, bool(confirm_apply), encrypted,
            )

            client = get_ecm_client()
            result = await client.call_endpoint(
                ENDPOINTS["backup_restore_dbas_saved"],
                body=body,
                timeout=300.0,
            )

            if isinstance(result, dict):
                task_id = result.get("task_id", "dbas_restore")
                is_dry_run = result.get("is_dry_run", not confirm_apply)
                mode = "DRY-RUN preview (no changes)" if is_dry_run else "APPLY (state overwritten)"
                return (
                    f"DBAS restore-from-saved started for {filename} — {mode}. "
                    f"Poll task '{task_id}' history for the restore report."
                )
            return f"DBAS restore-from-saved started for {filename}."
        except Exception as e:
            # The passphrase is NOT in `e` — call_endpoint never echoes the body.
            logger.error("[MCP] restore_dbas_backup_saved failed: %s", e)
            return f"Error restoring DBAS backup: {e}"

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
