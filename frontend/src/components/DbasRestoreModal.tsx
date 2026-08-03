import { useState, useEffect, useCallback, useRef } from 'react';
import { ModalOverlay } from './ModalOverlay';
import { RestoreProgress } from './RestoreProgress';
import { RestoreCompleteSummary } from './RestoreCompleteSummary';
import { LogoMissBanner } from './LogoMissBanner';
import { TypeToConfirmDialog } from './TypeToConfirmDialog';
import { useRestoreProgress } from '../hooks/useRestoreProgress';
import { useNavigateAwayGuard } from '../hooks/useNavigateAwayGuard';
import * as api from '../services/api';
import type { RestoreReport } from '../services/api';
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
  const [runningApply, setRunningApply] = useState(false);
  const [restoreTaskId, setRestoreTaskId] = useState<string | null>(null);
  const [restoreReport, setRestoreReport] = useState<RestoreReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [showApplyConfirm, setShowApplyConfirm] = useState(false);
  const finalizedRef = useRef(false);

  // Dispatcharr base URL drives the logo-miss banner's "Fix in Dispatcharr"
  // link (bead .19). Best-effort: the banner omits the link if unknown.
  const [dispatcharrUrl, setDispatcharrUrl] = useState('');
  useEffect(() => {
    let active = true;
    api.getSettings().then((s) => { if (active) setDispatcharrUrl(s.url ?? ''); }).catch(() => {});
    return () => { active = false; };
  }, []);

  const progress = useRestoreProgress({ taskId: restoreTaskId });
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
      const res = await api.startDbasRestore(file, apply, isEncrypted ? passphrase : undefined);
      setRestoreTaskId(res.task_id);
      setStep('restoring');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Restore failed');
    } finally {
      setBusy(false);
    }
  }, [file, isEncrypted, passphrase]);

  // On terminal, read the RestoreReport (or sanitized failure) from task
  // history. The task sets progress='completed' BEFORE the engine writes the
  // execution row with details, so retry briefly until the terminal row lands.
  useEffect(() => {
    if (step !== 'restoring' || !restoreTaskId) return;
    if (!progress.isComplete && !progress.isError) return;
    if (finalizedRef.current) return;
    finalizedRef.current = true;

    let cancelled = false;
    (async () => {
      let report: RestoreReport | null = null;
      let failMsg: string | null = null;
      for (let i = 0; i < 6 && !cancelled; i++) {
        try {
          const { history } = await api.getTaskHistory(DBAS_RESTORE_TASK_ID, 1);
          const exec = history?.[0];
          if (exec && (exec.status === 'completed' || exec.status === 'failed')) {
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
        setStep('results');
      } else {
        // Sanitized failure (wrong passphrase / corrupt / unsupported version /
        // orchestration error). Back to configure so the operator can retry.
        setError(failMsg || 'Restore failed');
        setStep('configure');
      }
    })();
    return () => { cancelled = true; };
  }, [step, restoreTaskId, progress.isComplete, progress.isError]);

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
                <button
                  className="modal-btn modal-btn-primary"
                  disabled={busy}
                  onClick={() => setShowApplyConfirm(true)}
                >
                  Apply these changes
                </button>
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
