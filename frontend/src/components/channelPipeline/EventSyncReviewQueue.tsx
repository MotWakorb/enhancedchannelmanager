/**
 * EventSyncReviewQueue — operator review surface for ambiguous event_sync
 * matches (bead ti939.3.2, epic ti939).
 *
 * Ambiguous-band matches (including contested ties) from event_sync runs
 * enqueue here instead of being silently skipped. One row = one exact
 * (secondary stream, master channel) PAIRING, keyed backend-side on content
 * fingerprints — never channel/stream IDs — so a decision survives provider
 * refreshes and re-applies whenever the same provider string + event
 * identity recurs.
 *
 * Reuses the Pending Merges review-surface PATTERN (per-row Accept/Reject,
 * optimistic removal, per-row busy/error state) but with PER-CANDIDATE
 * EVIDENCE instead of a single aggregate score: both raw names side by
 * side, parsed titles/times, time delta, and the team-token verdict. An
 * opaque score was the human-factors condition that let the original
 * 1,341-merge incident scale — the operator must see WHY the matcher was
 * unsure. Badges are text + icon, never color alone (event-sync UI
 * accessibility baseline).
 *
 * Accept: records the durable decision (future runs auto-attach the
 * pairing) and attaches the stream immediately when the backend can
 * re-verify its snapshot ids; a deferred attach is surfaced in the info
 * banner and applied by the next pipeline run. Reject: durable suppression
 * — the pairing never attaches and is never asked about again.
 *
 * Never attach (bead ti939.3.5): records a standing operator EXCLUSION for
 * the pairing (visible/removable in the exclusions panel) and closes the
 * question as rejected. Unlike a plain reject, the exclusion is a
 * first-class row the operator can list and undo — and it outranks any
 * later accept for the same fingerprint.
 */
import { useCallback, useEffect, useId, useState } from 'react';
import * as api from '../../services/api';
import type { EventSyncReviewRecord } from '../../types/eventSync';
import { logger } from '../../utils/logger';
import { getDateLocale } from '../../utils/formatting';
import { BAND_META, TEAM_VERDICT_META } from './eventSyncDefaults';
import { EXCLUSIONS_CHANGED_EVENT } from './EventSyncExclusionsPanel';
import { ModalOverlay } from '../ModalOverlay';
import '../ModalBase.css';
import './EventSyncReviewQueue.css';

const PAGE_SIZE = 50;

function formatStart(iso: string | null | undefined): string {
  if (!iso) return '—';
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString(getDateLocale());
}

