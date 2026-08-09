import { useState, useEffect, useCallback, useRef } from 'react';
import { ModalOverlay } from './ModalOverlay';
import { RestoreProgress } from './RestoreProgress';
import { RestoreCompleteSummary } from './RestoreCompleteSummary';
import { LogoMissBanner } from './LogoMissBanner';
import { TypeToConfirmDialog } from './TypeToConfirmDialog';
import { ChannelReattachModeField } from './ChannelReattachModeField';
import { useRestoreProgress } from '../hooks/useRestoreProgress';
import { useNavigateAwayGuard } from '../hooks/useNavigateAwayGuard';
import * as api from '../services/api';
import { isTerminalExecutionStatus } from '../utils/taskExecutionStatus';
import { invalidateServerData } from '../hooks/useServerDataInvalidation';
import type { ChannelReattachMode, RestoreReport } from '../services/api';
import './ModalBase.css';
import './BackupRestoreModal.css';
import './DbasRestoreModal.css';

const DBAS_RESTORE_TASK_ID = 'dbas_restore';

type Step = 'configure' | 'restoring' | 'results';

/**
 * Restore a DBAS-format backup artifact (.zip) already SAVED on the server
 * (enhancedchannelmanager-rzhid) — the SAVED-file analogue of DbasRestoreModal
 * (which restores an operator-uploaded file). Same dry-run-first contract:
 * POST /api/backup/restore-dbas-saved defaults to a counts-only dry run;
 * applying requires an explicit type-to-confirm step.
 *
 * `GET /backup/saved` cannot tell a DBAS-format .zip apart from a legacy
 * full-archive .zip (both share the `ecm-backup-<ts>.zip` naming convention)
 * — BackupRestoreSection offers this action alongside the legacy restore
 * action on every saved .zip row and lets the operator pick the one that
 * matches how the file was produced.
 */
