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
 */
import { useCallback, useEffect, useState } from 'react';
import * as api from '../../services/api';
import type { EventSyncReviewRecord } from '../../types/eventSync';
import { logger } from '../../utils/logger';
import { getDateLocale } from '../../utils/formatting';
import { BAND_META, TEAM_VERDICT_META } from './eventSyncDefaults';
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

                <div className="event-sync-review-actions">
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
      )}
    </div>
  );
}

export default EventSyncReviewQueue;
