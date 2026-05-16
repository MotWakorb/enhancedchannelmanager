/**
 * PendingMergesPage — operator-facing queue view for stream-to-channel
 * deduplication candidates (BD-J / bd-gfxrz, ADR-008 §D1).
 *
 * Where it lives: this page is a SUB-VIEW of the Channel Manager tab, not a
 * new top-level tab. The top tab bar is already at 10 entries, and the UX-
 * ratified spec in the parent epic (bd-1v4ht) places this surface in the
 * Channel Manager subnav with a count badge that appears only when there is
 * something to act on (or when the operator is already on this page).
 *
 * Data source:
 *   GET  /api/channel-merges?status=pending&page=1&page_size=50  (BD-E list)
 *   POST /api/channel-merges/{id}/accept                         (BD-E merge)
 *   POST /api/channel-merges/{id}/dismiss                        (BD-E dismiss)
 *
 * Per-row affordances:
 *   - "Merge" — accept the candidate. Idempotent on the backend per ADR-008
 *     §D1; on success we optimistically remove the row from the local list.
 *   - "Create New" — dismiss the candidate (the actual channel-creation path
 *     is the operator's next trigger — drag-drop, Add Stream, or the next
 *     M3U refresh — and `dismiss` is a pure ECM-side state flip plus audit
 *     row per ADR-008 §D6). Same optimistic remove.
 *
 * On error, the backend's `detail` string is surfaced verbatim in an inline
 * banner (matching the bd-7j6v1 / bd-9q9z0 pattern); the row stays in place
 * so the operator can retry or pick the other action.
 *
 * Bulk-action buttons ("Resolve All …") are deliberately NOT rendered — the
 * parent epic explicitly deferred them to backlog bead
 * `enhancedchannelmanager-qpgsx` (P3) per the PO ratification. The hard
 * confidence floor (ADR-008 §D2) is the architectural backstop against
 * mass-destruction footguns, but a low-default-threshold operator could
 * still queue many low-quality rows; a "Resolve All" button is one click
 * away from a regret. v0.17.1 ships only per-row resolution.
 */
import { useCallback, useEffect, useState } from 'react';
import * as api from '../../services/api';
import type { PendingMergeRecord } from '../../services/api';
import { logger } from '../../utils/logger';
import './PendingMergesPage.css';

const PAGE_SIZE = 50;
const EXACT_MATCH_THRESHOLD = 1.0;

/** Format a 0.0–1.0 confidence as an integer-percent badge string. */
function formatConfidencePercent(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

export function PendingMergesPage() {
  const [rows, setRows] = useState<PendingMergeRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Per-row in-flight + error tracking — the operator may have multiple
  // rows in different action states, so we key by row id rather than a
  // single page-wide "submitting" flag.
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [rowBusy, setRowBusy] = useState<Record<number, boolean>>({});

  const loadRows = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const response = await api.getPendingMerges({
        status: 'pending',
        page: 1,
        pageSize: PAGE_SIZE,
      });
      setRows(response.merges);
    } catch (err) {
      const detail = err instanceof Error ? err.message : 'Failed to load pending merges';
      logger.error('PendingMergesPage: failed to load queue', err);
      setLoadError(detail);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRows();
  }, [loadRows]);

  const handleAction = useCallback(
    async (
      rowId: number,
      action: (id: number) => Promise<unknown>,
      operationLabel: string,
    ) => {
      setRowErrors((prev) => {
        const next = { ...prev };
        delete next[rowId];
        return next;
      });
      setRowBusy((prev) => ({ ...prev, [rowId]: true }));
      try {
        await action(rowId);
        // Optimistic remove — the backend has flipped the row to a terminal
        // state and the list endpoint defaults to status='pending', so the
        // row would not be returned on the next reload anyway. Removing it
        // here avoids a round-trip and a UI flash.
        setRows((prev) => prev.filter((r) => r.id !== rowId));
      } catch (err) {
        const detail =
          err instanceof Error ? err.message : `${operationLabel} failed`;
        logger.error('PendingMergesPage: %s failed for row %s', operationLabel, rowId, err);
        setRowErrors((prev) => ({ ...prev, [rowId]: detail }));
      } finally {
        setRowBusy((prev) => {
          const next = { ...prev };
          delete next[rowId];
          return next;
        });
      }
    },
    [],
  );

  const handleMerge = useCallback(
    (rowId: number) => handleAction(rowId, api.acceptPendingMerge, 'Merge'),
    [handleAction],
  );

  const handleCreateNew = useCallback(
    (rowId: number) => handleAction(rowId, api.dismissPendingMerge, 'Dismiss'),
    [handleAction],
  );

  return (
    <div className="pending-merges-page">
      <div className="pending-merges-header">
        <h2>Pending Merges</h2>
        <button
          type="button"
          className="btn-secondary"
          onClick={loadRows}
          disabled={loading}
          title="Reload pending merges"
        >
          <span className={`material-icons ${loading ? 'spinning-cw' : ''}`}>refresh</span>
          Refresh
        </button>
      </div>

      {loadError && (
        <div className="error-banner" role="alert">
          <span className="material-icons">error</span>
          <span>{loadError}</span>
        </div>
      )}

      {!loading && rows.length === 0 && !loadError && (
        <div className="empty-state">
          <span className="material-icons">inbox</span>
          <h3>No pending merges</h3>
          <p>
            Pending Merges will appear here after an M3U refresh detects potential
            duplicates.
          </p>
        </div>
      )}

      {rows.length > 0 && (
        <ul className="pending-merges-list" aria-label="Pending merges">
          {rows.map((row) => {
            const isExact = row.confidence >= EXACT_MATCH_THRESHOLD;
            const busy = !!rowBusy[row.id];
            const rowError = rowErrors[row.id];
            return (
              <li key={row.id} className="pending-merges-row">
                <div className="pending-merges-row-main">
                  <div className="pending-merges-stream">
                    <label className="pending-merges-label">Incoming stream</label>
                    <span className="pending-merges-stream-name">{row.stream_name}</span>
                  </div>
                  <div className="pending-merges-candidate">
                    <label className="pending-merges-label">Candidate channel</label>
                    <span className="pending-merges-candidate-row">
                      <span className="pending-merges-candidate-id">
                        {row.candidate_channel_id}
                      </span>
                      {isExact ? (
                        <span
                          className="confidence-badge pending-merges-exact-badge"
                          aria-label="Exact match"
                        >
                          Exact match
                        </span>
                      ) : (
                        <span
                          className="confidence-badge pending-merges-confidence-badge"
                          aria-label={`Confidence: ${Math.round(row.confidence * 100)} percent`}
                        >
                          {formatConfidencePercent(row.confidence)} match
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="pending-merges-actions">
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={() => handleCreateNew(row.id)}
                      disabled={busy}
                    >
                      Create New
                    </button>
                    <button
                      type="button"
                      className={
                        isExact
                          ? 'btn-primary pending-merges-merge-btn'
                          : 'btn-secondary pending-merges-merge-btn'
                      }
                      onClick={() => handleMerge(row.id)}
                      disabled={busy}
                    >
                      {busy ? 'Working...' : 'Merge'}
                    </button>
                  </div>
                </div>
                {rowError && (
                  <div
                    className="error-banner pending-merges-row-error"
                    role="alert"
                  >
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

export default PendingMergesPage;
