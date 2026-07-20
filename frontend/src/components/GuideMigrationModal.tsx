import { useEffect, useState } from 'react';
import type { EPGSource } from '../types';
import * as api from '../services/api';
import { ModalOverlay } from './ModalOverlay';

interface Props {
  isOpen: boolean;
  sources: EPGSource[];
  onClose: () => void;
  onApplied: (updated: number, skipped: number, failed: number) => void;
}

const STATUS_LABELS: Record<api.GuideMigrationStatus, string> = {
  ready: 'Ready',
  already_target: 'Already on target',
  unassigned: 'No guide assigned',
  missing_lcn: 'LCN not found',
  missing_target: 'No target match',
  ambiguous_target: 'Ambiguous target',
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

  useEffect(() => {
    if (!isOpen) {
      setTargetId('');
      setPreview(null);
      setConfirmed(false);
      setError(null);
      setBusy(false);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const runPreview = async () => {
    setBusy(true);
    setError(null);
    setPreview(null);
    setConfirmed(false);
    try {
      setPreview(await api.previewGuideMigration(Number(targetId)));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Guide migration preview failed');
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.applyGuideMigration(preview);
      onApplied(result.updated, result.skipped, result.failed);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Guide migration failed');
      setBusy(false);
    }
  };

  const ready = preview?.counts.ready ?? 0;
  return (
    <ModalOverlay onClose={busy ? () => undefined : onClose}>
      <div className="modal-container modal-lg guide-migration-modal">
        <div className="modal-header">
          <h2>Migrate channel guides</h2>
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
            className="btn-secondary"
            onClick={runPreview}
            disabled={!targetId || busy}
          >
            {busy && !preview ? 'Building preview…' : 'Preview migration'}
          </button>
          {error && <div className="alert alert-error" role="alert">{error}</div>}
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
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="btn-primary"
            onClick={apply}
            disabled={!preview || ready === 0 || !confirmed || busy}
          >
            {busy && preview ? 'Applying…' : `Apply ${ready} migration${ready === 1 ? '' : 's'}`}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