export function DbasRestoreSavedModal({
  filename,
  onClose,
}: {
  filename: string;
  onClose: () => void;
}) {
  const [step, setStep] = useState<Step>('configure');
  const [isEncrypted, setIsEncrypted] = useState(false);
  const [passphrase, setPassphrase] = useState('');
  // What happens to channels this restore does NOT create (bead dfkbn).
  const [reattachMode, setReattachMode] = useState<ChannelReattachMode>('preserve');
  const [runningApply, setRunningApply] = useState(false);
  const [restoreTaskId, setRestoreTaskId] = useState<string | null>(null);
  const [restoreReport, setRestoreReport] = useState<RestoreReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showApplyConfirm, setShowApplyConfirm] = useState(false);
  // One token per started run. Every DBAS restore run shares the constant task
  // id "dbas_restore", so without this the progress hook's poll effect never
  // restarts and the previous run's terminal state stays live — the finalize
  // effect below would then fire instantly and render the PREVIOUS run's
  // report for THIS run (bead dfkbn, review round 4).
  const [runKey, setRunKey] = useState(0);
  const finalizedRef = useRef(false);

  const [dispatcharrUrl, setDispatcharrUrl] = useState('');
  useEffect(() => {
    let active = true;
    api.getSettings().then((s) => { if (active) setDispatcharrUrl(s.url ?? ''); }).catch(() => {});
    return () => { active = false; };
  }, []);

  const progress = useRestoreProgress({ taskId: restoreTaskId, runKey });
  // True when the progress hook synthesised a terminal ERROR because no new run
  // ever appeared, as opposed to the backend genuinely reporting one. Only the
  // synthesised view has a null payload.
  const runDidNotStart = progress.isError && progress.progress === null;
  const isRestoring = step === 'restoring';
  useNavigateAwayGuard(isRestoring && runningApply);

  const start = useCallback(async (apply: boolean) => {
    if (isEncrypted && !passphrase) {
      setError('This backup is encrypted — enter the passphrase to continue.');
      return;
    }
    setBusy(true);
    setError(null);
    finalizedRef.current = false;
    setRestoreReport(null);
    setRunningApply(apply);
    try {
      const res = await api.restoreDbasBackupSaved(
        filename,
        apply,
        isEncrypted ? passphrase : undefined,
        reattachMode,
      );
      // Advance the run token HERE, in the same batch as the step flip, not in
      // the handler's synchronous prologue. Bumping it before the await
      // restarted the progress poll while the trigger request was still in
      // flight, so the budget that exists to cover the scheduler's
      // fire-and-forget lag was instead spent on the upload itself: a slow
      // multipart POST (this release archives Dispatcharr-hosted logo bytes,
      // which it never used to) exhausted it before the run had been asked for.
      //
      // In this batch the derived view is EMPTY_VIEW, so isComplete/isError are
      // false and the finalize effect below does not fire; and the previous
      // run's view is never rendered, because `step` only becomes 'restoring'
      // in this same batch.
      setRunKey((n) => n + 1);
      setRestoreTaskId(res.task_id);
      setStep('restoring');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Restore failed');
    } finally {
      setBusy(false);
    }
  }, [filename, isEncrypted, passphrase, reattachMode]);

  // On terminal, read the RestoreReport (or sanitized failure) from task
  // history — same retry-until-landed pattern as DbasRestoreModal (the task
  // sets progress='completed' before the engine writes the execution row).
  useEffect(() => {
    if (step !== 'restoring' || !restoreTaskId) return;
    if (!progress.isComplete && !progress.isError) return;
    if (finalizedRef.current) return;
    finalizedRef.current = true;

    // The hook gave up waiting for the run to start (it never saw a new
    // `started_at`). There is NO result for this run, and task history is
    // unversioned — `?limit=1` still holds the PREVIOUS run's row, so reading it
    // here would render that run's report as this one's, which is the whole
    // defect this machinery exists to prevent. `progress.progress === null` is
    // what tells the two apart: a real backend terminal state always arrives
    // through viewFromProgress and carries its payload, while the hook's
    // give-up view is synthesised from the empty one.
    if (runDidNotStart) {
      setError(
        progress.error ||
          'The restore did not start. It may already be running — check Task History.',
      );
      setStep('configure');
      return;
    }

    let cancelled = false;
    (async () => {
      let report: RestoreReport | null = null;
      let failMsg: string | null = null;
      for (let i = 0; i < 6 && !cancelled; i++) {
        try {
          const { history } = await api.getTaskHistory(DBAS_RESTORE_TASK_ID, 1);
          const exec = history?.[0];
          // Any terminal status, not just completed/failed: a degraded run
          // reports `completed_with_warnings` (bead fexq1) and its report is
          // just as readable as a clean one's.
          if (exec && isTerminalExecutionStatus(exec.status)) {
            const rep = exec.details?.restore_report as RestoreReport | undefined;
            if (rep) { report = rep; break; }
            failMsg = exec.error || exec.message || 'Restore failed';
            if (exec.status === 'failed') break;
          }
        } catch {
          /* transient — retry */
        }
        await new Promise((r) => setTimeout(r, 500));
      }
      if (cancelled) return;
      if (report) {
        setRestoreReport(report);
        // An APPLY creates and renames channel groups that the Channel Manager's
        // group filter is holding a pre-restore copy of — drill run
        // 2026-08-08-run17 reported "No groups match Drill17" for groups this
        // restore had just made, until a full reload (bead
        // enhancedchannelmanager-3vtim). A dry run changed nothing, so it
        // publishes nothing.
        if (!report.is_dry_run) {
          invalidateServerData('channel-groups');
          // The groups' CONTENTS are just as stale as the group list: a
          // restore that created 12 channels still rendered "CHANNELS 0"
          // behind a correctly-refreshed filter, and the Channels pane has no
          // refresh control to fix it in place (bead
          // enhancedchannelmanager-eelgi).
          invalidateServerData('channels');
        }
        setStep('results');
      } else {
        setError(failMsg || 'Restore failed');
        setStep('configure');
      }
    })();
    return () => { cancelled = true; };
  }, [step, restoreTaskId, progress.isComplete, progress.isError, runDidNotStart, progress.error]);

  const canClose = step !== 'restoring';
  const canStart = (!isEncrypted || passphrase.length > 0) && !busy;

  return (
    <ModalOverlay onClose={canClose ? onClose : () => {}}>
      <div className="modal-container modal-md backup-restore-modal-container">
        <div className="modal-header">
          <h3 className="modal-title">Restore Saved DBAS Backup</h3>
          {canClose && (
            <button className="modal-close-btn" onClick={onClose} aria-label="Close" title="Close">
              <span className="material-icons" aria-hidden="true">close</span>
            </button>
          )}
        </div>

        <div className="modal-body">
          {error && (
            <div className="modal-error-banner">
              <span className="material-icons">error</span>
              {error}
            </div>
          )}

          {step === 'configure' && (
            <>
              <div className="brm-file-info">
                <span className="material-icons">folder_zip</span>
                <div>
                  <div className="brm-file-name">{filename}</div>
                  <div className="brm-file-meta">Saved backup artifact</div>
                </div>
              </div>

              <label className="modal-checkbox-label">
                <input
                  type="checkbox"
                  checked={isEncrypted}
                  onChange={(e) => setIsEncrypted(e.target.checked)}
                />
                This backup is encrypted
              </label>

              {isEncrypted && (
                <div className="dbr-field">
                  <label htmlFor="dbrs-passphrase">Passphrase</label>
                  <input
                    id="dbrs-passphrase"
                    type="password"
                    autoComplete="off"
                    value={passphrase}
                    onChange={(e) => setPassphrase(e.target.value)}
                    placeholder="Passphrase used when this backup was created"
                  />
                </div>
              )}

              <ChannelReattachModeField
                value={reattachMode}
                onChange={setReattachMode}
                disabled={busy}
                idPrefix="dbrs"
              />

              <p className="brm-file-meta">
                Runs a preview (dry run) first — no changes are made until you review the plan
                and explicitly apply it.
              </p>
            </>
          )}

          {step === 'restoring' && (
            <RestoreProgress mode={runningApply ? 'restore' : 'dry-run'} view={progress} />
          )}

          {step === 'results' && restoreReport && (
            <RestoreCompleteSummary
              report={restoreReport}
              mode={restoreReport.is_dry_run ? 'dry-run' : 'applied'}
              bannerSlot={<LogoMissBanner report={restoreReport} dispatcharrUrl={dispatcharrUrl} />}
            />
          )}
        </div>

        <div className="modal-footer">
          {step === 'configure' && (
            <>
              <button className="modal-btn modal-btn-secondary" onClick={onClose}>Cancel</button>
              <button
                className="modal-btn modal-btn-primary"
                disabled={!canStart}
                onClick={() => start(false)}
              >
                Run preview
              </button>
            </>
          )}
          {step === 'results' && restoreReport && (
            <>
              {restoreReport.is_dry_run && (
                <>
                  {/*
                    Back to the options the preview is a decision about. Without
                    it the summary's advice to pick the other reattach option
                    named a control this step had unmounted (bead dfkbn, review
                    round 3).
                  */}
                  <button
                    className="modal-btn modal-btn-secondary"
                    disabled={busy}
                    onClick={() => { setRestoreReport(null); setStep('configure'); }}
                  >
                    Back to options
                  </button>
                  <button
                    className="modal-btn modal-btn-primary"
                    disabled={busy}
                    onClick={() => setShowApplyConfirm(true)}
                  >
                    Apply these changes
                  </button>
                </>
              )}
              <button className="modal-btn modal-btn-secondary" onClick={onClose}>Done</button>
            </>
          )}
        </div>
      </div>

      {showApplyConfirm && (
        <TypeToConfirmDialog
          title="Apply DBAS Restore"
          message={
            <>
              This overwrites current ECM/Dispatcharr configuration with the contents of{' '}
              <strong>{filename}</strong>. This cannot be undone.
            </>
          }
          confirmText={filename}
          confirmLabel="Apply restore"
          busy={busy}
          onCancel={() => setShowApplyConfirm(false)}
          onConfirm={() => {
            setShowApplyConfirm(false);
            start(true);
          }}
        />
      )}
    </ModalOverlay>
  );
}
