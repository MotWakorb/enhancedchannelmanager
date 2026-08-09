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

/**
 * Restore a new-format DBAS backup artifact (.zip) — bead 7euap, wiring the
 * async restore endpoint (POST /api/backup/restore-dbas) that o8tbv shipped to
 * the UI, plus the u81kh encrypted-artifact passphrase path.
 *
 * Flow: upload .zip -> (passphrase if encrypted) -> dry-run (default) or apply
 * -> poll /api/tasks/dbas_restore via useRestoreProgress -> read the terminal
 * RestoreReport from task history -> RestoreCompleteSummary + logo-miss banner.
 * A dry-run result offers an "Apply these changes" follow-through using the same
 * file + passphrase, so the operator previews before the one-way apply.
 *
 * Both apply entry points — the configure-step "Apply" mode and the post-dry-run
 * "Apply these changes" follow-through — are gated behind a TypeToConfirmDialog
 * requiring the operator to type the uploaded file's name, mirroring the
 * saved-backups path's filename-based confirm (DbasRestoreSavedModal).
 *
 * Distinct from BackupRestoreModal (the legacy section-level .yaml restore); the
 * two share the RestoreProgress / RestoreCompleteSummary / LogoMissBanner
 * components and the useRestoreProgress hook, not the flow.
 */
const DBAS_RESTORE_TASK_ID = 'dbas_restore';
// Magic prefix of an encrypted artifact envelope (dbas/artifact_crypto.MAGIC).
// Lets the UI require a passphrase up front for an encrypted file instead of
// failing the round-trip at the decrypt gate.
const ARTIFACT_MAGIC = 'ECMBKENC';

type Step = 'upload' | 'configure' | 'restoring' | 'results';

async function detectEncrypted(file: File): Promise<boolean> {
  try {
    const head = new Uint8Array(await file.slice(0, ARTIFACT_MAGIC.length).arrayBuffer());
    return Array.from(head).map((b) => String.fromCharCode(b)).join('') === ARTIFACT_MAGIC;
  } catch {
    return false;
  }
}

