"""DBAS Backup Task (bead 0i2vt.6).

Scheduled + manually-triggerable producer of the new-format DBAS backup
artifact (the ``.7`` builder, :func:`routers.backup.build_backup_artifact`).
The artifact is a redacted, sealed ZIP plus a ``.sha256`` sidecar written into
``CONFIG_DIR / "backups"`` (same dir convention as ``YamlBackupTask``).

Schedules: MANUAL (default), CRON and INTERVAL, via the existing
``ScheduleConfig`` mechanism. Manual triggering uses the existing
``POST /api/tasks/{task_id}/run`` endpoint — no bespoke endpoint here.

Ships OFF by default (``default_enabled = False``) per the PO decision: a
less-engaged operator can't silently end up with zero backups, so the
one-time "set one up" banner (a separate frontend sub-task) covers discovery.

Fire-time credential-freshness gate (ADR-008 Security Mandatory #5)
------------------------------------------------------------------
The task config may carry an optional ``cloud_target_id`` plus the
``cloud_credential_version`` captured when the schedule was configured. There
is NO owner/user_id model (ECM is single-admin) and NO new DB column — the
two fields live in the task config JSON (``task_schedules.parameters``).

At fire time, when ``cloud_target_id`` is set the task re-reads the
``CloudStorageTarget`` FRESH from the DB and ABORTS the run (no artifact
produced) if ANY of:

  * the target is missing,
  * ``enabled`` is False,
  * ``token_revoked_at`` is not None (hard stop), or
  * ``credential_version`` no longer matches the captured version.

Cloud upload (bead 0i2vt.8)
---------------------------
After the artifact is built, the task uploads it (+ ``.sha256`` sidecar) to each
target listed in the ``cloud_targets`` config (a list of
``{cloud_target_id, cloud_credential_version}``). Per-target tri-state run
semantics (PO decision 2026-06-17): the run is ``success`` only if ALL
configured+enabled targets succeed, ``partial`` if some, ``failed`` if none;
``partial`` and ``failed`` both fire a NotificationCenter notification and
reconcile against ``ecm_backup_runs_total`` (the run is NOT reported as a clean
``success``). Each target is re-validated FRESH against the DB right before its
upload (the per-target generalisation of the single-target gate below); a
stale/revoked target is a non-silent per-target failure. Every upload routes
through the ``.5`` SSRF chokepoint, is executor-wrapped, streams from disk, and
masks credentials in logs. Uploads run ONE AT A TIME (cap=1) to bound memory.

The legacy single ``cloud_target_id`` pre-build gate (below) is unchanged: when
set, a stale bound target ABORTS the whole run (no artifact) — distinct from the
``cloud_targets`` post-build per-target failure path.

Silent-skip MUST notify
-----------------------
A freshness-gate abort is NOT silent: it emits a WARN log (``[DBAS_BACKUP]``
prefix), a ``journal`` entry, AND a NotificationCenter notification, and
returns ``TaskResult(success=False, ...)``. A scheduled backup that silently
stops = false safety.

Degraded gather = WARNING, never a silent clean success (bead zt3kf)
---------------------------------------------------------------------
``build_backup_artifact`` gathers Dispatcharr-managed categories per-key
(``routers.backup._gather_dispatcharr_sections``), and each category's fetch
is isolated: a single upstream failure stubs ONLY that category into the
artifact as ``{"_warning": ...}`` rather than aborting the whole gather or
degrading unrelated categories. Before this fix, that stub was invisible
downstream — a backup missing an entire category (e.g. every
``dvr_rules.yaml`` on a run where the Dispatcharr endpoint errored) still
reported a clean ``TaskResult(success=True, failed_count=0)``. Now:
``artifact.degraded_categories`` (populated by the builder) is threaded into
``details["degraded_categories"]`` and the run is reported as WARNING-level —
``success`` stays ``True`` (a real, checksum-verified artifact WAS produced
and is useful), but ``failed_count`` is set so task_engine.py's existing
"Completed with Warnings" branch fires, naming the degraded category/
categories in both the task message and the completion notification
(``task_engine._warning_task_completion_message``). Cloud-upload health
(below) takes precedence when both are unhealthy — an upload failure is the
more severe, already-notified condition.

The SAME rule covers a partial LOGO-BYTE gather (bead …-xb58a). Logo images live
in Dispatcharr, so the builder fetches them at gather time; when some of those
fetches fail the artifact is built and useful but carries fewer logo bytes than
the operator expects, and a restore will report those as logo misses. The
builder therefore adds ``"logos"`` to ``degraded_categories`` and reports the
COUNT in ``artifact.unarchived_logo_bytes``, which lands in
``details["unarchived_logo_bytes"]`` and the task message. Shipping that as a
clean success would be the same defect the logo-bytes bead exists to fix.

A channel whose ``epg_data_id`` points at a guide row that no longer exists is
DIFFERENT in kind (bead …-dfkbn, PR review W2). Its link cannot be restored, and
the count reaches ``details["unresolved_epg_links"]`` and the task message so the
operator can see it, but it does NOT set ``failed_count`` and does NOT add a
degraded category: a dangling FK is common, largely unactionable, and is not ECM
failing to gather something it could have. Making it a WARNING would erode the
badge that the two conditions above depend on.

Metrics: ``ecm_backup_runs_total{result="success"|"partial"|"skipped"|"failed"}``
and ``ecm_backup_upload_total{provider,result}`` (per-upload outcome). Gather
degradation does not get its own metric label — it is a data-completeness
signal on the local artifact, orthogonal to the upload-destination health this
metric tracks.

Concurrency: the engine's ``TaskScheduler.run()`` already rejects a second
concurrent run of the same ``task_id`` (ALREADY_RUNNING guard), so no extra
self-exclusion is needed here.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cloud_storage import SUPPORTED_PROVIDERS
from config import CONFIG_DIR
from services.notification_service import create_notification_internal
from task_registry import register_task
from task_scheduler import ScheduleConfig, ScheduleType, TaskResult, TaskScheduler

import journal
import observability
from dbas import retention
from routers.backup import (
    build_backup_artifact,
    verify_artifact_sha256,
)

logger = logging.getLogger(__name__)

BACKUPS_DIR = CONFIG_DIR / "backups"

# Provider types whose upload path is wired in v0.18.0 (bead 0i2vt.8). Dropbox
# and OneDrive are DEFERRED to a v0.18.x follow-up (PO decision 2026-06-17) —
# a configured target of a deferred provider is treated as a non-silent
# per-target failure rather than silently skipped. Single source of truth lives
# in cloud_storage (also gates the config-UI test surface — SEC-4).
_SUPPORTED_UPLOAD_PROVIDERS = SUPPORTED_PROVIDERS


def _bump_metric(result: str) -> None:
    """Increment ecm_backup_runs_total for a result label, best-effort."""
    try:
        observability.get_metric("backup_runs_total").labels(result=result).inc()
    except Exception as e:  # pragma: no cover — metrics best-effort
        logger.warning("[DBAS_BACKUP] Failed to increment backup_runs_total: %s", e)


def _bump_upload_metric(provider: str, result: str) -> None:
    """Increment ecm_backup_upload_total{provider,result}, best-effort."""
    try:
        observability.get_metric("backup_upload_total").labels(
            provider=provider, result=result
        ).inc()
    except Exception as e:  # pragma: no cover — metrics best-effort
        logger.warning("[DBAS_BACKUP] Failed to increment backup_upload_total: %s", e)


@register_task
class DbasBackupTask(TaskScheduler):
    """Build the new-format DBAS backup artifact on a schedule or on demand.

    Configuration options (stored in task config JSON):
    - cloud_target_id: Optional[int] — the CloudStorageTarget this schedule is
      bound to. None = local-only backup (freshness gate is a no-op).
    - cloud_credential_version: Optional[int] — the target's
      ``credential_version`` captured when the schedule was configured. Checked
      fresh against the DB at fire time.
    """

    task_id = "dbas_backup"
    task_name = "DBAS Backup"
    task_description = (
        "Build a redacted, sealed DBAS backup artifact (ZIP + SHA-256 sidecar) "
        "in /config/backups/. Scheduled or manual."
    )
    default_enabled = False

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(schedule_type=ScheduleType.MANUAL)
        super().__init__(schedule_config)

        self.cloud_target_id: Optional[int] = None
        self.cloud_credential_version: Optional[int] = None
        # Multi-destination upload list (bead 0i2vt.8). Each entry is
        # {"cloud_target_id": int, "cloud_credential_version": int}. When set,
        # the artifact is built and then uploaded to EACH configured+enabled
        # target with per-target freshness re-validation and tri-state run
        # semantics. Distinct from the legacy single cloud_target_id pre-build
        # gate above (which still aborts the whole run for backward-compat).
        self.cloud_targets: list[dict] = []

        # Retention policy (bead 0i2vt.9). Pruning runs ONLY after a
        # checksum-verified-successful new backup completes, per-destination, and
        # never below the last-N floor (NEVER-ZERO). Defaults per PO decision:
        # keep the newest 7, additionally drop beyond-N artifacts older than 30
        # days. ``retention_enabled=False`` is the safe default — an operator
        # opts in (a less-engaged operator never silently loses history).
        self.retention_enabled: bool = False
        self.retention_last_n: int = retention.DEFAULT_LAST_N
        self.retention_max_age_days: int = retention.DEFAULT_MAX_AGE_DAYS

        # Whole-artifact passphrase encryption (ADR-012 D12 / u81kh) — MANUAL-RUN
        # ONLY transients, passed as ad-hoc run parameters and DELIBERATELY
        # excluded from get_config so a passphrase is never persisted to the
        # schedule store (journal.db). A SCHEDULED run therefore always produces
        # the default redact-by-default backup: there is nowhere safe to keep a
        # passphrase at rest for an unattended run, so encrypted backups are
        # opt-in + interactive only.
        #
        # ONE-SHOT (bead …-cytzj). Excluding them from get_config keeps them out
        # of journal.db, but this task object is a LIVE SINGLETON that outlives
        # the run: without the unconditional reset in execute()'s finally, one
        # manual "Create Encrypted Backup" made EVERY later run in the same
        # process — including an unattended scheduled one — emit an encrypted,
        # credential-bearing artifact under that one-off passphrase, with no
        # signal to the operator and no way to notice short of a restart.
        self.passphrase: Optional[str] = None
        self.include_credentials: bool = False
        self.acknowledge_unrecoverable: bool = False

    def _reset_encryption_transients(self) -> None:
        """Return the manual-run encryption transients to their defaults.

        Called from execute()'s ``finally`` so it runs on EVERY exit path —
        success, build failure, an exception, and the freshness gate's early
        SKIP return — which is what makes "these apply to a single run" true.
        """
        self.passphrase = None
        self.include_credentials = False
        self.acknowledge_unrecoverable = False

    def get_config(self) -> dict:
        # Encryption transients (passphrase/include_credentials/
        # acknowledge_unrecoverable) are intentionally absent — they must never
        # be persisted; they apply to a single manual run only.
        return {
            "cloud_target_id": self.cloud_target_id,
            "cloud_credential_version": self.cloud_credential_version,
            "cloud_targets": list(self.cloud_targets),
            "retention_enabled": self.retention_enabled,
            "retention_last_n": self.retention_last_n,
            "retention_max_age_days": self.retention_max_age_days,
        }

    def update_config(self, config: dict) -> None:
        if "cloud_target_id" in config:
            val = config["cloud_target_id"]
            self.cloud_target_id = int(val) if val is not None else None
        if "cloud_credential_version" in config:
            val = config["cloud_credential_version"]
            self.cloud_credential_version = int(val) if val is not None else None
        if "cloud_targets" in config:
            raw = config["cloud_targets"] or []
            parsed: list[dict] = []
            for entry in raw:
                tid = entry.get("cloud_target_id")
                if tid is None:
                    continue
                parsed.append({
                    "cloud_target_id": int(tid),
                    "cloud_credential_version": (
                        int(entry["cloud_credential_version"])
                        if entry.get("cloud_credential_version") is not None
                        else None
                    ),
                })
            self.cloud_targets = parsed
        if "retention_enabled" in config:
            self.retention_enabled = bool(config["retention_enabled"])
        if "retention_last_n" in config:
            val = config["retention_last_n"]
            # Floor at 1 here too (defense in depth — select_prunable also
            # clamps): a misconfigured 0 must never erode history to zero.
            self.retention_last_n = max(int(val), 1) if val is not None else retention.DEFAULT_LAST_N
        if "retention_max_age_days" in config:
            val = config["retention_max_age_days"]
            self.retention_max_age_days = int(val) if val is not None else retention.DEFAULT_MAX_AGE_DAYS
        # Encryption transients (manual run only — never persisted, see get_config).
        if "passphrase" in config:
            val = config["passphrase"]
            self.passphrase = str(val) if val else None
        if "include_credentials" in config:
            self.include_credentials = bool(config["include_credentials"])
        if "acknowledge_unrecoverable" in config:
            self.acknowledge_unrecoverable = bool(config["acknowledge_unrecoverable"])

    async def execute(self) -> TaskResult:
        """Run one backup, then unconditionally disarm the encryption transients.

        The ``finally`` is the whole point (bead …-cytzj): the transients must
        not survive the run that supplied them, no matter how that run ended.
        """
        try:
            return await self._execute_run()
        finally:
            self._reset_encryption_transients()

    async def _execute_run(self) -> TaskResult:
        started_at = datetime.now(timezone.utc)
        self._set_progress(
            total=1, current=0, status="starting",
            current_item="Preparing DBAS backup...",
        )

        # Fire-time credential-freshness gate. Returns a SKIP TaskResult when
        # the bound target is no longer usable; None means "proceed".
        if self.cloud_target_id is not None:
            skip = await self._check_credential_freshness(started_at)
            if skip is not None:
                return skip

        try:
            self._set_progress(
                current_item="Building backup artifact...", status="running",
            )
            artifact = await build_backup_artifact(
                dest_dir=BACKUPS_DIR,
                passphrase=self.passphrase,
                include_credentials=self.include_credentials,
                acknowledge_unrecoverable=self.acknowledge_unrecoverable,
            )

            # The artifact already carries its CANONICAL, timestamped,
            # allowlist-matching name (ecm-backup-<ts>.zip): the builder owns it
            # directly (bead e0r3h) — there is no post-build rename. The retention
            # canonical-sort + _BACKUP_ZIP_FILENAME_RE allowlist both match the
            # builder-produced shape.
            filename = artifact.zip_path.name
            logger.info(
                "[DBAS_BACKUP] Built artifact %s "
                "(schema_version=%d, %d members, sha256=%s)",
                filename, artifact.schema_version, artifact.file_count,
                artifact.sha256,
            )

            # NEVER-ZERO precondition: the LOCAL artifact is "verified-successful"
            # only when its bytes match the SHA-256 sidecar (a checksum-verified
            # success, NOT merely 'the coroutine returned'). Retention keys off
            # THIS flag — a corrupt local write prunes nothing.
            local_verified = verify_artifact_sha256(
                artifact.zip_path, artifact.sidecar_path
            )
            if not local_verified:
                logger.warning(
                    "[DBAS_BACKUP] Local artifact %s failed checksum verification; "
                    "retention will NOT prune (never-zero).", filename,
                )

            # Cloud-upload step (bead 0i2vt.8). The local artifact is the core
            # value (host-failure survival starts with a redacted on-disk
            # backup); uploads are layered on top. When cloud_targets are
            # configured the run result is the per-target tri-state.
            upload_summary = await self._upload_to_targets(artifact)

            run_result = upload_summary["run_result"]  # success|partial|failed
            _bump_metric(run_result)

            # Retention (bead 0i2vt.9) — PER-DESTINATION, post-verified-success.
            # LOCAL prune is gated on the local checksum verification above;
            # cloud per-destination prune happens inside _upload_one_target right
            # after each verified-successful upload. Both honor the last-N floor
            # + age threshold and NEVER prune below the floor (never-zero).
            if self.retention_enabled:
                try:
                    retention.prune_local_backups(
                        verified_success=local_verified,
                        last_n=self.retention_last_n,
                        max_age_days=self.retention_max_age_days,
                        backups_dir=BACKUPS_DIR,
                    )
                except Exception as e:  # retention must never fail the backup run
                    logger.warning("[DBAS_BACKUP] Local retention prune failed: %s", e)

            self._set_progress(current=1, total=1, status="completed")

            # zt3kf — a category whose Dispatcharr gather stubbed to
            # {"_warning": ...} instead of real data (upstream fetch failure,
            # isolated per-category by routers.backup._gather_dispatcharr_sections)
            # makes this artifact DEGRADED: it was built and is on disk, but is
            # missing real data for the named category/categories. Never silent —
            # WARN log + named in details/message so the operator does not have
            # to open the artifact to discover what is missing.
            degraded_categories = list(artifact.degraded_categories or [])
            if degraded_categories:
                logger.warning(
                    "[DBAS_BACKUP] Backup artifact %s built with %d degraded "
                    "Dispatcharr categor%s (Dispatcharr fetch failed): %s",
                    filename, len(degraded_categories),
                    "y" if len(degraded_categories) == 1 else "ies",
                    ", ".join(degraded_categories),
                )

            details = {
                "filename": filename,
                "schema_version": artifact.schema_version,
                "sha256": artifact.sha256,
                "file_count": artifact.file_count,
            }
            if degraded_categories:
                details["degraded_categories"] = degraded_categories
            # bead …-xb58a: a backup that could not fetch some Dispatcharr-hosted
            # logo bytes already sets the "logos" degraded flag above; this
            # carries the COUNT so the operator sees how much is missing, not
            # just that something is.
            unarchived_logos = getattr(artifact, "unarchived_logo_bytes", 0) or 0
            if unarchived_logos:
                details["unarchived_logo_bytes"] = unarchived_logos
            # bead …-dfkbn (PR review W2): channels whose EPG link could not be
            # resolved to its guide natural key. Those links cannot come back on
            # restore, so the operator should be able to SEE the number here
            # rather than discover it in a restore report. Deliberately does NOT
            # set failed_count: a dangling epg_data_id is common and largely
            # unactionable, and turning every one of them into a "Completed with
            # Warnings" run would train the operator to ignore that badge.
            unresolved_epg_links = getattr(artifact, "unresolved_epg_links", 0) or 0
            epg_index_truncated = bool(getattr(artifact, "epg_index_truncated", False))
            if unresolved_epg_links:
                details["unresolved_epg_links"] = unresolved_epg_links
                if epg_index_truncated:
                    # A different diagnosis with a different remedy: the guide
                    # read hit its ceiling, so some of those may be good links
                    # the read never saw. Never conflate the two.
                    details["epg_index_truncated"] = True
            if upload_summary["attempted"]:
                details["upload"] = {
                    "run_result": run_result,
                    "succeeded": upload_summary["succeeded"],
                    "failed": upload_summary["failed"],
                    "results": upload_summary["results"],
                }

            message = "Built DBAS backup %s (schema v%d, %d files)" % (
                filename, artifact.schema_version, artifact.file_count,
            )
            if degraded_categories:
                message += "; %d Dispatcharr categor%s degraded: %s" % (
                    len(degraded_categories),
                    "y" if len(degraded_categories) == 1 else "ies",
                    ", ".join(degraded_categories),
                )
            if unarchived_logos:
                message += "; %d logo(s) archived without their image bytes" % (
                    unarchived_logos,
                )
            if unresolved_epg_links:
                if epg_index_truncated:
                    message += (
                        "; %d channel EPG link(s) unresolved, but the guide read "
                        "hit its row ceiling so the index may be incomplete"
                        % unresolved_epg_links
                    )
                else:
                    message += (
                        "; %d channel EPG link(s) point at a guide entry that no "
                        "longer exists and will not be restored"
                        % unresolved_epg_links
                    )
            if upload_summary["attempted"]:
                message += "; cloud upload: %s (%d ok, %d failed)" % (
                    run_result, upload_summary["succeeded"], upload_summary["failed"],
                )

            # PO decision: a backup run whose configured uploads did NOT all
            # succeed is NOT reported as a clean success — partial/failed
            # reconcile against the scheduled-job health metric. Cloud-upload
            # health takes precedence over gather-completeness when both are
            # unhealthy (an upload failure is the more severe condition and
            # already drives its own dedicated notification via
            # _notify_upload_outcome); gather degradation alone — uploads fine
            # or not configured — is WARNING-level (success stays True,
            # failed_count>0), which is what makes task_engine.py's existing
            # "Completed with Warnings" branch reachable instead of a silent
            # clean success (zt3kf; mirrors the tyei5/dbas_restore counts-wiring
            # pattern — real counts, no new notification path).
            success = (run_result == "success")
            if success:
                success_count = 0 if degraded_categories else 1
                failed_count = 1 if degraded_categories else 0
            else:
                success_count = 0
                failed_count = 1

            return TaskResult(
                success=success,
                message=message,
                error=None if run_result == "success" else "CLOUD_UPLOAD_%s" % run_result.upper(),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                total_items=1,
                success_count=success_count,
                failed_count=failed_count,
                details=details,
            )
        except Exception as e:
            logger.exception("[DBAS_BACKUP] Backup build failed: %s", e)
            _bump_metric("failed")
            return TaskResult(
                success=False,
                message="DBAS backup build failed: %s" % str(e),
                error=str(e),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
                failed_count=1,
            )

    async def _check_credential_freshness(
        self, started_at: datetime
    ) -> Optional[TaskResult]:
        """Re-read the bound CloudStorageTarget FRESH and abort if stale.

        Returns a SKIP ``TaskResult`` (and emits WARN + journal + notification
        + metric) when the target is missing/disabled/revoked/rotated, or
        ``None`` when the run may proceed.
        """
        from database import get_session
        from export_models import CloudStorageTarget

        reason: Optional[str] = None
        session = get_session()
        try:
            target = (
                session.query(CloudStorageTarget)
                .filter(CloudStorageTarget.id == self.cloud_target_id)
                .first()
            )
            if target is None:
                reason = "target %s no longer exists" % self.cloud_target_id
            elif not target.enabled:
                reason = "target '%s' (id=%s) is disabled" % (
                    target.name, target.id,
                )
            elif target.token_revoked_at is not None:
                reason = "credentials for target '%s' (id=%s) were revoked" % (
                    target.name, target.id,
                )
            elif target.credential_version != self.cloud_credential_version:
                reason = (
                    "credentials for target '%s' (id=%s) were rotated "
                    "(configured v%s, current v%s)" % (
                        target.name, target.id,
                        self.cloud_credential_version, target.credential_version,
                    )
                )
        finally:
            session.close()

        if reason is None:
            return None

        return await self._abort_skip(started_at, reason)

    async def _abort_skip(
        self, started_at: datetime, reason: str
    ) -> TaskResult:
        """Emit a NON-SILENT skip (WARN + journal + notification + metric)
        and return a failed TaskResult. A scheduled backup that silently
        stops = false safety."""
        message = (
            "DBAS backup skipped — %s. No backup was produced. Review the "
            "cloud target configuration or update the backup schedule." % reason
        )
        logger.warning("[DBAS_BACKUP] %s", message)

        _bump_metric("skipped")

        # Journal entry (audit trail).
        try:
            journal.log_entry(
                category="backup",
                action_type="scheduled_backup_skipped",
                entity_name="DBAS Backup",
                description=message,
                user_initiated=False,
            )
        except Exception as e:  # pragma: no cover — journal best-effort
            logger.warning("[DBAS_BACKUP] Failed to journal skip: %s", e)

        # NotificationCenter notification (operator-visible).
        try:
            await create_notification_internal(
                notification_type="warning",
                title="DBAS Backup: Skipped",
                message=message,
                source="task_dbas_backup",
                source_id="credential_freshness",
                send_alerts=True,
            )
        except Exception as e:  # pragma: no cover — notification best-effort
            logger.warning("[DBAS_BACKUP] Failed to emit skip notification: %s", e)

        self._set_progress(current=0, total=1, status="failed", skipped_count=1)
        return TaskResult(
            success=False,
            message=message,
            error="CREDENTIAL_FRESHNESS_ABORT",
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            total_items=1,
            skipped_count=1,
            details={"skipped": True, "reason": reason},
        )

    # ------------------------------------------------------------------
    # Cloud upload (bead 0i2vt.8) — per-target tri-state, SSRF-chokepointed,
    # executor-wrapped, streamed, credential-masked.
    # ------------------------------------------------------------------
    async def _upload_to_targets(self, artifact) -> dict:
        """Upload the sealed artifact (+ .sha256 sidecar) to each configured
        enabled target and compute the tri-state run result.

        PO decision (2026-06-17): run is 'success' only if ALL configured+
        enabled targets succeed, 'partial' if some, 'failed' if none. Both
        'partial' and 'failed' fire a NotificationCenter notification and
        reconcile against the scheduled-job health metric.

        Concurrency: targets are uploaded ONE AT A TIME (sequential loop). This
        is the simplest correct cap=1 serialization — it bounds peak memory and
        socket pressure regardless of MAX_CONCURRENT_TASKS, since two large
        artifact uploads never run in parallel within a single backup run.

        Per-target freshness re-validation (reconciled with .6's single-target
        gate): EACH configured target is re-read FRESH from the DB right before
        uploading to it and validated (enabled + credential_version match +
        token_revoked_at None). A stale/revoked target is a non-silent failure
        for THAT target (WARN + journal + notification), not a silent drop.
        """
        summary = {
            "attempted": False,
            "succeeded": 0,
            "failed": 0,
            "results": [],
            "run_result": "success",
        }
        if not self.cloud_targets:
            return summary  # local-only backup — run_result stays 'success'

        summary["attempted"] = True
        total = len(self.cloud_targets)
        # Track counts in plain int locals — NOT by reading them back out of the
        # `summary` dict. `summary["results"]` holds per-target `reason` strings
        # that can carry masked credential-derived detail, so CodeQL treats the
        # whole dict (and every subscript of it) as tainted. Keeping the counts
        # and the derived status word as standalone locals means the values that
        # reach the notification emitter never share a taint lineage with the
        # per-target reason strings — the emitted data is provably just a status
        # word and two integers.
        succeeded = 0
        failed = 0
        for idx, entry in enumerate(self.cloud_targets, start=1):
            self._set_progress(
                current_item="Uploading to cloud target %d/%d..." % (idx, total),
                status="running",
            )
            tid = entry["cloud_target_id"]
            cfg_version = entry.get("cloud_credential_version")
            outcome = await self._upload_one_target(artifact, tid, cfg_version)
            summary["results"].append(outcome)
            if outcome["success"]:
                succeeded += 1
            else:
                failed += 1

        if succeeded == total:
            run_result = "success"
        elif succeeded == 0:
            run_result = "failed"
        else:
            run_result = "partial"

        summary["succeeded"] = succeeded
        summary["failed"] = failed
        summary["run_result"] = run_result

        if run_result != "success":
            await self._notify_upload_outcome(run_result, succeeded, total)

        return summary

    async def _upload_one_target(
        self, artifact, target_id: int, cfg_version: Optional[int]
    ) -> dict:
        """Re-validate one target FRESH, then upload artifact + sidecar to it.

        Returns a per-target outcome dict. Never raises — an upload failure is
        captured as success=False so the tri-state can be computed across all
        targets.
        """
        from cloud_storage import get_adapter
        from cloud_storage.crypto import decrypt_credentials
        from cloud_storage.upload_security import mask_secrets
        from database import get_session
        from export_models import CloudStorageTarget

        # --- fresh re-read + freshness gate for THIS target -----------------
        session = get_session()
        try:
            target = (
                session.query(CloudStorageTarget)
                .filter(CloudStorageTarget.id == target_id)
                .first()
            )
            stale_reason = self._freshness_reason(target, target_id, cfg_version)
            if stale_reason is not None:
                await self._fail_target(target_id, None, stale_reason, provider=None)
                return {"target_id": target_id, "success": False, "reason": stale_reason}

            provider = target.provider_type
            target_name = target.name
            insecure = bool(target.insecure)
            upload_path = target.upload_path or "/"
            encrypted = target.credentials
        finally:
            session.close()

        if provider not in _SUPPORTED_UPLOAD_PROVIDERS:
            reason = (
                "provider '%s' upload is not supported in this release "
                "(deferred to a v0.18.x follow-up)" % provider
            )
            await self._fail_target(target_id, target_name, reason, provider=provider)
            return {"target_id": target_id, "success": False, "reason": reason}

        creds = decrypt_credentials(encrypted)
        if creds is None:
            reason = "credentials for target '%s' could not be decrypted" % target_name
            await self._fail_target(target_id, target_name, reason, provider=provider)
            _bump_upload_metric(provider, "failed")
            return {"target_id": target_id, "success": False, "reason": reason}

        # Per-target TLS posture: thread the insecure flag into the adapter and
        # write the mandatory insecure audit row BEFORE the request.
        creds = dict(creds)
        creds["insecure"] = insecure
        if insecure:
            self._audit_insecure(target_id, target_name, provider)

        zip_remote, sidecar_remote = self._remote_paths(upload_path, artifact)

        # --- upload artifact + sidecar (each through the adapter) -----------
        try:
            adapter = get_adapter(provider, creds)
            zip_res = await adapter.upload(Path(artifact.zip_path), zip_remote)
            sidecar_res = await adapter.upload(Path(artifact.sidecar_path), sidecar_remote)
        except Exception as e:  # adapter construction / unexpected error
            # SEC-2: mask credential-shaped material before the raw exception
            # text reaches the journal / notification. This is the one
            # credential-adjacent surface in the task that bypasses the
            # adapter-level masking (adapter-returned `error` fields are already
            # masked at source), so mask it here.
            reason = "upload to target '%s' raised: %s" % (
                target_name, mask_secrets(str(e)),
            )
            await self._fail_target(target_id, target_name, reason, provider=provider)
            _bump_upload_metric(provider, "failed")
            return {"target_id": target_id, "success": False, "reason": reason}

        ok = bool(zip_res.success and sidecar_res.success)
        # Per-destination audit row on EVERY outbound upload (Security #3/#8).
        self._audit_upload(
            target_id, target_name, provider, ok,
            zip_res, sidecar_res, insecure,
        )
        _bump_upload_metric(provider, "success" if ok else "failed")

        if not ok:
            err = zip_res.error or sidecar_res.error or "unknown upload error"
            reason = "upload to target '%s' failed: %s" % (target_name, err)
            await self._fail_target(target_id, target_name, reason, provider=provider)
            return {"target_id": target_id, "success": False, "reason": reason}

        # Per-destination retention (bead 0i2vt.9). Prune THIS destination's old
        # artifacts ONLY after THIS verified-successful upload (per-destination
        # never-prune-on-failure — we are on the success path here). Routes
        # through the adapter's list/delete (the .5 SSRF chokepoint +
        # executor-wrap). Retention failures never fail the upload.
        if self.retention_enabled:
            try:
                await retention.prune_cloud_destination(
                    adapter=adapter,
                    remote_dir=upload_path,
                    dest_name=target_name,
                    verified_success=True,
                    last_n=self.retention_last_n,
                    max_age_days=self.retention_max_age_days,
                )
            except Exception as e:  # retention must never fail the run
                logger.warning(
                    "[DBAS_BACKUP] Cloud retention prune for target id=%s failed: %s",
                    target_id, e,
                )

        return {
            "target_id": target_id,
            "success": True,
            "provider": provider,
            "remote_url": zip_res.remote_url,
        }

    @staticmethod
    def _freshness_reason(target, target_id, cfg_version) -> Optional[str]:
        """Per-target freshness check (mirrors the single-target .6 gate)."""
        if target is None:
            return "target %s no longer exists" % target_id
        if not target.enabled:
            return "target '%s' (id=%s) is disabled" % (target.name, target.id)
        if target.token_revoked_at is not None:
            return "credentials for target '%s' (id=%s) were revoked" % (
                target.name, target.id,
            )
        if cfg_version is not None and target.credential_version != cfg_version:
            return (
                "credentials for target '%s' (id=%s) were rotated "
                "(configured v%s, current v%s)" % (
                    target.name, target.id, cfg_version, target.credential_version,
                )
            )
        return None

    @staticmethod
    def _remote_paths(upload_path: str, artifact) -> tuple[str, str]:
        prefix = (upload_path or "/").strip("/")
        zip_name = Path(artifact.zip_path).name
        sidecar_name = Path(artifact.sidecar_path).name
        if prefix:
            return "%s/%s" % (prefix, zip_name), "%s/%s" % (prefix, sidecar_name)
        return zip_name, sidecar_name

    def _audit_upload(
        self, target_id, target_name, provider, ok, zip_res, sidecar_res, insecure
    ) -> None:
        """Write a per-destination audit row for an upload attempt."""
        try:
            journal.log_entry(
                category="backup_outbound",
                action_type="cloud_upload" if ok else "cloud_upload_failed",
                entity_name=target_name or ("target %s" % target_id),
                entity_id=target_id,
                description=(
                    "DBAS backup upload to %s target '%s' (id=%s): %s "
                    "(zip=%s, sidecar=%s, tls_verified=%s)" % (
                        provider, target_name, target_id,
                        "success" if ok else "failed",
                        zip_res.success, sidecar_res.success, not insecure,
                    )
                ),
                user_initiated=False,
            )
        except Exception as e:  # pragma: no cover — journal best-effort
            logger.warning("[DBAS_BACKUP] Failed to journal upload audit: %s", e)

    def _audit_insecure(self, target_id, target_name, provider) -> None:
        """Explicit audit row recording that TLS verification was skipped."""
        try:
            journal.log_entry(
                category="backup_outbound",
                action_type="cloud_upload_insecure_tls",
                entity_name=target_name or ("target %s" % target_id),
                entity_id=target_id,
                description=(
                    "TLS verification SKIPPED (insecure=true) for %s target "
                    "'%s' (id=%s) on a DBAS backup upload" % (
                        provider, target_name, target_id,
                    )
                ),
                user_initiated=False,
            )
        except Exception as e:  # pragma: no cover — journal best-effort
            logger.warning("[DBAS_BACKUP] Failed to journal insecure audit: %s", e)

    async def _fail_target(
        self, target_id, target_name, reason: str, provider: Optional[str]
    ) -> None:
        """Non-silent per-target failure: WARN + journal + notification."""
        msg = "DBAS backup upload to target id=%s failed — %s" % (target_id, reason)
        # The logger receives ONLY the safe target id, never `reason`/`msg`:
        # `reason` can carry a masked-but-credential-derived adapter error or a
        # masked SDK-exception string, and CodeQL traces that value into any
        # logging sink (it does not recognise mask_secrets() as a sanitizer).
        # The masked detail still reaches the operator via the journal
        # description and the notification message below (non-logging sinks
        # that are stored/displayed, not logged).
        logger.warning("[DBAS_BACKUP] Upload to target id=%s failed (see journal/notification for detail)", target_id)
        try:
            journal.log_entry(
                category="backup_outbound",
                action_type="cloud_upload_failed",
                entity_name=target_name or ("target %s" % target_id),
                entity_id=target_id,
                description=msg,
                user_initiated=False,
            )
        except Exception as e:  # pragma: no cover
            logger.warning("[DBAS_BACKUP] Failed to journal target failure: %s", e)
        try:
            await create_notification_internal(
                notification_type="warning",
                title="DBAS Backup: Upload Failed",
                message=msg,
                source="task_dbas_backup",
                source_id="cloud_upload_%s" % target_id,
                send_alerts=True,
            )
        except Exception as e:  # pragma: no cover
            logger.warning("[DBAS_BACKUP] Failed to emit target-failure notification: %s", e)

    async def _notify_upload_outcome(
        self, run_result: str, succeeded: int, total: int
    ) -> None:
        """Fire the run-level partial/failed notification (PO decision).

        Accepts ONLY clean scalars — the run-result status word (``success`` /
        ``partial`` / ``failed``) and two integer counts. It deliberately does
        NOT take the ``summary`` dict: ``summary["results"]`` holds per-target
        ``reason`` strings that can carry masked credential-derived detail, and
        CodeQL treats the whole dict (and every subscript of it) as tainted.
        Receiving the status word + counts as plain typed arguments cuts that
        taint lineage at the source, so nothing sensitive can flow into the
        notification message/title or the downstream alert logging sinks.
        """
        msg = (
            "DBAS backup cloud upload %s: %d of %d configured targets succeeded. "
            "The local artifact was written; review the cloud target "
            "configuration." % (run_result, succeeded, total)
        )
        # Log only the safe run-level counts (no per-target reason strings flow
        # here); the full message goes to the notification sink below.
        logger.warning(
            "[DBAS_BACKUP] Cloud upload %s: %d of %d targets succeeded",
            run_result, succeeded, total,
        )
        try:
            await create_notification_internal(
                notification_type="error" if run_result == "failed" else "warning",
                title="DBAS Backup: Upload %s" % run_result.capitalize(),
                message=msg,
                source="task_dbas_backup",
                source_id="cloud_upload_run",
                send_alerts=True,
            )
        except Exception as e:  # pragma: no cover
            logger.warning("[DBAS_BACKUP] Failed to emit run-outcome notification: %s", e)
