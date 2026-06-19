"""DBAS backup retention policy (bead 0i2vt.9).

Bounds the on-disk and offsite backup footprint by pruning old backup artifacts
AFTER a verified-successful new backup/upload completes. Two complementary
controls, applied together:

last-N FLOOR
    Always keep the newest ``last_n`` successful backups, regardless of age.
    This is a hard floor: the age control can never reduce the kept set below
    it.

age PRUNE
    ADDITIONALLY prune any backup that is BOTH (a) beyond the newest ``last_n``
    AND (b) older than ``max_age_days``. A backup within the newest N is kept
    even when older than the threshold (the floor wins).

NEVER-ZERO (hard invariant)
    Deletion runs ONLY when the caller asserts a checksum-verified-successful
    NEW backup/upload completed for THAT destination (``verified_success=True``).
    A failed/partial run prunes nothing — a failing job plus working retention
    would otherwise silently erode history to zero (a data-loss spiral). The
    last-N floor is additionally clamped to a minimum of 1 so a misconfigured
    ``last_n=0`` can never empty the destination.

CANONICAL SORT KEY
    Selection AND the age threshold both use the SAME key: the timestamp parsed
    from the artifact FILENAME (``ecm-backup-<YYYY-MM-DD_HHMMSS>.zip``). We never
    mix filename-sort with mtime/created_at — mixing the two would prune the
    wrong file (``list_saved_backups`` sorts by filename while ``created_at``
    reports mtime; see bead grooming notes).

FILESYSTEM-DERIVED, NO CATALOG
    The candidate set is enumerated live from the backup directory (local) or
    the cloud listing (remote) — there is no catalog table (a catalog would be a
    dual-source-of-truth bug). Every candidate is filtered through the existing
    :data:`routers.backup._BACKUP_ZIP_FILENAME_RE` allowlist BEFORE it can be
    selected for deletion, so an arbitrary path can never be unlinked (this is
    the CodeQL path barrier — we do not bypass it).

PER-DESTINATION
    Retention is applied independently to LOCAL (``BACKUPS_DIR``) and to EACH
    cloud destination, each gated on that destination's own verified-success
    flag (ties to the .8 per-target tri-state — never prune on a partial/failed
    run).

SECURITY — cloud delete
    Cloud listing + deletion route through the adapter's ``list_files`` /
    ``delete``, which already go through the ``.5`` SSRF chokepoint and are
    executor-wrapped (a delete is a blocking outbound call to a user URL).

LOGGING — credential-clean
    Logs and audit rows carry ONLY the filename, the reason (``last_n`` / ``age``)
    the destination NAME, and counts — never URLs, credentials, or SDK-exception
    bodies (the .8/.13 lesson).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import journal
from config import CONFIG_DIR
# Reuse the SINGLE compiled artifact-filename allowlist (do NOT build a new
# dynamic regex — Semgrep flags bare re.* on dynamic patterns, and a second
# pattern would be a divergence to reconcile). This is the CodeQL path barrier.
from routers.backup import _BACKUP_ZIP_FILENAME_RE

logger = logging.getLogger(__name__)

# Same directory convention as the rest of the backup subsystem. Module-level so
# tests can monkeypatch it (mirrors tasks.dbas_backup.BACKUPS_DIR).
BACKUPS_DIR = CONFIG_DIR / "backups"

# Ship defaults (PO decision 2026-06-17): keep the newest 7, additionally drop
# beyond-N artifacts older than 30 days.
DEFAULT_LAST_N = 7
DEFAULT_MAX_AGE_DAYS = 30

# Parses the canonical filename timestamp out of an already-allowlisted name.
# This runs ONLY on strings that have passed _BACKUP_ZIP_FILENAME_RE, so it is
# not a security boundary — it is the sort-key extractor.
_TS_RE = re.compile(r"ecm-backup-(\d{4}-\d{2}-\d{2}_\d{6})\.zip$")
_TS_FORMAT = "%Y-%m-%d_%H%M%S"

# Reason labels recorded in the audit row.
REASON_LAST_N = "last_n"
REASON_AGE = "age"


def _filename_timestamp(name: str) -> Optional[datetime]:
    """Parse the canonical UTC timestamp out of a backup-artifact filename.

    Returns a tz-aware UTC datetime, or None if the name does not carry a
    parseable timestamp (such a name is excluded from the prunable set).
    """
    m = _TS_RE.search(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), _TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def select_prunable(
    filenames: list[str],
    last_n: int,
    max_age_days: int,
    now: Optional[datetime] = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Decide which backup filenames to keep vs prune (pure function).

    Applies the last-N FLOOR + age PRUNE algorithm over the canonical filename
    timestamp. Returns ``(kept, pruned)`` where ``pruned`` is a list of
    ``(filename, reason)`` with reason ∈ {``last_n``, ``age``}.

    Guarantees:
      * Only names matching :data:`_BACKUP_ZIP_FILENAME_RE` are ever eligible —
        every other name is silently ignored (never pruned, never counted).
      * The newest ``max(last_n, 1)`` valid backups are ALWAYS kept (the floor;
        clamped to >=1 so a misconfigured ``last_n=0`` can never empty the set).
      * Beyond the floor, a backup is pruned with reason ``age`` if it is older
        than ``max_age_days``; otherwise it is pruned with reason ``last_n`` only
        if it exceeds the floor count, else kept.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Filter through the allowlist + require a parseable timestamp. Build a list
    # of (timestamp, filename) for the eligible artifacts only.
    eligible: list[tuple[datetime, str]] = []
    for name in filenames:
        if not _BACKUP_ZIP_FILENAME_RE.fullmatch(name):
            continue
        ts = _filename_timestamp(name)
        if ts is None:
            continue
        eligible.append((ts, name))

    # Sort newest-first by the canonical filename timestamp (the ONE key used
    # for BOTH last-N selection and the age threshold).
    eligible.sort(key=lambda item: item[0], reverse=True)

    floor = max(int(last_n), 1)  # NEVER-ZERO: keep at least the most recent.
    kept: list[str] = []
    pruned: list[tuple[str, str]] = []

    for idx, (ts, name) in enumerate(eligible):
        if idx < floor:
            kept.append(name)  # within the floor — always kept.
            continue
        # Beyond the floor: age decides.
        age_days = (now - ts).total_seconds() / 86400.0
        if age_days > max_age_days:
            pruned.append((name, REASON_AGE))
        else:
            # Beyond the floor but within the age window. With the floor being a
            # strict newest-N keep, a name beyond the floor that is NOT aged out
            # is still pruned as a last_n overflow ONLY when last_n is the
            # binding control. The PO spec keeps last-N as the floor: anything
            # beyond N is a last_n-prune candidate, with age giving the more
            # specific reason when applicable.
            pruned.append((name, REASON_LAST_N))

    return kept, pruned


def _audit_deletion(filename: str, reason: str, destination: str) -> None:
    """Write one credential-clean audit row for a single pruned artifact.

    Carries ONLY the filename, the reason label, and the destination NAME — no
    URL, credential, or SDK-exception body (the .8/.13 logging lesson).
    """
    try:
        journal.log_entry(
            category="backup_retention",
            action_type="backup_pruned",
            entity_name=filename,
            description=(
                "Pruned backup '%s' from destination '%s' (reason=%s)"
                % (filename, destination, reason)
            ),
            user_initiated=False,
        )
    except Exception as e:  # pragma: no cover — journal best-effort
        logger.warning("[RETENTION] Failed to journal prune audit: %s", e)


def prune_local_backups(
    verified_success: bool,
    last_n: int = DEFAULT_LAST_N,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: Optional[datetime] = None,
    backups_dir: Optional[Path] = None,
) -> dict:
    """Prune local backup artifacts under ``backups_dir`` (defaults to
    :data:`BACKUPS_DIR`; destination ``local``).

    NEVER-ZERO / no-prune-on-failure: returns immediately without deleting
    anything when ``verified_success`` is False.

    Filesystem-derived (glob enumeration), allowlist-guarded, audited per
    deletion. Returns a summary dict ``{deleted, failed, skipped_reason}``.
    """
    if not verified_success:
        logger.info(
            "[RETENTION] Local prune skipped — new backup not verified-successful "
            "(no-prune-on-failure)."
        )
        return {"deleted": 0, "failed": 0, "skipped_reason": "unverified"}

    target_dir = Path(backups_dir) if backups_dir is not None else BACKUPS_DIR
    if not target_dir.exists():
        return {"deleted": 0, "failed": 0, "skipped_reason": None}

    # Filesystem-derived candidate set (reuse the list_saved_backups glob shape).
    candidates = [p.name for p in target_dir.glob("ecm-backup-*.zip")]
    _kept, pruned = select_prunable(candidates, last_n, max_age_days, now)

    deleted = 0
    failed = 0
    safe_root = target_dir.resolve()
    for filename, reason in pruned:
        # Two-layer guard INLINED at the sink (CodeQL py/path-injection,
        # CWE-22/23/36/73/99): the containment barrier is not tracked across a
        # function-return boundary, so the allowlist re-check + the resolve()
        # containment check + the unlink() live in this same body. Layer 1: the
        # strict allowlist (already applied in select_prunable, re-applied here
        # as defense-in-depth at the sink). Layer 2: canonicalize + verify
        # containment under BACKUPS_DIR.
        if not _BACKUP_ZIP_FILENAME_RE.fullmatch(filename):
            continue
        try:
            path = (target_dir / filename).resolve()
            path.relative_to(safe_root)
        except (ValueError, OSError):
            continue
        try:
            path.unlink()
        except OSError as e:
            failed += 1
            logger.warning("[RETENTION] Failed to delete local backup '%s': %s", filename, e)
            continue
        deleted += 1
        logger.info("[RETENTION] Pruned local backup '%s' (reason=%s)", filename, reason)
        _audit_deletion(filename, reason, "local")

    return {"deleted": deleted, "failed": failed, "skipped_reason": None}


async def prune_cloud_destination(
    adapter,
    remote_dir: str,
    dest_name: str,
    verified_success: bool,
    last_n: int = DEFAULT_LAST_N,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: Optional[datetime] = None,
) -> dict:
    """Prune backup artifacts at a single cloud destination.

    ``adapter`` is a :class:`cloud_storage.types.CloudStorageAdapter` whose
    ``list_files`` / ``delete`` already route through the ``.5`` SSRF chokepoint
    and are executor-wrapped (a delete is a blocking outbound call to a user
    URL). ``remote_dir`` is the upload path prefix; ``dest_name`` is the
    operator-facing target NAME used in audit rows (never a URL/credential).

    NEVER-ZERO / no-prune-on-failure (per-destination): returns immediately
    without listing or deleting when ``verified_success`` is False — a partial/
    failed upload to THIS destination prunes nothing.

    Returns a summary dict ``{deleted, failed, skipped_reason}``.
    """
    if not verified_success:
        logger.info(
            "[RETENTION] Cloud prune for destination '%s' skipped — upload not "
            "verified-successful (no-prune-on-failure).", dest_name,
        )
        return {"deleted": 0, "failed": 0, "skipped_reason": "unverified"}

    # Cloud listing (routes through the chokepoint + executor-wrap inside the
    # adapter). Adapters return bare leaf filenames.
    try:
        listing = await adapter.list_files(remote_dir)
    except Exception as e:
        # Do not let a listing failure cascade — log the destination NAME only
        # (never the exception body, which can carry a URL/credential).
        logger.warning(
            "[RETENTION] Cloud list for destination '%s' failed; no prune performed.",
            dest_name,
        )
        logger.debug("[RETENTION] (list failure detail suppressed: %s)", type(e).__name__)
        return {"deleted": 0, "failed": 0, "skipped_reason": "list_failed"}

    names = [Path(str(n)).name for n in (listing or [])]
    _kept, pruned = select_prunable(names, last_n, max_age_days, now)

    prefix = (remote_dir or "").strip("/")
    deleted = 0
    failed = 0
    for filename, reason in pruned:
        # Allowlist re-check at the sink (defense-in-depth — already applied in
        # select_prunable). Only a name matching the artifact pattern is ever
        # passed to adapter.delete.
        if not _BACKUP_ZIP_FILENAME_RE.fullmatch(filename):
            continue
        remote_path = "%s/%s" % (prefix, filename) if prefix else filename
        try:
            ok = await adapter.delete(remote_path)
        except Exception:
            ok = False
            logger.warning(
                "[RETENTION] Delete of '%s' at destination '%s' raised; continuing.",
                filename, dest_name,
            )
        if not ok:
            failed += 1
            logger.warning(
                "[RETENTION] Delete of '%s' at destination '%s' failed.",
                filename, dest_name,
            )
            continue
        deleted += 1
        logger.info(
            "[RETENTION] Pruned backup '%s' from destination '%s' (reason=%s)",
            filename, dest_name, reason,
        )
        _audit_deletion(filename, reason, dest_name)

    return {"deleted": deleted, "failed": failed, "skipped_reason": None}
