/**
 * EventSyncExclusionsPanel — operator management surface for Event Sync
 * never-attach exclusions (bead ti939.3.5, epic ti939).
 *
 * One row = one standing "never attach this provider stream to that event"
 * order, keyed backend-side on content fingerprints — never channel/stream
 * IDs — so it survives provider refreshes and stream-ID churn. The shared
 * resolver consults these BEFORE the attach band on every run and preview
 * (excluded pairings report as "Excluded by operator" there), and an
 * exclusion outranks a prior review-queue accept for the same pairing.
 *
 * Remove is the undo: the pairing becomes matchable again on the next
 * run/preview. Rows are created from the review queue's "Never attach"
 * action (or via the API); this panel lists and removes them.
 */
import { useCallback, useEffect, useState } from 'react';
import * as api from '../../services/api';
import type { EventSyncExclusionRecord } from '../../types/eventSync';
import { logger } from '../../utils/logger';
import { getDateLocale } from '../../utils/formatting';
import './EventSyncExclusionsPanel.css';

const PAGE_SIZE = 50;

/**
 * Window CustomEvent the review queue fires after its "Never attach"
 * action creates an exclusion — this panel refetches on it, so the new
 * standing order appears without a manual refresh (same `ecm:` event
 * idiom as `ecm:open-task-editor`).
 */
export const EXCLUSIONS_CHANGED_EVENT = 'ecm:event-sync-exclusions-changed';

function formatCreated(epochMs: number): string {
  const parsed = new Date(epochMs);
  return Number.isNaN(parsed.getTime())
    ? '—'
    : parsed.toLocaleString(getDateLocale());
}

export function EventSyncExclusionsPanel() {
  const [rows, setRows] = useState<EventSyncExclusionRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [rowBusy, setRowBusy] = useState<Record<number, boolean>>({});
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [notice, setNotice] = useState<string | null>(null);

  const loadRows = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const response = await api.getEventSyncExclusions({
        page: 1,
        pageSize: PAGE_SIZE,
      });
      setRows(response.exclusions);
      setTotal(response.total);
    } catch (err) {
      const detail =
        err instanceof Error ? err.message : 'Failed to load exclusions';
      logger.error('EventSyncExclusionsPanel: failed to load list', err);
      setLoadError(detail);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRows();
  }, [loadRows]);

  // Refetch when the review queue records a new "Never attach" — the two
  // components share no parent state, so the CustomEvent is the contract.
  useEffect(() => {
    const handler = () => {
      loadRows();
    };
    window.addEventListener(EXCLUSIONS_CHANGED_EVENT, handler);
    return () => window.removeEventListener(EXCLUSIONS_CHANGED_EVENT, handler);
  }, [loadRows]);

  const handleRemove = useCallback(async (row: EventSyncExclusionRecord) => {
    setNotice(null);
    setRowErrors(prev => {
      const next = { ...prev };
      delete next[row.id];
      return next;
    });
    setRowBusy(prev => ({ ...prev, [row.id]: true }));
    try {
      await api.deleteEventSyncExclusion(row.id);
      setRows(prev => prev.filter(r => r.id !== row.id));
      setTotal(prev => Math.max(0, prev - 1));
      setNotice(
        'Exclusion removed — the pairing becomes matchable again on the next run or preview.',
      );
    } catch (err) {
      const detail = err instanceof Error ? err.message : 'Remove failed';
      logger.error(
        'EventSyncExclusionsPanel: remove failed for row %s', row.id, err,
      );
      setRowErrors(prev => ({ ...prev, [row.id]: detail }));
    } finally {
      setRowBusy(prev => {
        const next = { ...prev };
        delete next[row.id];
        return next;
      });
    }
  }, []);

  // Nothing to manage and nothing failed: stay out of the way (the review
  // queue above is the primary surface; an empty exclusion list is the
  // normal state).
  if (!loading && !loadError && rows.length === 0 && !notice) {
    return null;
  }

  return (
    <div
      className="event-sync-exclusions"
      data-testid="event-sync-exclusions"
    >
      <div className="event-sync-exclusions-header">
        <h3>
          Never-attach exclusions
          {total > 0 && (
            <span
              className="event-sync-exclusions-count"
              data-testid="event-sync-exclusions-count"
              aria-label={`${total} standing exclusions`}
            >
              {total}
            </span>
          )}
        </h3>
        <button
          type="button"
          className="btn-secondary"
          onClick={loadRows}
          disabled={loading}
          title="Reload exclusions"
        >
          <span className={`material-icons ${loading ? 'spinning-cw' : ''}`}>refresh</span>
          Refresh
        </button>
      </div>
      <p className="form-hint">
        Standing operator orders: these pairings never attach — on any run or
        preview — until removed. Keyed on the provider string and event
        identity, so they survive provider refreshes. They also override a
        prior review-queue accept for the same pairing.
      </p>

      {loadError && (
        <div className="error-banner" role="alert">
          <span className="material-icons">error</span>
          <span>{loadError}</span>
        </div>
      )}

      {notice && (
        <div className="event-sync-exclusions-notice" role="status">
          <span className="material-icons" aria-hidden="true">info</span>
          <span>{notice}</span>
        </div>
      )}

      {rows.length > 0 && (
        <ul
          className="event-sync-exclusions-list"
          aria-label="Never-attach exclusions"
        >
          {rows.map(row => {
            const ev = row.evidence;
            const busy = !!rowBusy[row.id];
            const rowError = rowErrors[row.id];
            return (
              <li key={row.id} className="event-sync-exclusion-card">
                <div className="event-sync-exclusion-main">
                  <span
                    className="material-icons event-sync-exclusion-icon"
                    aria-hidden="true"
                  >
                    block
                  </span>
                  <div className="event-sync-exclusion-pairing">
                    <span className="event-sync-raw-name">
                      {ev.stream_name ?? `fingerprint ${row.stream_name_hash.slice(0, 12)}…`}
                    </span>
                    <span className="event-sync-exclusion-never" aria-hidden="true">
                      never attaches to
                    </span>
                    <span className="event-sync-raw-name">
                      {ev.master_channel_name ?? row.event_key}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => handleRemove(row)}
                    disabled={busy}
                    title="Remove this exclusion — the pairing becomes matchable again"
                  >
                    <span className="material-icons" aria-hidden="true">delete</span>
                    {busy ? 'Removing...' : 'Remove'}
                  </button>
                </div>
                <div className="event-sync-exclusion-meta">
                  {ev.rule_name && (
                    <span className="event-sync-exclusion-chip">{ev.rule_name}</span>
                  )}
                  {ev.provider && (
                    <span className="event-sync-exclusion-chip">{ev.provider}</span>
                  )}
                  <span className="event-sync-exclusion-chip">
                    Added {formatCreated(row.created_at)}
                  </span>
                  {row.note && (
                    <span className="event-sync-exclusion-note">{row.note}</span>
                  )}
                </div>
                {rowError && (
                  <div className="error-banner event-sync-exclusion-row-error" role="alert">
                    <span className="material-icons">error</span>
                    <span>{rowError}</span>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default EventSyncExclusionsPanel;
