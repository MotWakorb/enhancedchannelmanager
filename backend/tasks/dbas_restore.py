"""DBAS Restore Task (bead ``enhancedchannelmanager-o8tbv``).

The async, progress-emitting restore that makes a new-format DBAS artifact
user-triggerable and observable — the missing integration piece for the
v0.18.0 full round-trip. Mirrors :class:`tasks.dbas_backup.DbasBackupTask`: a
``TaskScheduler`` subclass whose ``execute()`` runs the whole restore pipeline
and emits ``_set_progress`` at each of the 13 canonical stages
(``frontend/src/utils/restoreStages.ts``) so the frontend can poll
``/api/tasks/{id}`` and render "Stage N of M".

Pipeline (validate -> decode -> dry-run-default -> orchestrate)
---------------------------------------------------------------
1. Open the streamed-to-disk artifact (the restore endpoint streamed the upload
   to a temp file on the CONFIG partition; this task never sees the raw upload).
2. ``routers.backup.validate_artifact_manifest`` — the .17 gate (version +
   per-member SHA-256) runs BEFORE any decode or mutation. A refusal returns a
   failed ``TaskResult`` with ZERO importer ever called.
3. ``dbas.restore_artifact.decode_artifact_to_plan`` — turn the validated ZIP
   into the orchestrator's :class:`~dbas.preflight.ImportPlan` (per-category
   records + the binary logo subtree decoded into .15 logo records).
4. Run the orchestrator. **Dry-run is default-ON** (.16): the task calls
   :func:`dbas.restore_orchestrator.run_dry_run` UNLESS the task config carries
   ``confirm_apply=True``, in which case it calls ``run_restore(...,
   confirm_apply=True)`` with the apply registry. The orchestrator's own
   guardrail is the final, unbypassable enforcement — even a misconfigured task
   degrades to a counts-only dry-run rather than mutating.
5. Return the :class:`~dbas.restore_contracts.RestoreReport` in the
   ``TaskResult.details`` so the frontend's ``.20`` summary can render it.

Temp-artifact cleanup
---------------------
The task deletes the temp artifact on BOTH success and failure (the endpoint
owns creation; the task owns teardown so the file does not outlive the run).
``cleanup_artifact`` defaults to True; the restore endpoint sets it.

Security (untrusted upload — security-review surface)
-----------------------------------------------------
Validate-before-mutate is structural: ``validate_artifact_manifest`` runs before
``decode_artifact_to_plan``, which runs before the orchestrator. The decode
itself never extracts to disk (zip-slip safe — see ``dbas.restore_artifact``).
Logging is credential-clean: only safe fields (filename, schema_version, counts,
outcome) are logged; never an artifact path that could embed a token, never an
upstream SDK body. The temp artifact is the endpoint's concern for mode/cleanup.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from task_registry import register_task
from task_scheduler import ScheduleConfig, ScheduleType, TaskResult, TaskScheduler

logger = logging.getLogger(__name__)


# The canonical stage keys, in order, aligned 1:1 with the keys in
# ``frontend/src/utils/restoreStages.ts`` (RESTORE_STAGES). The frontend maps a
# ``current_item`` string back to "Stage N of M" by these keys, so they MUST
# match. ``preflight`` is the entry stage; ``finalize`` is the terminal stage.
_STAGE_KEYS: tuple[str, ...] = (
    "preflight",
    "m3u_account",
    "epg_source",
    "channel_group",
    "channel_profile",
    "stream_profile",
    "user_agent",
    "user",
    "channel",
    "stream",
    "logo",
    "deferred",
    "finalize",
)
_TOTAL_STAGES = len(_STAGE_KEYS)


@register_task
class DbasRestoreTask(TaskScheduler):
    """Run a new-format DBAS artifact restore as an async, progress-emitting task.

    Configuration options (stored on the instance via ``update_config`` — the
    restore endpoint passes them as ad-hoc run parameters):

    - ``artifact_path``: str — the temp artifact the endpoint streamed the upload
      to. Required; an absent/empty path is a hard failure (nothing to restore).
    - ``confirm_apply``: bool — ``False`` (default) runs a counts-only DRY-RUN;
      ``True`` runs the destructive APPLY through the orchestrator's apply
      registry. The orchestrator re-enforces this (.16 guardrail), so a stale
      ``True`` against an unsupported plan still degrades safely to a dry-run.
    - ``cleanup_artifact``: bool — delete the temp artifact on completion
      (default ``True``).
    """

    task_id = "dbas_restore"
    task_name = "DBAS Restore"
    task_description = (
        "Restore a new-format DBAS backup artifact (validate -> decode -> "
        "dry-run by default; apply only when confirmed). Emits per-stage progress."
    )
    default_enabled = False
    # Per-invocation state, not durable settings: artifact_path points at a
    # temp upload deleted after the run, and confirm_apply arms a DESTRUCTIVE
    # apply. The fail-safe direction on restart is disarmed/cleared, so the
    # registry must neither persist nor rehydrate this surface (gjb01).
    persist_config = False

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        if schedule_config is None:
            schedule_config = ScheduleConfig(schedule_type=ScheduleType.MANUAL)
        super().__init__(schedule_config)

        self.artifact_path: Optional[str] = None
        self.confirm_apply: bool = False
        self.cleanup_artifact: bool = True
        # Operator passphrase for an encrypted artifact (ADR-012 D12 / u81kh).
        # None for a plain artifact. NEVER logged or echoed in any TaskResult.
        self.passphrase: Optional[str] = None

    def get_config(self) -> dict:
        # The passphrase is DELIBERATELY excluded from get_config so it is never
        # persisted to the schedule store, surfaced in the task list, or logged.
        return {
            "artifact_path": self.artifact_path,
            "confirm_apply": self.confirm_apply,
            "cleanup_artifact": self.cleanup_artifact,
        }

    def update_config(self, config: dict) -> None:
        if "artifact_path" in config:
            val = config["artifact_path"]
            self.artifact_path = str(val) if val is not None else None
        if "confirm_apply" in config:
            self.confirm_apply = bool(config["confirm_apply"])
        if "cleanup_artifact" in config:
            self.cleanup_artifact = bool(config["cleanup_artifact"])
        if "passphrase" in config:
            val = config["passphrase"]
            self.passphrase = str(val) if val else None

    # -- progress helpers ----------------------------------------------------

    def _stage(self, key: str, *, success: int = 0, failed: int = 0, skipped: int = 0) -> None:
        """Advance progress to a named stage (current_item = the restoreStages key)."""
        idx = _STAGE_KEYS.index(key)
        self._set_progress(
            total=_TOTAL_STAGES,
            current=idx + 1,
            status="running",
            current_item=key,
            success_count=success,
            failed_count=failed,
            skipped_count=skipped,
        )

    async def execute(self) -> TaskResult:
        started_at = datetime.now(timezone.utc)
        self._set_progress(
            total=_TOTAL_STAGES, current=0, status="starting",
            current_item="preflight",
        )

        artifact = self.artifact_path
        if not artifact:
            return self._fail(started_at, "No artifact_path configured for restore")

        artifact_path = Path(artifact)

        # --- Decrypt-at-ingest gate (ADR-012 D12 / u81kh). -----------------
        # The SINGLE place an encrypted artifact is decrypted, BEFORE any ZIP is
        # opened or any importer runs. A plain artifact passes straight through.
        # On any gate failure NOTHING downstream runs (validate-before-mutate).
        decrypted_path: Optional[Path] = None
        try:
            effective_path, decrypted_path, gate_failure = await self._decrypt_gate(
                started_at, artifact_path
            )
            if gate_failure is not None:
                return gate_failure
            return await self._run_restore(started_at, effective_path)
        finally:
            self._cleanup(artifact_path)
            if decrypted_path is not None:
                self._cleanup_decrypted(decrypted_path)

    async def _decrypt_gate(self, started_at, artifact_path):
        """Resolve the effective (possibly-decrypted) artifact path.

        Returns ``(effective_path, decrypted_temp, failure_result)``:

        * A PLAIN artifact -> ``(artifact_path, None, None)`` (no-op pass-through).
        * An ENCRYPTED artifact -> decrypt to a sibling temp off the event loop
          and return ``(temp, temp, None)``. The envelope ``format_version`` is
          version-gated PRE-decrypt (no passphrase needed); a missing passphrase
          or any decrypt failure returns a sanitized ``failure_result`` and NO
          decryption output survives (the primitive releases zero plaintext).

        Decrypt happens exactly ONCE here; the per-category importers (.10-.15)
        need zero crypto awareness (spike 0zrse: a single ingest gate, not an
        11-bead fan-out).
        """
        from dbas import artifact_crypto

        if not artifact_path.exists():
            return artifact_path, None, self._fail(started_at, "Restore artifact is missing")

        if not artifact_crypto.is_encrypted_artifact(artifact_path):
            return artifact_path, None, None  # plain artifact — unchanged path

        # Pre-decrypt envelope version gate (checklist 30): refuse an unsupported
        # format_version WITHOUT a passphrase. Sanitized user message; detail in
        # the server log only (mirrors the .17 schema_version gate / S4).
        try:
            artifact_crypto.read_header(artifact_path)
        except artifact_crypto.UnsupportedArtifactFormatError as exc:
            logger.warning("[DBAS-RESTORE] Encrypted artifact rejected pre-decrypt: %s", exc)
            return artifact_path, None, self._fail(started_at, "Unsupported backup version")

        if not self.passphrase:
            return artifact_path, None, self._fail(
                started_at, "This backup is encrypted; a passphrase is required to restore it"
            )

        self._set_progress(
            total=_TOTAL_STAGES, current=0, status="running", current_item="preflight",
        )
        fd, tmp_name = tempfile.mkstemp(
            prefix="ecm-restore-dec-", suffix=".zip", dir=str(artifact_path.parent)
        )
        os.close(fd)
        decrypted = Path(tmp_name)
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                artifact_crypto.decrypt_file,
                artifact_path, self.passphrase, decrypted,
            )
        except artifact_crypto.ArtifactDecryptError:
            # Wrong passphrase or corrupted artifact — IDENTICAL sanitized
            # failure (no oracle); the primitive already released zero plaintext.
            self._cleanup_decrypted(decrypted)
            return artifact_path, None, self._fail(
                started_at,
                "Could not decrypt backup: wrong passphrase or corrupted artifact",
            )
        return decrypted, decrypted, None

    async def _run_restore(self, started_at: datetime, artifact_path: Path) -> TaskResult:
        # Local imports keep the heavy backup router / orchestrator off this
        # module's import-time path (mirrors DbasBackupTask's deferred imports).
        from dispatcharr_client import get_client
        from routers.backup import validate_artifact_manifest
        from dbas.restore_artifact import decode_artifact_to_plan
        from dbas.restore_orchestrator import run_dry_run, run_restore, new_restore_id
        from dbas.restore_contracts import IdRemapTable, RestoreReport, RollbackLedger
        from dbas.restore_orchestrator import default_importer_steps

        from fastapi import HTTPException

        if not artifact_path.exists():
            return self._fail(started_at, "Restore artifact is missing")

        # --- Stage 0: pre-flight (validate-before-mutate). -----------------
        # validate_artifact_manifest is the .17 gate: version + per-member
        # SHA-256, BEFORE any decode or importer. A refusal here means ZERO
        # mutation ever happened.
        self._stage("preflight")
        try:
            with zipfile.ZipFile(artifact_path, "r") as zf:
                validate_artifact_manifest(zf)   # .17 — raises HTTPException(400) on refusal
                plan = decode_artifact_to_plan(zf)  # decode the validated ZIP
        except zipfile.BadZipFile:
            return self._fail(started_at, "Uploaded artifact is not a valid archive")
        except HTTPException as exc:
            # .17 refused (version/integrity). NO importer ran. Surface the
            # sanitized message; never leak schema internals.
            return self._fail(started_at, str(exc.detail))

        is_apply = bool(self.confirm_apply)
        client = get_client()

        try:
            if is_apply:
                report = await run_restore(
                    plan=plan,
                    client=client,
                    steps=default_importer_steps(),
                    report=RestoreReport(is_dry_run=False),
                    ledger=RollbackLedger(restore_id=new_restore_id()),
                    remap=plan.existing_remap or IdRemapTable(),
                    confirm_apply=True,
                )
            else:
                report = await run_dry_run(plan=plan, client=client)
        except Exception as exc:  # noqa: BLE001 - any orchestration error is a sanitized failure
            logger.exception("[DBAS-RESTORE] Restore orchestration failed: %s", exc)
            return self._fail(started_at, "Restore failed during orchestration")

        # --- Per-category stages: emit one tick per restoreStages key with the
        #     category's realized (apply) or planned (dry-run) counts. ---------
        self._emit_category_stages(report)

        # --- Deferred + finalize. ------------------------------------------
        self._stage("deferred")
        self._set_progress(current=_TOTAL_STAGES, status="completed", current_item="finalize")

        outcome = report.outcome.value if report.outcome else "dry_run"
        mode = "apply" if is_apply else "dry-run"
        logger.info(
            "[DBAS-RESTORE] Restore task complete (mode=%s, outcome=%s, %d categories)",
            mode, outcome, len(report.categories),
        )
        return TaskResult(
            success=self._report_succeeded(report, is_apply),
            message=self._summary_message(report, is_apply),
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            total_items=_TOTAL_STAGES,
            success_count=_TOTAL_STAGES,
            details={
                "is_dry_run": report.is_dry_run,
                "outcome": outcome,
                "restore_report": report.model_dump(mode="json"),
            },
        )

    # -- stage emission ------------------------------------------------------

    def _emit_category_stages(self, report) -> None:
        """Emit a ``_set_progress`` tick for each per-category restoreStages key.

        Each category's counts come straight from the orchestrator's report (the
        same counts the .20 summary renders), so the per-stage progress is a true
        reflection of what happened, not a parallel counter.
        """
        # entity_type.value already equals the restoreStages key for every
        # category EntityType (m3u_account, epg_source, …, channel, logo).
        by_type = {cat.entity_type.value: cat for cat in report.categories}
        for key in _STAGE_KEYS:
            if key in ("preflight", "deferred", "finalize"):
                continue
            cat = by_type.get(key)
            if cat is None:
                # A stage with no category in the report (e.g. user_agent seam,
                # stream) still advances the bar so the UI shows linear motion.
                self._stage(key)
                continue
            if report.is_dry_run:
                self._stage(
                    key,
                    success=cat.would_create + cat.would_update,
                    skipped=cat.would_skip,
                )
            else:
                self._stage(
                    key,
                    success=cat.created + cat.updated,
                    failed=cat.failed,
                    skipped=cat.skipped,
                )

    # -- result shaping ------------------------------------------------------

    @staticmethod
    def _report_succeeded(report, is_apply: bool) -> bool:
        """A dry-run always 'succeeds' (it produced a plan); an apply succeeds
        only on a clean SUCCESS outcome (never on a rolled-back/incomplete state)."""
        from dbas.restore_contracts import RestoreOutcome

        if not is_apply or report.is_dry_run:
            return True
        return report.outcome == RestoreOutcome.SUCCESS

    @staticmethod
    def _summary_message(report, is_apply: bool) -> str:
        if report.is_dry_run or not is_apply:
            total_create = sum(c.would_create for c in report.categories)
            total_update = sum(c.would_update for c in report.categories)
            total_skip = sum(c.would_skip for c in report.categories)
            return (
                "Dry-run complete: would create %d, update %d, skip %d "
                "across %d categories" % (
                    total_create, total_update, total_skip, len(report.categories),
                )
            )
        outcome = report.outcome.value if report.outcome else "unknown"
        total_created = sum(c.created for c in report.categories)
        total_failed = sum(c.failed for c in report.categories)
        return (
            "Restore %s: created %d, failed %d across %d categories" % (
                outcome, total_created, total_failed, len(report.categories),
            )
        )

    def _fail(self, started_at: datetime, message: str) -> TaskResult:
        """Build a failed TaskResult and mark progress failed (sanitized message)."""
        logger.warning("[DBAS-RESTORE] %s", message)
        self._set_progress(status="failed", current_item="finalize")
        return TaskResult(
            success=False,
            message=message,
            error=message,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            failed_count=1,
        )

    def _cleanup(self, artifact_path: Path) -> None:
        """Delete the temp artifact on completion/failure (best-effort)."""
        if not self.cleanup_artifact:
            return
        try:
            if artifact_path.exists():
                artifact_path.unlink()
                logger.info("[DBAS-RESTORE] Cleaned up temp artifact")
        except OSError as exc:
            logger.warning("[DBAS-RESTORE] Failed to clean up temp artifact: %s", exc)

    def _cleanup_decrypted(self, decrypted_path: Path) -> None:
        """Delete the transient DECRYPTED plaintext temp (always, best-effort).

        Unlike the uploaded artifact, the decrypted plaintext ZIP holds the
        operator's secrets in the clear, so it is ALWAYS removed regardless of
        ``cleanup_artifact`` — it must never linger on disk after the restore.
        """
        try:
            if decrypted_path.exists():
                decrypted_path.unlink()
                logger.info("[DBAS-RESTORE] Cleaned up decrypted plaintext temp")
        except OSError as exc:
            logger.warning(
                "[DBAS-RESTORE] Failed to clean up decrypted temp: %s", exc
            )
