import { useEffect, useId, useRef, useState } from 'react';
import type { EPGSource } from '../types';
import * as api from '../services/api';
import { ModalOverlay } from './ModalOverlay';

interface Props {
  isOpen: boolean;
  sources: EPGSource[];
  onClose: () => void;
  onApplied: (result: Awaited<ReturnType<typeof api.applyGuideMigration>>) => void;
}

const STATUS_LABELS: Record<api.GuideMigrationStatus, string> = {
  ready: 'Ready',
  already_target: 'Already on target',
  unassigned: 'No guide assigned',
  missing_lcn: 'LCN not found',
  missing_target: 'No target match',
  ambiguous_target: 'Ambiguous target',
  unsupported_origin: 'Unsupported source type',
};

const APPLY_STATUS_LABELS: Record<api.GuideMigrationApplyStatus, string> = {
  updated: 'Updated and audited',
  updated_audit_failed: 'Updated; audit record failed',
  ambiguous_target: 'Skipped: target is no longer unique',
  unsupported_origin: 'Skipped: source type is unsupported',
  semantic_drift: 'Skipped: guide mapping changed',
  changed_since_preview: 'Skipped: channel changed since preview',
  failed: 'Update failed',
};

export function GuideMigrationModal({
  isOpen,
  sources,
  onClose,
  onApplied,
}: Props) {
  const eligibleSources = sources.filter(
    (source) =>
      source.source_type === 'xmltv' ||
      source.source_type === 'schedules_direct'
  );
  const [targetId, setTargetId] = useState('');
  const [preview, setPreview] = useState<api.GuideMigrationPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobProgress, setJobProgress] =
    useState<api.GuideMigrationJobStatus | null>(null);
  const [applyResult, setApplyResult] = useState<Awaited<
    ReturnType<typeof api.applyGuideMigration>
  > | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const targetRef = useRef<HTMLSelectElement>(null);
  const previewButtonRef = useRef<HTMLButtonElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const applyControllerRef = useRef<AbortController | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();

  useEffect(
    () => () => {
      applyControllerRef.current?.abort();
    },
    []
  );

  useEffect(() => {
    if (!isOpen) {
      applyControllerRef.current?.abort();
      setTargetId('');
      setPreview(null);
      setConfirmed(false);
      setError(null);
      setBusy(false);
      setApplyResult(null);
      setJobProgress(null);
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    const frame = window.requestAnimationFrame(() => targetRef.current?.focus());
    const handleTab = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !containerRef.current) return;
      const focusable = Array.from(
        containerRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), select:not([disabled]), input:not([disabled])'
        )
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handleTab);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener('keydown', handleTab);
      previousFocusRef.current?.focus();
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const runPreview = async () => {
    setBusy(true);
    setError(null);
    setPreview(null);
    setConfirmed(false);
    setApplyResult(null);
    setJobProgress(null);
    try {
      setPreview(await api.previewGuideMigration(Number(targetId)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Guide migration preview failed');
      window.requestAnimationFrame(() => errorRef.current?.focus());
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!preview) return;
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    applyControllerRef.current?.abort();
    applyControllerRef.current = controller;
    try {
      const result = await api.applyGuideMigration(
        preview,
        setJobProgress,
        controller.signal
      );
      onApplied(result);
      setApplyResult(result);
      setBusy(false);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message = err instanceof Error ? err.message : 'Guide migration failed';
      setError(message);
      if (/preview.*(expired|invalid|again)/i.test(message)) {
        setPreview(null);
        setConfirmed(false);
        window.requestAnimationFrame(() => previewButtonRef.current?.focus());
      } else {
        window.requestAnimationFrame(() => errorRef.current?.focus());
      }
      setBusy(false);
    } finally {
      if (applyControllerRef.current === controller) {
        applyControllerRef.current = null;
      }
    }
  };

  const ready = preview?.counts.ready ?? 0;
  return (
    <ModalOverlay
      onClose={busy ? () => undefined : onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <div ref={containerRef} className="modal-container modal-lg guide-migration-modal">
        <div className="modal-header">
          <h2 id={titleId}>Migrate channel guides</h2>
          <button
            className="modal-close-btn"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
          >
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </div>
        <div className="modal-body">
          <p>
            Preview LCN/Gracenote matches before changing any channel. Missing
            or ambiguous matches are never overwritten.
          </p>
          <div className="modal-form-group">
            <label htmlFor="guide-migration-target">Target EPG source</label>
            <select
              id="guide-migration-target"
              ref={targetRef}
              value={targetId}
              onChange={(event) => {
                setTargetId(event.target.value);
                setPreview(null);
                setConfirmed(false);
              }}
              disabled={busy}
            >
              <option value="">Choose a target…</option>
              {eligibleSources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name} ({source.source_type === 'schedules_direct' ? 'Gracenote' : 'XMLTV'})
                </option>
              ))}
            </select>
          </div>
          <button
            ref={previewButtonRef}
            className="btn-secondary"
            onClick={runPreview}
            disabled={!targetId || busy}
          >
            {busy && !preview ? 'Building preview…' : 'Preview migration'}
          </button>
          {error && (
            <div
              ref={errorRef}
              className="error-banner"
              role="alert"
              tabIndex={-1}
            >
              {error}
            </div>
          )}
          {preview && (
            <>
              <div className="guide-migration-summary" aria-label="Migration summary">
                {Object.entries(preview.counts).map(([status, count]) => (
                  <span key={status}>
                    {STATUS_LABELS[status as api.GuideMigrationStatus]}: {count}
                  </span>
                ))}
              </div>
              <div className="guide-migration-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Channel</th>
                      <th>Current source</th>
                      <th>LCN</th>
                      <th>Target</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((row) => (
                      <tr key={row.channel_id}>
                        <td>{row.channel_name}</td>
                        <td>{row.current_source_name ?? '—'}</td>
                        <td>{row.lcn ?? '—'}</td>
                        <td>{row.target_name ?? '—'}</td>
                        <td>{STATUS_LABELS[row.status]}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {ready > 0 && (
                <label className="guide-migration-confirm">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(event) => setConfirmed(event.target.checked)}
                    disabled={busy}
                  />
                  Change guide assignments for exactly {ready} ready channel{ready === 1 ? '' : 's'}.
                </label>
              )}
            </>
          )}
          {applyResult && (
            <div className="guide-migration-results" role="status">
              <h3>Apply results</h3>
              <p>
                Mutated {applyResult.mutated}; audited {applyResult.updated};
                audit failures {applyResult.audit_failed}; skipped {applyResult.skipped};
                failed {applyResult.failed}.
              </p>
              <ul>
                {applyResult.results.map((result) => {
                  const row = preview?.rows.find(
                    (candidate) => candidate.channel_id === result.channel_id
                  );
                  return (
                    <li key={result.channel_id}>
                      {row?.channel_name ?? `Channel ${result.channel_id}`}: {APPLY_STATUS_LABELS[result.status]}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {busy && jobProgress && (
            <div className="guide-migration-progress" role="status">
              Applied {jobProgress.processed} of {jobProgress.total}; updated{' '}
              {jobProgress.result.updated}, skipped {jobProgress.result.skipped},
              failed {jobProgress.result.failed}. Batch {jobProgress.batch_id}.
            </div>
          )}
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={busy}>
            {applyResult ? 'Done' : 'Cancel'}
          </button>
          <button
            className="btn-primary"
            onClick={apply}
            disabled={!preview || ready === 0 || !confirmed || busy || !!applyResult}
          >
            {busy && preview ? 'Applying…' : `Apply ${ready} migration${ready === 1 ? '' : 's'}`}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
