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
from typing import NamedTuple, Optional

from dbas.restore_contracts import ChannelReattachMode
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


class RestoreCounts(NamedTuple):
    """Per-run item counts summed from ``RestoreReport.categories``.

    Mirrors ``tasks.dbas_sync.SyncCounts`` (enhancedchannelmanager-tyei5):
    the ``TaskResult`` numeric badges (Task History UI, and
    ``task_engine.py``'s ``elif result.failed_count > 0`` "Completed with
    Warnings" branch) must reflect the REAL per-category sums, not the
    task-level stage count (``_TOTAL_STAGES``) previously hardcoded into
    ``total_items``/``success_count`` here.

    * ``total_items`` — success_count + skipped_count + failed_count.
    * ``success_count`` — dry-run: would_create + would_update; apply:
      created + updated.
    * ``failed_count`` — sum of ``category.failed`` on BOTH dry-run and
      apply. A dry-run plan cannot FAIL an apply attempt, but ``cat.failed``
      also carries per-item CONFLICTs an importer surfaces unconditionally
      (see ``EntityCategoryReport.failed``'s docstring) — those are facts
      about the source data, populated on a dry-run preview too, so this
      bucket is identical in both branches.
    * ``skipped_count`` — dry-run: would_skip; apply: skipped.
    """

    total_items: int
    success_count: int
    failed_count: int
    skipped_count: int


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
    - ``channel_reattach_mode``: ``"preserve"`` or ``"overwrite"``, what the
      post-create reattach passes do to channels this restore did NOT create
      (bead …-dfkbn, PR review W1). ``"preserve"`` is the default and is what an
      absent or unrecognised value resolves to, so an OLD client that does not
      send the field can never silently start overwriting an operator's live EPG
      links and logos.
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
        # PRESERVE unless the operator explicitly chose otherwise (…-dfkbn W1).
        # A PER-RUN transient on a SINGLETON task instance, and therefore reset
        # in execute()'s finally — see _reset_run_transients.
        self.channel_reattach_mode: ChannelReattachMode = ChannelReattachMode.PRESERVE
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
            "channel_reattach_mode": self.channel_reattach_mode.value,
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
        if "channel_reattach_mode" in config:
            self.channel_reattach_mode = ChannelReattachMode.coerce(
                config["channel_reattach_mode"]
            )

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

    def _reset_run_transients(self) -> None:
        """Return the per-run restore transients to their SAFE defaults.

        The ``cytzj`` shape, one bead old and re-created here (PR review round 2,
        finding 5). ``get_task_instance()`` hands back a SINGLETON, and
        :meth:`update_config` only assigns a field when its key is PRESENT, so a
        bare ``POST /api/tasks/dbas_restore/run`` with no parameters re-runs with
        whatever the PREVIOUS run left behind. For ``channel_reattach_mode`` that
        means a run where the option is absent would silently inherit
        ``overwrite`` — precisely the "absent resolves to preserve" guarantee the
        rest of this changeset is built on, defeated by process state.

        ``confirm_apply`` gets the same treatment for the same reason, and it is
        the more dangerous of the two: a stale ``True`` arms a DESTRUCTIVE apply
        on a run the operator asked nothing of.

        ``cleanup_artifact`` is the fifth, and it defaults back to ``True``
        rather than to its last value: ``/restore-dbas-saved`` sets it ``False``
        DELIBERATELY, because the artifact there is the operator's own saved
        backup and must survive the restore. Left sticky on the singleton, that
        ``False`` governs whether a LATER run deletes the file it was handed.

        Called from execute()'s ``finally`` so it runs on EVERY exit path.
        """
        self.channel_reattach_mode = ChannelReattachMode.PRESERVE
        self.confirm_apply = False
        self.artifact_path = None
        self.passphrase = None
        self.cleanup_artifact = True

    async def execute(self) -> TaskResult:
        """Run one restore, then unconditionally disarm the per-run transients."""
        try:
            return await self._execute_run()
        finally:
            self._reset_run_transients()

    async def _execute_run(self) -> TaskResult:
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
                    channel_reattach_mode=self.channel_reattach_mode,
                    # STATED, not inherited (bead …-efvyg). The archive-restore
                    # path matches on the FULL 4-tier ladder: this is the value
                    # ``import_channels`` already runs under here, and the
                    # post-create rebind is where a restore does essentially all
                    # of its matching (the destination has no provider streams
                    # yet at channel-import time). The Tier-3 floor belongs to
                    # the cross-instance SYNC path (ruling 1b), not to this one.
                    allow_fuzzy_stream_match=True,
                )
            else:
                # The preview MUST run under the SAME mode the apply will, or it
                # mispredicts the one number the mode exists to control.
                report = await run_dry_run(
                    plan=plan,
                    client=client,
                    channel_reattach_mode=self.channel_reattach_mode,
                )
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
        counts = self._counts_from_report(report, is_apply)
        return TaskResult(
            success=self._report_succeeded(report, is_apply),
            completed_degraded=self._degraded_not_failed(report, is_apply),
            message=self._summary_message(report, is_apply),
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            total_items=counts.total_items,
            success_count=counts.success_count,
            failed_count=counts.failed_count,
            skipped_count=counts.skipped_count,
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
    def _degraded_not_failed(report, is_apply: bool) -> bool:
        """True when the restore RAN TO COMPLETION and rolled nothing back.

        Beads ``…-daziw`` (PO decision 2) and ``…-cwmid``. Such a run is a
        WARNING, not a red "Task Failed": the applied state is real and kept, and
        the summary names the shortfall. The task declares that state here;
        ``task_engine`` maps it to the alert severity
        (:attr:`task_scheduler.TaskResult.completed_degraded`).

        The condition is the OUTCOME, not the particular category that degraded.
        :class:`dbas.restore_contracts.RestoreOutcome.COMPLETED_WITH_FAILURES`
        already means exactly "ran to completion and NOTHING was rolled back",
        for either of its two triggers — a non-fatal category's failed row, or a
        channel left with no playable stream. The test is
        :attr:`~dbas.restore_contracts.RestoreOutcome.is_degraded_not_failed`,
        SHARED with ``tasks.dbas_sync``: the cross-instance sync builds its own
        TaskResult from the same reports, and a second copy of this rule is how
        it came to announce "Task Failed" for a run this one calls a warning.

        daziw's original form keyed on ``channels_with_no_playable_stream`` and
        excluded any run with a failed row, which inverted the severity ordering
        for triage (bead ``…-cwmid``, drill run 2026-08-06-run9): a restore in
        which 12 of 12 channels could not play alerted as a ``warning`` while one
        unwritable logo — on a category deliberately made NON-FATAL — alerted as
        ``error`` / "Task Failed". "Task Failed" was also untrue, and the
        published docs warn that re-running a restore after a failed attempt
        starts from an unknown state.

        ``error`` is reserved for what it describes: the two rolled-back /
        indeterminate outcomes, an orchestration error, and any dry run's
        failure mode.
        """
        if not is_apply or report.is_dry_run or report.outcome is None:
            return False
        return report.outcome.is_degraded_not_failed

    @staticmethod
    def _counts_from_report(report, is_apply: bool) -> RestoreCounts:
        """Sum the REAL per-category item counts across ``report.categories``.

        Mirrors ``tasks.dbas_sync.DbasSyncTask._counts_from_report`` — same
        underlying counts as :meth:`_summary_message`, so the human-readable
        message and the numeric ``TaskResult`` badges never disagree.
        """
        failed_count = sum(c.failed for c in report.categories)
        if report.is_dry_run or not is_apply:
            success_count = sum(
                c.would_create + c.would_update for c in report.categories
            )
            skipped_count = sum(c.would_skip for c in report.categories)
        else:
            success_count = sum(c.created + c.updated for c in report.categories)
            skipped_count = sum(c.skipped for c in report.categories)
        total_items = success_count + skipped_count + failed_count
        return RestoreCounts(
            total_items=total_items,
            success_count=success_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
        )

    @staticmethod
    def stream_reattach_phrases(report) -> list[str]:
        """Name the placeholder populations WITHOUT overstating either (…-daziw).

        PUBLIC, unlike its sibling builders: ``tasks.dbas_sync`` renders the same
        two counters into the cross-instance sync's summary, and one wording for
        one pair of counters is the point — a second copy is how the two paths
        came to describe the same run differently in the first place.

        Two different facts, previously reported as one:

        * ``channels_with_no_playable_stream`` — not one URL-bearing stream is
          left on the channel. It CANNOT PLAY. This is the claim the old wording
          made on behalf of the other counter, which could not support it.
        * ``channels_needing_stream_reattach`` — the channel holds at least one
          placeholder slot. It is a SUPERSET of the above, and most of its
          members play fine (the ``…-ixdaw`` fix produces exactly that shape).

        Rendered as one clause when the two counts agree (every holder is dead)
        and as one combined clause when they differ, so the summary never reads
        as two unrelated problems or double-counts the same channels.
        """
        unplayable = getattr(report, "channels_with_no_playable_stream", 0) or 0
        holding = getattr(report, "channels_needing_stream_reattach", 0) or 0
        if unplayable and holding > unplayable:
            return [
                "%d channel(s) have NO playable stream (of %d still holding a "
                "placeholder stream)" % (unplayable, holding)
            ]
        if unplayable:
            return ["%d channel(s) have NO playable stream" % unplayable]
        if holding:
            return ["%d channel(s) still hold a placeholder stream" % holding]
        return []

    @staticmethod
    def _channel_group_drift_phrases(report, *, is_preview: bool = False) -> list[str]:
        """Say whether the drifted channels were REGROUPED or merely reported (…-r1ei7).

        One counter, two outcomes: under the default relink mode the restore
        reports the divergence and leaves the operator's grouping alone, and
        under ``replace`` it also moves each channel into the archive's group.
        A single template would have to describe both, so the clause is built
        from the detail rows' ``moved`` flag instead — the same reason the two
        placeholder populations get their own builder.

        THIRD OUTCOME: the check did not run at all, because the operator
        deselected the channel-groups category. The counter is ``0`` then for a
        reason that has nothing to do with the lineup, and a suffix that stays
        silent lets that zero pass for a clean bill of health — so the report's
        ``channel_group_drift_note`` is emitted verbatim instead. It is the only
        clause here that is a sentence rather than a count phrase, which is
        correct: it is a caveat, not an action item.
        """
        note = getattr(report, "channel_group_drift_note", None)
        if note:
            return [note]
        total = getattr(report, "channel_group_drift", 0) or 0
        if not total:
            return []
        details = getattr(report, "channel_group_drift_details", None) or []
        moved = sum(1 for d in details if getattr(d, "moved", False))
        if moved >= total:
            return [
                "%d channel(s) %s into the group the backup assigns them"
                % (total, "would be moved" if is_preview else "were moved")
            ]
        if moved:
            return [
                "%d channel(s) are in a different group than the backup "
                "(%d %s, the rest left as they are)"
                % (total, moved, "would be moved" if is_preview else "moved")
            ]
        return [
            "%d channel(s) are in a different group than the backup; left as they are"
            % total
        ]

    # Post-restore action items, as ``(attribute, apply_template,
    # preview_template)``. A PREVIEW makes no changes, so its clause must be in
    # the FUTURE tense (bead ``enhancedchannelmanager-juu3c``): PR #784 made
    # these counters genuinely PREDICTED on a dry run, and rendering a real
    # prediction as "N profile membership(s) corrected" reads as though the
    # restore already happened — which is exactly the trust the preview exists
    # to earn. The counters and the summary structure are identical either way;
    # only the verb moves.
    _ACTION_ITEM_TEMPLATES = (
        (
            "logo_misses",
            "%d logo(s) could not be reinstated",
            "%d logo(s) would not be reinstated",
        ),
        (
            "epg_links_unrestored",
            "%d channel(s) restored without an EPG link",
            "%d channel(s) would be restored without an EPG link",
        ),
        (
            "profile_membership_drift",
            "%d profile membership(s) corrected",
            "%d profile membership(s) would be corrected",
        ),
    )

    @staticmethod
    def _credential_reentry_suffix(report, *, is_preview: bool = False) -> str:
        """Name every post-restore ACTION ITEM in the one-line summary.

        The task-history row and the MCP tool result are the ONLY surfaces an
        operator who did not use the restore modal ever sees. Each item below is
        state the operator HAD before the backup and does NOT have after the
        restore — none of them is a failure, so none of them moves the counts,
        which is exactly how the drill's ``Restore success: created 32, failed 0``
        described an instance where not one channel could play, every logo and
        EPG link was gone, and a channel profile had silently widened
        (beads …-6pilh / …-2o0cz / …-dfkbn). They belong in the message itself,
        not only in ``details``.

        Args:
            report: The :class:`RestoreReport` to summarize.
            is_preview: Whether this suffix is being appended to a DRY-RUN
                summary. When true the action items are rendered in the future
                tense, because a preview changed nothing (bead ``…-juu3c``).
                ``credentials_needing_reentry`` is unaffected — "N account(s)
                need credentials re-entered" is already an is/will-be statement
                that is true of both a preview and an apply.
        """
        parts: list[str] = []
        credentials = getattr(report, "credentials_needing_reentry", 0) or 0
        if credentials > 0:
            parts.append("%d account(s) need credentials re-entered" % credentials)
        # The two placeholder populations need each other's counts to read
        # correctly, so they are rendered together rather than as two rows of the
        # generic table below. They are NULL (not predicted) on a preview, so the
        # tense question does not arise for them.
        parts.extend(DbasRestoreTask.stream_reattach_phrases(report))
        # Channel->group drift reads differently depending on whether the mode
        # also reconciled it, so it too gets a builder rather than a template.
        parts.extend(
            DbasRestoreTask._channel_group_drift_phrases(report, is_preview=is_preview)
        )
        for attribute, applied, predicted in DbasRestoreTask._ACTION_ITEM_TEMPLATES:
            count = getattr(report, attribute, 0) or 0
            if count > 0:
                parts.append((predicted if is_preview else applied) % count)
        if not parts:
            return ""
        return "; " + "; ".join(parts)

    @staticmethod
    def _summary_message(report, is_apply: bool) -> str:
        if report.is_dry_run or not is_apply:
            total_create = sum(c.would_create for c in report.categories)
            total_update = sum(c.would_update for c in report.categories)
            total_skip = sum(c.would_skip for c in report.categories)
            # `failed` is populated on a dry-run preview by the per-item
            # conflict paths (source-side duplicate name, ambiguous
            # null-channel-number collision) — surface it here too so this
            # message never disagrees with the numeric failed_count badge.
            # Mirrors tasks.dbas_sync.DbasSyncTask._summary_message's dry-run
            # branch (enhancedchannelmanager-tyei5).
            total_conflict = sum(c.failed for c in report.categories)
            return (
                "Dry-run complete: would create %d, update %d, skip %d, "
                "%d conflict(s) across %d categories" % (
                    total_create, total_update, total_skip, total_conflict,
                    len(report.categories),
                )
            ) + DbasRestoreTask._credential_reentry_suffix(report, is_preview=True)
        outcome = report.outcome.value if report.outcome else "unknown"
        total_created = sum(c.created for c in report.categories)
        total_failed = sum(c.failed for c in report.categories)
        return (
            "Restore %s: created %d, failed %d across %d categories" % (
                outcome, total_created, total_failed, len(report.categories),
            )
        ) + DbasRestoreTask._credential_reentry_suffix(report)

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