export function DbasRestoreModal({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState<Step>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [isEncrypted, setIsEncrypted] = useState(false);
  const [passphrase, setPassphrase] = useState('');
  const [applyMode, setApplyMode] = useState(false); // false = dry-run (default)
  // What happens to channels this restore does NOT create (bead dfkbn).
  // 'preserve' by default: on an empty target the modes are identical, so the
  // safe one costs disaster recovery nothing.
  const [reattachMode, setReattachMode] = useState<ChannelReattachMode>('preserve');
  const [runningApply, setRunningApply] = useState(false);
  const [restoreTaskId, setRestoreTaskId] = useState<string | null>(null);
  const [restoreReport, setRestoreReport] = useState<RestoreReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showApplyConfirm, setShowApplyConfirm] = useState(false);
  // One token per started run. Every DBAS restore run shares the constant task
  // id "dbas_restore", so without this the progress hook's poll effect never
  // restarts and the previous run's terminal state stays live — the finalize
  // effect below would then fire instantly and render the PREVIOUS run's
  // report for THIS run (bead dfkbn, review round 4).
  const [runKey, setRunKey] = useState(0);
  const finalizedRef = useRef(false);

  // Dispatcharr base URL drives the logo-miss banner's "Fix in Dispatcharr"
  // link (bead .19). Best-effort: the banner omits the link if unknown.
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
  // Guard against navigating away mid-apply — Dispatcharr has no DB transaction,
  // the compensating rollback depends on the op completing (ADR-012).
  useNavigateAwayGuard(isRestoring && runningApply);

  const handleFile = useCallback(async (selected: File) => {
    if (!selected.name.toLowerCase().endsWith('.zip')) {
      setError('Please select a .zip backup artifact');
      return;
    }
    setError(null);
    setFile(selected);
    setIsEncrypted(await detectEncrypted(selected));
    setPassphrase('');
    setApplyMode(false);
    setReattachMode('preserve');
    setStep('configure');
  }, []);

  const handleDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) void handleFile(dropped);
  }, [handleFile]);

  const handlePickFile = useCallback(() => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.zip';
    input.onchange = () => {
      const f = input.files?.[0];
      if (f) void handleFile(f);
    };
    input.click();
  }, [handleFile]);

  const start = useCallback(async (apply: boolean) => {
    if (!file) return;
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
      const res = await api.startDbasRestore(
        file,
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
  }, [file, isEncrypted, passphrase, reattachMode]);

  // On terminal, read the RestoreReport (or sanitized failure) from task
  // history. The task sets progress='completed' BEFORE the engine writes the
  // execution row with details, so retry briefly until the terminal row lands.
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
        // Sanitized failure (wrong passphrase / corrupt / unsupported version /
        // orchestration error). Back to configure so the operator can retry.
        setError(failMsg || 'Restore failed');
        setStep('configure');
      }
    })();
    return () => { cancelled = true; };
  }, [step, restoreTaskId, progress.isComplete, progress.isError, runDidNotStart, progress.error]);

  const canClose = step !== 'restoring';
  const canStart = !!file && (!isEncrypted || passphrase.length > 0) && !busy;

  return (
    <ModalOverlay onClose={canClose ? onClose : () => {}}>
      <div className="modal-container modal-md backup-restore-modal-container">
        <div className="modal-header">
          <h3 className="modal-title">Restore DBAS Backup</h3>
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

          {step === 'upload' && (
            <div
              className={`brm-dropzone ${isDragging ? 'is-dragging' : ''}`}
              onDrop={handleDrop}
              onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
              onDragLeave={() => setIsDragging(false)}
              onClick={handlePickFile}
            >
              <span className="material-icons brm-dropzone-icon">upload_file</span>
              <p className="brm-dropzone-text">
                Drag &amp; drop a backup artifact here, or click to browse
              </p>
              <p className="brm-dropzone-hint">.zip artifact (encrypted or plain)</p>
            </div>
          )}

          {step === 'configure' && file && (
            <>
              <div className="brm-file-info">
                <span className="material-icons">{isEncrypted ? 'lock' : 'folder_zip'}</span>
                <div>
                  <div className="brm-file-name">{file.name}</div>
                  <div className="brm-file-meta">
                    {isEncrypted ? 'Encrypted artifact — passphrase required' : 'Backup artifact'}
                  </div>
                </div>
              </div>

              {isEncrypted && (
                <div className="dbr-field">
                  <label htmlFor="dbr-passphrase">Passphrase</label>
                  <input
                    id="dbr-passphrase"
                    type="password"
                    autoComplete="off"
                    value={passphrase}
                    onChange={(e) => setPassphrase(e.target.value)}
                    placeholder="Passphrase used when this backup was created"
                  />
                </div>
              )}

              <div className="dbr-mode">
                <label className={`dbr-mode-option ${!applyMode ? 'is-active' : ''}`}>
                  <input type="radio" checked={!applyMode} onChange={() => setApplyMode(false)} />
                  <span>
                    <strong>Preview (dry run)</strong>
                    <span className="dbr-mode-hint">See what would change. Makes no changes.</span>
                  </span>
                </label>
                <label className={`dbr-mode-option ${applyMode ? 'is-active' : ''}`}>
                  <input type="radio" checked={applyMode} onChange={() => setApplyMode(true)} />
                  <span>
                    <strong>Apply</strong>
                    <span className="dbr-mode-hint">Write the restore to Dispatcharr &amp; ECM.</span>
                  </span>
                </label>
              </div>

              <ChannelReattachModeField
                value={reattachMode}
                onChange={setReattachMode}
                disabled={busy}
                idPrefix="dbr"
              />

              {applyMode && (
                <div className="restore-warning">
                  <span className="material-icons">warning</span>
                  <span>Applying a restore changes live configuration. Run a preview first if unsure.</span>
                </div>
              )}
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
          {step === 'upload' && (
            <button className="modal-btn modal-btn-secondary" onClick={onClose}>Cancel</button>
          )}
          {step === 'configure' && (
            <>
              <button className="modal-btn modal-btn-secondary" onClick={onClose}>Cancel</button>
              <button
                className="modal-btn modal-btn-primary"
                disabled={!canStart}
                onClick={() => (applyMode ? setShowApplyConfirm(true) : start(false))}
              >
                {applyMode ? 'Apply restore…' : 'Run preview'}
              </button>
            </>
          )}
          {step === 'results' && restoreReport && (
            <>
              {restoreReport.is_dry_run && (
                <>
                  {/*
                    A preview is a decision point, so the operator must be able
                    to act on what it told them. The summary can report that the
                    restore would replace guide data, logos and grouping on
                    channels they already have and advise picking the other
                    option — advice
                    that pointed at a control this step had unmounted, with no
                    way back to it (bead dfkbn, review round 3).
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

      {showApplyConfirm && file && (
        <TypeToConfirmDialog
          title="Apply DBAS Restore"
          message={
            <>
              This overwrites current ECM/Dispatcharr configuration with the contents of{' '}
              <strong>{file.name}</strong>. This cannot be undone.
            </>
          }
          confirmText={file.name}
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
