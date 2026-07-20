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
import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../../services/api';
import type { PendingMergeRecord } from '../../services/api';
import { logger } from '../../utils/logger';
import { ModalOverlay } from '../ModalOverlay';
import './PendingMergesPage.css';

const PAGE_SIZE = 50;
const EXACT_MATCH_THRESHOLD = 1.0;
const BULK_PAGE_SIZE = 200;
const MAX_BULK_PAGES = 100;
const MAX_SNAPSHOT_PASSES = 4;
const SNAPSHOT_ERROR =
  'The pending merges queue kept changing while ECM prepared this action. Nothing was changed. Wait for the current refresh to finish, then try again.';

type BulkScope = 'all' | 'selected';
type BulkOperation = 'Merge' | 'Clear';

interface BulkIntent {
  scope: BulkScope;
  operation: BulkOperation;
  targets: PendingMergeRecord[];
}

interface BulkProgress {
  scope: BulkScope;
  operation: BulkOperation;
  completed: number;
  total: number;
  failures: number;
  phase: 'running' | 'stopping' | 'stopped' | 'completed';
}

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
  const selectedIdsRef = useRef(selectedIds);
  const [bulkIntent, setBulkIntent] = useState<BulkIntent | null>(null);
  const [bulkProgress, setBulkProgress] = useState<BulkProgress | null>(null);
  const bulkLockRef = useRef(false);
  const confirmingRef = useRef(false);
  const stopRequestedRef = useRef(false);
  const bulkTriggerRef = useRef<HTMLButtonElement | null>(null);
  const bulkDialogRef = useRef<HTMLDivElement | null>(null);
  const bulkCancelRef = useRef<HTMLButtonElement | null>(null);
  const restoreBulkFocusRef = useRef(false);

  useEffect(() => {
    selectedIdsRef.current = selectedIds;
  }, [selectedIds]);

  const loadRows = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const response = await api.getPendingMerges({
        status: 'pending',
        page: 1,
        pageSize: PAGE_SIZE,
      });
      let refreshedRows = response.merges;
      // A refresh while records are selected must reconcile those selections
      // against the whole queue, not just page one. Otherwise an off-page
      // selected failure can disappear from the retry surface.
      if (selectedIdsRef.current.size > 0 && response.total > response.merges.length) {
        const allRows = new Map<number, PendingMergeRecord>();
        let observedTotal = response.total;
        for (let page = 1; page <= MAX_BULK_PAGES; page += 1) {
          const pageResponse = await api.getPendingMerges({
            status: 'pending',
            page,
            pageSize: BULK_PAGE_SIZE,
          });
          observedTotal = Math.max(observedTotal, pageResponse.total);
          pageResponse.merges.forEach((row) => allRows.set(row.id, row));
          const expectedPages = Math.ceil(observedTotal / BULK_PAGE_SIZE);
          if (
            pageResponse.merges.length === 0 ||
            (page >= expectedPages &&
              (allRows.size >= observedTotal ||
                pageResponse.merges.length < BULK_PAGE_SIZE))
          ) {
            break;
          }
        }
        refreshedRows = [...allRows.values()];
      }
      setRows(refreshedRows);
      setTotalRows(response.total);
      const loadedIds = new Set(refreshedRows.map((row) => row.id));
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

  const bulkBusy =
    bulkProgress?.phase === 'running' || bulkProgress?.phase === 'stopping';
  const anyRowBusy = Object.keys(rowBusy).length > 0;
  const actionsDisabled = loading || bulkBusy || bulkIntent !== null || anyRowBusy;

  const toggleSelected = useCallback((rowId: number) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(rowId)) next.delete(rowId);
      else next.add(rowId);
      return next;
    });
  }, []);

  const getAllPendingRows = useCallback(async (): Promise<PendingMergeRecord[]> => {
    let previousFingerprint: string | null = null;

    // Offset pagination can skip IDs when the queue contracts between pages.
    // Re-reading from page one until two complete passes agree converts that
    // race into either a stable snapshot or an explicit, mutation-free error.
    for (let pass = 1; pass <= MAX_SNAPSHOT_PASSES; pass += 1) {
      const passRows = new Map<number, PendingMergeRecord>();
      const passTotals = new Set<number>();
      let finalTotal = 0;
      let complete = false;

      for (let page = 1; page <= MAX_BULK_PAGES; page += 1) {
        const response = await api.getPendingMerges({
          status: 'pending',
          page,
          pageSize: BULK_PAGE_SIZE,
        });
        finalTotal = response.total;
        passTotals.add(response.total);
        response.merges.forEach((row) => passRows.set(row.id, row));

        const expectedPages = Math.max(
          1,
          Math.ceil(response.total / BULK_PAGE_SIZE),
        );
        if (expectedPages > MAX_BULK_PAGES) break;
        if (page >= expectedPages) {
          complete =
            passTotals.size === 1 && passRows.size === response.total;
          break;
        }
      }

      if (!complete) {
        previousFingerprint = null;
        continue;
      }

      const fingerprint = [...passRows.keys()]
        .sort((left, right) => left - right)
        .join(',');
      if (
        previousFingerprint !== null &&
        fingerprint === previousFingerprint &&
        passRows.size === finalTotal
      ) {
        return [...passRows.values()];
      }
      previousFingerprint = fingerprint;
    }

    throw new Error(SNAPSHOT_ERROR);
  }, []);

  const handleSelectAll = useCallback(async () => {
    if (actionsDisabled) return;
    setLoading(true);
    setLoadError(null);
    try {
      const allRows = await getAllPendingRows();
      setRows(allRows);
      setSelectedIds(new Set(allRows.map((row) => row.id)));
    } catch (err) {
      const detail =
        err instanceof Error ? err.message : 'Failed to load all pending merges';
      logger.error('PendingMergesPage: failed to select whole queue', err);
      setLoadError(detail);
    } finally {
      setLoading(false);
    }
  }, [actionsDisabled, getAllPendingRows]);

  const requestBulkAction = useCallback(
    async (
      scope: BulkScope,
      operation: BulkOperation,
      trigger?: HTMLButtonElement,
    ) => {
      if (bulkLockRef.current || bulkBusy || anyRowBusy) return;
      bulkLockRef.current = true;
      bulkTriggerRef.current = trigger ?? null;
      setBulkProgress(null);

      try {
        let targets =
          scope === 'selected'
            ? rows.filter((row) => selectedIds.has(row.id))
            : rows;
        if (scope === 'all') {
          setLoading(true);
          targets = await getAllPendingRows();
        }
        if (targets.length === 0) {
          bulkLockRef.current = false;
          return;
        }
        if (scope === 'all') {
          // Materialize the stable snapshot before confirmation. This keeps
          // progress, remaining records, cancellation, and off-page failures
          // anchored to one visible set throughout the sweep.
          setRows(targets);
          setTotalRows(targets.length);
        }
        setBulkIntent({ scope, operation, targets });
      } catch (err) {
        const detail =
          err instanceof Error ? err.message : 'Failed to load all pending merges';
        logger.error('PendingMergesPage: failed to prepare bulk action', err);
        setLoadError(detail);
        bulkLockRef.current = false;
      } finally {
        setLoading(false);
      }
    },
    [anyRowBusy, bulkBusy, getAllPendingRows, rows, selectedIds],
  );

  useEffect(() => {
    if (!bulkIntent) {
      if (restoreBulkFocusRef.current) {
        restoreBulkFocusRef.current = false;
        bulkTriggerRef.current?.focus();
      }
      return;
    }
    bulkCancelRef.current?.focus();

    const handleTab = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const dialog = bulkDialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
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
    return () => document.removeEventListener('keydown', handleTab);
  }, [bulkIntent]);

  const runConfirmedBulkAction = useCallback(async (intent: BulkIntent) => {
    if (confirmingRef.current) return;
    confirmingRef.current = true;
    stopRequestedRef.current = false;
    setBulkIntent(null);
    const { scope, operation, targets } = intent;
    const count = targets.length;
    let completed = 0;
    let failures = 0;
    setBulkProgress({
      scope,
      operation,
      completed,
      total: count,
      failures,
      phase: 'running',
    });
      setRowErrors((previous) => {
        const next = { ...previous };
        targets.forEach((row) => delete next[row.id]);
        return next;
      });
      setRowBusy((previous) => ({
        ...previous,
        ...Object.fromEntries(targets.map((row) => [row.id, true])),
      }));

    try {
      for (let index = 0; index < targets.length; index += 1) {
        if (stopRequestedRef.current) break;
        const row = targets[index];
        try {
          const action =
            operation === 'Merge'
              ? api.acceptPendingMerge
              : api.dismissPendingMerge;
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
          failures += 1;
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
          // All-queue snapshots may contain records that were never on the
          // visible first page. Pin a failed record into the rendered list so
          // its verbatim error and per-row retry controls remain reachable.
          setRows((previous) =>
            previous.some((item) => item.id === row.id)
              ? previous
              : [...previous, row],
          );
        }
        completed += 1;
        setBulkProgress({
          scope,
          operation,
          completed,
          total: count,
          failures,
          phase: stopRequestedRef.current ? 'stopping' : 'running',
        });
      }

      const stopped = stopRequestedRef.current && completed < count;
      if (stopped) {
        const unprocessedIds = targets
          .slice(completed)
          .map((row) => row.id);
        setSelectedIds((previous) => new Set([...previous, ...unprocessedIds]));
      }
      setRowBusy((previous) => {
        const next = { ...previous };
        targets.forEach((row) => delete next[row.id]);
        return next;
      });
      setBulkProgress({
        scope,
        operation,
        completed,
        total: count,
        failures,
        phase: stopped ? 'stopped' : 'completed',
      });
    } finally {
      confirmingRef.current = false;
      bulkLockRef.current = false;
    }
  }, []);

  const cancelBulkIntent = useCallback(() => {
    restoreBulkFocusRef.current = true;
    setBulkIntent(null);
    bulkLockRef.current = false;
  }, []);

  const stopBulkAction = useCallback(() => {
    stopRequestedRef.current = true;
    setBulkProgress((previous) =>
      previous && previous.phase === 'running'
        ? { ...previous, phase: 'stopping' }
        : previous,
    );
  }, []);

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
              onClick={handleSelectAll}
              disabled={
                actionsDisabled ||
                (selectedIds.size === totalRows && rows.length >= totalRows)
              }
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
              onClick={(event) =>
                requestBulkAction('selected', 'Clear', event.currentTarget)
              }
              disabled={actionsDisabled || selectedIds.size === 0}
            >
              Clear selected
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={(event) =>
                requestBulkAction('selected', 'Merge', event.currentTarget)
              }
              disabled={actionsDisabled || selectedIds.size === 0}
            >
              Merge selected
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={(event) =>
                requestBulkAction('all', 'Clear', event.currentTarget)
              }
              disabled={actionsDisabled}
            >
              Clear all
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={(event) =>
                requestBulkAction('all', 'Merge', event.currentTarget)
              }
              disabled={actionsDisabled}
            >
              Merge all
            </button>
          </div>
        </div>
      )}

      {bulkProgress && (
        <div
          className={`pending-merges-bulk-progress pending-merges-bulk-progress-${bulkProgress.phase}`}
          role="status"
          aria-live="polite"
          aria-label="Bulk action progress"
        >
          <span className="material-icons" aria-hidden="true">
            {bulkProgress.phase === 'completed'
              ? bulkProgress.failures > 0
                ? 'warning'
                : 'check_circle'
              : bulkProgress.phase === 'stopped'
                ? 'pause_circle'
                : 'sync'}
          </span>
          <span>
            {bulkProgress.phase === 'running' &&
              `${bulkProgress.operation === 'Merge' ? 'Merged' : 'Cleared'} ${bulkProgress.completed} of ${bulkProgress.total}`}
            {bulkProgress.phase === 'stopping' &&
              `Stopping after the current item… ${bulkProgress.completed} of ${bulkProgress.total} processed.`}
            {bulkProgress.phase === 'stopped' &&
              `Stopped after ${bulkProgress.completed} of ${bulkProgress.total}${bulkProgress.failures ? ` with ${bulkProgress.failures} failures` : ''}. ${bulkProgress.total - bulkProgress.completed} remaining.`}
            {bulkProgress.phase === 'completed' &&
              `Completed ${bulkProgress.completed} of ${bulkProgress.total}${bulkProgress.failures ? ` with ${bulkProgress.failures} failures` : ''}.`}
          </span>
          {(bulkProgress.phase === 'running' ||
            bulkProgress.phase === 'stopping') && (
            <button
              type="button"
              className="btn-secondary"
              onClick={stopBulkAction}
              disabled={bulkProgress.phase === 'stopping'}
            >
              Stop
            </button>
          )}
        </div>
      )}

      {loadError && (
        <div className="error-banner" role="alert">
          <span className="material-icons">error</span>
          <span>{loadError}</span>
        </div>
      )}

      {!loading && rows.length === 0 && totalRows === 0 && !loadError && (
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
                      disabled={busy || actionsDisabled}
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
                      disabled={busy || actionsDisabled}
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

      {bulkIntent && (
        <ModalOverlay
          onClose={cancelBulkIntent}
          role="dialog"
          aria-modal="true"
          aria-labelledby="pending-merges-bulk-confirm-title"
        >
          <div ref={bulkDialogRef} className="modal-container modal-sm">
            <div className="modal-header">
              <h2 id="pending-merges-bulk-confirm-title">Confirm bulk action</h2>
              <button
                type="button"
                className="modal-close"
                onClick={cancelBulkIntent}
                aria-label="Close"
              >
                <span className="material-icons">close</span>
              </button>
            </div>
            <div className="modal-body">
              <p>
                This will {bulkIntent.operation === 'Merge' ? 'merge' : 'clear'}{' '}
                <strong>
                  {bulkIntent.targets.length} pending{' '}
                  {bulkIntent.targets.length === 1 ? 'merge' : 'merges'}
                </strong>
                {bulkIntent.scope === 'selected' ? ' currently selected' : ''}.
              </p>
              <p>
                {bulkIntent.operation === 'Merge'
                  ? 'Each incoming stream will be attached to its candidate channel.'
                  : 'These candidates will be dismissed so the streams can create new channels on a future trigger.'}
              </p>
              <p className="pending-merges-confirm-warning">
                This action cannot be undone.
              </p>
            </div>
            <div className="modal-footer">
              <button
                ref={bulkCancelRef}
                type="button"
                className="modal-btn modal-btn-secondary"
                onClick={cancelBulkIntent}
              >
                Cancel
              </button>
              <button
                type="button"
                className="modal-btn modal-btn-danger"
                onClick={() => runConfirmedBulkAction(bulkIntent)}
              >
                Confirm {bulkIntent.operation.toLowerCase()}
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}
    </div>
  );
}

export default PendingMergesPage;
