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
 * Bulk actions (GH #642 / bead enhancedchannelmanager-ixcf1) reuse those same
 * endpoints sequentially. Sequential execution prevents a bulk click from
 * amplifying concurrent Dispatcharr mutations; failures do not stop later
 * rows, and only successful rows are removed.
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
  const [totalRows, setTotalRows] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Per-row in-flight + error tracking — the operator may have multiple
  // rows in different action states, so we key by row id rather than a
  // single page-wide "submitting" flag.
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [rowBusy, setRowBusy] = useState<Record<number, boolean>>({});
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkProgress, setBulkProgress] = useState<{
    scope: 'all' | 'selected';
    operation: 'Merge' | 'Clear';
    current: number;
    total: number;
  } | null>(null);

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
      setTotalRows(response.total);
      const loadedIds = new Set(response.merges.map((row) => row.id));
      setSelectedIds((previous) => {
        const next = new Set([...previous].filter((id) => loadedIds.has(id)));
        return next.size === previous.size ? previous : next;
      });
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
        setTotalRows((prev) => Math.max(0, prev - 1));
        setSelectedIds((prev) => {
          if (!prev.has(rowId)) return prev;
          const next = new Set(prev);
          next.delete(rowId);
          return next;
        });
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

  const bulkBusy = bulkProgress !== null;
  const anyRowBusy = Object.keys(rowBusy).length > 0;
  const actionsDisabled = loading || bulkBusy || anyRowBusy;

  const toggleSelected = useCallback((rowId: number) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(rowId)) next.delete(rowId);
      else next.add(rowId);
      return next;
    });
  }, []);

  const getAllPendingRows = useCallback(async (): Promise<PendingMergeRecord[]> => {
    if (totalRows <= rows.length) return rows;

    const allRows: PendingMergeRecord[] = [];
    const pageSize = 200;
    const totalPages = Math.ceil(totalRows / pageSize);
    for (let page = 1; page <= totalPages; page += 1) {
      const response = await api.getPendingMerges({
        status: 'pending',
        page,
        pageSize,
      });
      allRows.push(...response.merges);
    }
    return allRows;
  }, [rows, totalRows]);

  const runBulkAction = useCallback(
    async (
      scope: 'all' | 'selected',
      operation: 'Merge' | 'Clear',
      action: (id: number) => Promise<unknown>,
    ) => {
      if (bulkBusy || anyRowBusy) return;

      let targets =
        scope === 'selected'
          ? rows.filter((row) => selectedIds.has(row.id))
          : rows;
      if (targets.length === 0) return;

      if (scope === 'all' && totalRows > rows.length) {
        setLoading(true);
        try {
          targets = await getAllPendingRows();
        } catch (err) {
          const detail =
            err instanceof Error ? err.message : 'Failed to load all pending merges';
          logger.error('PendingMergesPage: failed to prepare bulk action', err);
          setLoadError(detail);
          return;
        } finally {
          setLoading(false);
        }
      }

      const count = targets.length;
      const scopeLabel = scope === 'selected' ? ' selected' : '';
      const consequence =
        operation === 'Merge'
          ? 'This will attach each incoming stream to its candidate channel.'
          : 'This will dismiss each candidate so it can be created as a new channel.';
      if (
        !window.confirm(
          `${operation} ${count}${scopeLabel} pending merge${count === 1 ? '' : 's'}? ${consequence}`,
        )
      ) {
        return;
      }

      setBulkProgress({ scope, operation, current: 1, total: count });
      setRowErrors((previous) => {
        const next = { ...previous };
        targets.forEach((row) => delete next[row.id]);
        return next;
      });
      setRowBusy((previous) => ({
        ...previous,
        ...Object.fromEntries(targets.map((row) => [row.id, true])),
      }));

      for (let index = 0; index < targets.length; index += 1) {
        const row = targets[index];
        setBulkProgress({ scope, operation, current: index + 1, total: count });
        try {
          await action(row.id);
          setRows((previous) => previous.filter((item) => item.id !== row.id));
          setTotalRows((previous) => Math.max(0, previous - 1));
          setSelectedIds((previous) => {
            if (!previous.has(row.id)) return previous;
            const next = new Set(previous);
            next.delete(row.id);
            return next;
          });
        } catch (err) {
          const detail =
            err instanceof Error ? err.message : `${operation} failed`;
          logger.error(
            'PendingMergesPage: bulk %s failed for row %s',
            operation,
            row.id,
            err,
          );
          setRowErrors((previous) => ({ ...previous, [row.id]: detail }));
          setSelectedIds((previous) => new Set(previous).add(row.id));
        }
      }

      setRowBusy((previous) => {
        const next = { ...previous };
        targets.forEach((row) => delete next[row.id]);
        return next;
      });
      setBulkProgress(null);
    },
    [
      anyRowBusy,
      bulkBusy,
      getAllPendingRows,
      rows,
      selectedIds,
      totalRows,
    ],
  );

  return (
    <div className="pending-merges-page">
      <div className="pending-merges-header">
        <h2>Pending Merges</h2>
        <button
          type="button"
          className="btn-secondary"
          onClick={loadRows}
          disabled={actionsDisabled}
          title="Reload pending merges"
        >
          <span className={`material-icons ${loading ? 'spinning-cw' : ''}`}>refresh</span>
          Refresh
        </button>
      </div>

      {rows.length > 0 && (
        <div className="pending-merges-bulk-toolbar" aria-label="Bulk actions">
          <span className="pending-merges-selection-count" aria-live="polite">
            {selectedIds.size > 0 ? `${selectedIds.size} selected` : 'Select pending merges'}
          </span>
          <div className="pending-merges-bulk-buttons">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setSelectedIds(new Set(rows.map((row) => row.id)))}
              disabled={actionsDisabled || selectedIds.size === rows.length}
            >
              Select all
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setSelectedIds(new Set())}
              disabled={actionsDisabled || selectedIds.size === 0}
            >
              Deselect all
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() =>
                runBulkAction('selected', 'Clear', api.dismissPendingMerge)
              }
              disabled={actionsDisabled || selectedIds.size === 0}
            >
              {bulkProgress?.operation === 'Clear' &&
              bulkProgress.scope === 'selected'
                ? `Clearing ${bulkProgress.current} of ${bulkProgress.total}`
                : 'Clear selected'}
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={() =>
                runBulkAction('selected', 'Merge', api.acceptPendingMerge)
              }
              disabled={actionsDisabled || selectedIds.size === 0}
            >
              {bulkProgress?.operation === 'Merge' &&
              bulkProgress.scope === 'selected'
                ? `Merging ${bulkProgress.current} of ${bulkProgress.total}`
                : 'Merge selected'}
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => runBulkAction('all', 'Clear', api.dismissPendingMerge)}
              disabled={actionsDisabled}
            >
              {bulkProgress?.operation === 'Clear' && bulkProgress.scope === 'all'
                ? `Clearing ${bulkProgress.current} of ${bulkProgress.total}`
                : 'Clear all'}
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={() => runBulkAction('all', 'Merge', api.acceptPendingMerge)}
              disabled={actionsDisabled}
            >
              {bulkProgress?.operation === 'Merge' && bulkProgress.scope === 'all'
                ? `Merging ${bulkProgress.current} of ${bulkProgress.total}`
                : 'Merge all'}
            </button>
          </div>
        </div>
      )}

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
                  <input
                    type="checkbox"
                    className="pending-merges-select"
                    checked={selectedIds.has(row.id)}
                    onChange={() => toggleSelected(row.id)}
                    disabled={actionsDisabled}
                    aria-label={`Select ${row.stream_name}`}
                  />
                  <div className="pending-merges-stream">
                    <label className="pending-merges-label">Incoming stream</label>
                    <span className="pending-merges-stream-name">{row.stream_name}</span>
                  </div>
                  <div className="pending-merges-candidate">
                    <label className="pending-merges-label">Candidate channel</label>
                    <span className="pending-merges-candidate-row">
                      {row.candidate_channel_name ? (
                        <span className="pending-merges-candidate-identity">
                          <span
                            className="pending-merges-candidate-name"
                            data-testid="pending-merges-candidate-name"
                          >
                            {row.candidate_channel_number != null &&
                              `#${row.candidate_channel_number} `}
                            {row.candidate_channel_name}
                          </span>
                          {row.candidate_channel_group_name && (
                            <span className="pending-merges-candidate-group">
                              {row.candidate_channel_group_name}
                            </span>
                          )}
                          <span
                            className="pending-merges-candidate-id"
                            title={`Dispatcharr channel id ${row.candidate_channel_id}`}
                          >
                            id {row.candidate_channel_id}
                          </span>
                        </span>
                      ) : (
                        <span
                          className="pending-merges-candidate-missing"
                          role="status"
                        >
                          Channel no longer exists (id {row.candidate_channel_id})
                        </span>
                      )}
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
                      disabled={busy || bulkBusy}
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
                      disabled={busy || bulkBusy}
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