export function EventSyncReviewQueue() {
  const [rows, setRows] = useState<EventSyncReviewRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Per-row in-flight + error tracking (multiple rows can be mid-action).
  const [rowBusy, setRowBusy] = useState<Record<number, boolean>>({});
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  // Outcome of the last accept (attach-deferred explanations live here).
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [discardIds, setDiscardIds] = useState<number[] | null>(null);
  const [discardBusy, setDiscardBusy] = useState(false);
  const [discardError, setDiscardError] = useState<string | null>(null);
  const discardTitleId = `${useId()}-bulk-discard-title`;

  const loadRows = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const response = await api.getEventSyncReviews({
        status: 'pending',
        page: 1,
        pageSize: PAGE_SIZE,
      });
      setRows(response.reviews);
      setTotal(response.total);
      setSelectedIds(new Set());
    } catch (err) {
      const detail =
        err instanceof Error ? err.message : 'Failed to load event sync reviews';
      logger.error('EventSyncReviewQueue: failed to load queue', err);
      setLoadError(detail);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRows();
  }, [loadRows]);

  const clearRowError = useCallback((rowId: number) => {
    setRowErrors(prev => {
      const next = { ...prev };
      delete next[rowId];
      return next;
    });
  }, []);

  const handleAccept = useCallback(
    async (row: EventSyncReviewRecord) => {
      clearRowError(row.id);
      setNotice(null);
      setRowBusy(prev => ({ ...prev, [row.id]: true }));
      try {
        const outcome = await api.acceptEventSyncReview(row.id);
        // Optimistic removal: the accepted row AND its superseded siblings
        // (same stream fingerprint under the same rule) leave the queue.
        setRows(prev =>
          prev.filter(
            r =>
              r.id !== row.id &&
              !(
                r.rule_id === row.rule_id &&
                r.provider_id === row.provider_id &&
                r.stream_name_hash === row.stream_name_hash
              ),
          ),
        );
        setTotal(prev => Math.max(0, prev - 1 - outcome.superseded_siblings));
        if (outcome.attached) {
          setNotice(
            `Accepted — stream attached to '${row.evidence.master_channel_name ?? 'master channel'}'. Future runs re-attach this pairing automatically.`,
          );
        } else if (outcome.already_attached) {
          setNotice('Accepted — the stream was already attached. Decision recorded for future runs.');
        } else {
          setNotice(
            `Accepted — decision recorded. Attach deferred: ${outcome.attach_deferred_reason ?? 'the next pipeline run attaches it'}.`,
          );
        }
      } catch (err) {
        const detail = err instanceof Error ? err.message : 'Accept failed';
        logger.error('EventSyncReviewQueue: accept failed for row %s', row.id, err);
        setRowErrors(prev => ({ ...prev, [row.id]: detail }));
      } finally {
        setRowBusy(prev => {
          const next = { ...prev };
          delete next[row.id];
          return next;
        });
      }
    },
    [clearRowError],
  );

  // ti939.3.5: "Never attach" = create the standing exclusion, then close
  // the open question as rejected. Two calls; if the reject half fails the
  // exclusion still stands (the resolver already suppresses the pairing)
  // and the row surfaces the error for a retry.
  const handleNeverAttach = useCallback(
    async (row: EventSyncReviewRecord) => {
      clearRowError(row.id);
      setNotice(null);
      setRowBusy(prev => ({ ...prev, [row.id]: true }));
      try {
        await api.createEventSyncExclusion({
          rule_id: row.rule_id,
          provider_id: row.provider_id,
          stream_name_hash: row.stream_name_hash,
          event_key: row.event_key,
          evidence: row.evidence,
        });
        await api.rejectEventSyncReview(row.id);
        setRows(prev => prev.filter(r => r.id !== row.id));
        setTotal(prev => Math.max(0, prev - 1));
        setNotice(
          'Never attach recorded — this pairing is excluded on every future run. Manage exclusions below.',
        );
        // Tell the exclusions panel to refetch (shared-state-free contract).
        window.dispatchEvent(new CustomEvent(EXCLUSIONS_CHANGED_EVENT));
      } catch (err) {
        const detail = err instanceof Error ? err.message : 'Never attach failed';
        logger.error('EventSyncReviewQueue: never-attach failed for row %s', row.id, err);
        setRowErrors(prev => ({ ...prev, [row.id]: detail }));
      } finally {
        setRowBusy(prev => {
          const next = { ...prev };
          delete next[row.id];
          return next;
        });
      }
    },
    [clearRowError],
  );

  const handleReject = useCallback(
    async (row: EventSyncReviewRecord) => {
      clearRowError(row.id);
      setNotice(null);
      setRowBusy(prev => ({ ...prev, [row.id]: true }));
      try {
        await api.rejectEventSyncReview(row.id);
        setRows(prev => prev.filter(r => r.id !== row.id));
        setTotal(prev => Math.max(0, prev - 1));
        setNotice('Rejected — this pairing will never attach and will not be asked about again.');
      } catch (err) {
        const detail = err instanceof Error ? err.message : 'Reject failed';
        logger.error('EventSyncReviewQueue: reject failed for row %s', row.id, err);
        setRowErrors(prev => ({ ...prev, [row.id]: detail }));
      } finally {
        setRowBusy(prev => {
          const next = { ...prev };
          delete next[row.id];
          return next;
        });
      }
    },
    [clearRowError],
  );

  const toggleSelected = useCallback((rowId: number) => {
    setSelectedIds(previous => {
      const next = new Set(previous);
      if (next.has(rowId)) next.delete(rowId);
      else next.add(rowId);
      return next;
    });
  }, []);

  const renderedSelectedCount = rows.filter(row => selectedIds.has(row.id)).length;
  const allRenderedSelected = rows.length > 0 && renderedSelectedCount === rows.length;

  const toggleAllRendered = useCallback(() => {
    setSelectedIds(previous => {
      const next = new Set(previous);
      if (rows.every(row => next.has(row.id))) {
        rows.forEach(row => next.delete(row.id));
      } else {
        rows.forEach(row => next.add(row.id));
      }
      return next;
    });
  }, [rows]);

  const openBulkDiscard = useCallback(() => {
    const exactIds = rows.filter(row => selectedIds.has(row.id)).map(row => row.id);
    if (exactIds.length === 0) return;
    setDiscardError(null);
    setDiscardIds(exactIds);
  }, [rows, selectedIds]);

  const closeBulkDiscard = useCallback(() => {
    if (discardBusy) return;
    setDiscardError(null);
    setDiscardIds(null);
  }, [discardBusy]);

  const confirmBulkDiscard = useCallback(async () => {
    if (!discardIds || discardIds.length === 0) return;
    setDiscardBusy(true);
    setDiscardError(null);
    try {
      const outcome = await api.bulkDiscardEventSyncReviews(discardIds);
      const requested = outcome.requested_ids.length;
      const details: string[] = [];
      if (outcome.missing_ids.length > 0) {
        details.push(`${outcome.missing_ids.length} was already removed`);
      }
      if (outcome.not_pending_ids.length > 0) {
        details.push(`${outcome.not_pending_ids.length} was no longer pending`);
      }
      setNotice(
        `Discarded ${outcome.discarded_ids.length} of ${requested} selected review items` +
          (details.length ? `; ${details.join('; ')}.` : '.'),
      );
      setSelectedIds(new Set());
      setDiscardIds(null);
      await loadRows();
    } catch (err) {
      const detail = err instanceof Error ? err.message : 'Discard failed';
      logger.error('EventSyncReviewQueue: bulk discard failed', err);
      setDiscardError(detail);
    } finally {
      setDiscardBusy(false);
    }
  }, [discardIds, loadRows]);

  return (
    <div className="event-sync-review-queue" data-testid="event-sync-review-queue">
      <div className="event-sync-review-header">
        <h3>
          Event Sync Review
          {total > 0 && (
            <span
              className="event-sync-review-count"
              data-testid="event-sync-review-count"
              aria-label={`${total} pairings pending review`}
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
          title="Reload review queue"
        >
          <span className={`material-icons ${loading ? 'spinning-cw' : ''}`}>refresh</span>
          Refresh
        </button>
      </div>
      <p className="form-hint">
        Ambiguous event matches wait here instead of attaching. Decisions are
        keyed on the provider string and event identity — they survive
        provider refreshes: Accept auto-attaches this pairing on every future
        run; Reject suppresses it permanently.
      </p>

      {loadError && (
        <div className="error-banner" role="alert">
          <span className="material-icons">error</span>
          <span>{loadError}</span>
        </div>
      )}

      {notice && (
        <div className="event-sync-review-notice" role="status">
          <span className="material-icons" aria-hidden="true">info</span>
          <span>{notice}</span>
        </div>
      )}

      {!loading && rows.length === 0 && !loadError && (
        <div className="empty-state">
          <span className="material-icons">inbox</span>
          <h3>No pairings awaiting review</h3>
          <p>
            Ambiguous event matches from event sync runs (manual or automatic)
            will appear here for a decision.
          </p>
        </div>
      )}

      {rows.length > 0 && (
        <>
          <div className="event-sync-review-bulk-toolbar" aria-label="Event Sync review bulk actions">
            <label className="event-sync-review-select-all">
              <input
                type="checkbox"
                checked={allRenderedSelected}
                onChange={toggleAllRendered}
                aria-label="Select all rendered reviews"
              />
              <span>{renderedSelectedCount > 0 ? `${renderedSelectedCount} selected` : 'Select rendered reviews'}</span>
            </label>
            <button
              type="button"
              className="btn-secondary"
              onClick={openBulkDiscard}
              disabled={renderedSelectedCount === 0 || discardBusy}
            >
              <span className="material-icons" aria-hidden="true">delete_sweep</span>
              Discard selected
            </button>
          </div>
          <ul className="event-sync-review-list" aria-label="Event sync review queue">
          {rows.map(row => {
            const ev = row.evidence;
            const busy = !!rowBusy[row.id];
            const rowError = rowErrors[row.id];
            const band = ev.band ? BAND_META[ev.band] : null;
            const verdict = ev.team_verdict ? TEAM_VERDICT_META[ev.team_verdict] : null;
            return (
              <li key={row.id} className="event-sync-review-card">
                <div className="event-sync-review-card-header">
                  <input
                    type="checkbox"
                    className="event-sync-review-selector"
                    checked={selectedIds.has(row.id)}
                    onChange={() => toggleSelected(row.id)}
                    disabled={busy || discardBusy}
                    aria-label={`Select review ${row.id}`}
                  />
                  {ev.rule_name && (
                    <span className="event-sync-review-rule">{ev.rule_name}</span>
                  )}
                  {ev.provider && (
                    <span className="event-sync-review-provider">{ev.provider}</span>
                  )}
                  {ev.ambiguous_reason === 'contested_top_candidates' && (
                    <span className="event-sync-review-chip">
                      <span className="material-icons" aria-hidden="true">alt_route</span>
                      Contested between masters
                    </span>
                  )}
                </div>

                {/* Per-candidate evidence: both raw names side by side with
                    parsed identities — never just an aggregate number. */}
                <div className="event-sync-review-sides">
                  <div className="event-sync-review-side">
                    <span className="event-sync-review-side-role">Secondary stream</span>
                    <span className="event-sync-review-raw-name">{ev.stream_name ?? '—'}</span>
                    <dl className="event-sync-review-parsed">
                      <dt>Parsed title</dt>
                      <dd>{ev.stream_parsed_title ?? '—'}</dd>
                      <dt>Parsed start</dt>
                      <dd>{formatStart(ev.stream_parsed_start)}</dd>
                    </dl>
                  </div>
                  <div className="event-sync-review-side">
                    <span className="event-sync-review-side-role">Master channel candidate</span>
                    <span className="event-sync-review-raw-name">
                      {ev.master_channel_name ?? '—'}
                    </span>
                    <dl className="event-sync-review-parsed">
                      <dt>Parsed title</dt>
                      <dd>{ev.master_parsed_title ?? '—'}</dd>
                      <dt>Parsed start</dt>
                      <dd>{formatStart(ev.master_parsed_start)}</dd>
                    </dl>
                  </div>
                </div>

                <div className="event-sync-review-evidence">
                  {typeof ev.score === 'number' && (
                    <span className="event-sync-review-chip">
                      Score {ev.score.toFixed(2)}
                    </span>
                  )}
                  {band && (
                    <span className="event-sync-review-chip">
                      <span className="material-icons" aria-hidden="true">{band.icon}</span>
                      {band.label} band
                    </span>
                  )}
                  {verdict && (
                    <span className="event-sync-review-chip">
                      <span className="material-icons" aria-hidden="true">{verdict.icon}</span>
                      {verdict.label}
                    </span>
                  )}
                  {typeof ev.time_delta_minutes === 'number' && (
                    <span className="event-sync-review-chip">
                      <span className="material-icons" aria-hidden="true">schedule</span>
                      Start delta {ev.time_delta_minutes} min
                    </span>
                  )}
                </div>

                {ev.ambiguous_reason === 'contested_top_candidates' && (
                  <p className="event-sync-review-contested-note" role="note">
                    <span className="material-icons" aria-hidden="true">info</span>
                    <span>
                      Contested between two masters. Rejecting this pairing may
                      let the other (sibling) master attach automatically on the
                      next run if that pairing scores in the attach band —
                      rejecting one side is how you steer the stream to the
                      other.
                    </span>
                  </p>
                )}

                <div className="event-sync-review-actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => handleNeverAttach(row)}
                    disabled={busy}
                    title="Record a standing exclusion: this pairing never attaches, on any future run, until you remove it from the exclusions list"
                  >
                    <span className="material-icons" aria-hidden="true">block</span>
                    Never attach
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => handleReject(row)}
                    disabled={busy}
                  >
                    <span className="material-icons" aria-hidden="true">do_not_disturb_on</span>
                    Reject pairing
                  </button>
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => handleAccept(row)}
                    disabled={busy}
                  >
                    <span className="material-icons" aria-hidden="true">task_alt</span>
                    {busy ? 'Working...' : 'Accept & attach'}
                  </button>
                </div>

                {rowError && (
                  <div className="error-banner event-sync-review-row-error" role="alert">
                    <span className="material-icons">error</span>
                    <span>{rowError}</span>
                  </div>
                )}
              </li>
            );
          })}
          </ul>
        </>
      )}

      {discardIds && (
        <ModalOverlay onClose={closeBulkDiscard}>
          <div
            className="modal-container modal-sm"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby={discardTitleId}
          >
            <div className="modal-header">
              <h3 id={discardTitleId} className="modal-title">
                Discard {discardIds.length} selected review {discardIds.length === 1 ? 'item' : 'items'}?
              </h3>
            </div>
            <div className="modal-body">
              <p>
                This removes only the selected pending review items. It does not
                detach streams or change accepted and rejected decisions.
              </p>
              {discardError && <div className="error-banner" role="alert">{discardError}</div>}
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="modal-btn modal-btn-secondary"
                onClick={closeBulkDiscard}
                disabled={discardBusy}
              >
                Cancel
              </button>
              <button
                type="button"
                className="modal-btn modal-btn-danger"
                onClick={confirmBulkDiscard}
                disabled={discardBusy}
              >
                {discardBusy
                  ? 'Discarding...'
                  : `Discard ${discardIds.length} ${discardIds.length === 1 ? 'item' : 'items'}`}
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}
    </div>
  );
}

export default EventSyncReviewQueue;
