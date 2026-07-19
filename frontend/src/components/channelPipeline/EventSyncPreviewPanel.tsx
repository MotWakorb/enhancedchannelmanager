/**
 * Event Sync match preview panel (bead ti939.1.5 — Phase 1A, preview only).
 *
 * Renders the response of POST /api/channel-pipeline/event-sync-preview:
 * pre-flight results, a reconciling summary line, per-stream match cards
 * (raw provider names side by side with parsed identities, score, band,
 * team-token verdict, time delta), a distinct unmatched-secondary-streams
 * list, and a parse-failure panel.
 *
 * There is deliberately NO apply/attach control anywhere in this component —
 * the attach path is Phase 1B, gated on the PO validating preview quality
 * (docs/event_sync.md). Bands and dispositions render as text label + icon,
 * never color alone.
 */
import { useState } from 'react';
import type {
  EventSyncPreviewResponse,
  EventSyncStreamRow,
} from '../../types/eventSync';
import {
  BAND_META,
  DISPOSITION_META,
  REVIEW_STATUS_META,
  TEAM_VERDICT_META,
} from './eventSyncDefaults';
import { getDateLocale } from '../../utils/formatting';
import './EventSyncPreviewPanel.css';

/** Cards rendered before the "Show more" affordance kicks in. */
const CARDS_PER_PAGE = 50;

/**
 * S5 (bead sf8dj): the caution behind each provenance chip — why this
 * would-attach row deserves a second look. Keyed on the machine `key`;
 * an unknown key falls back to the chip label alone.
 */
const MATCHED_VIA_TITLES: Record<string, string> = {
  assume_current_date:
    'Matched only because the missing date was assumed to be today — confirm the event really is today.',
  time_window_ignored:
    'Matched outside the time window because the window gate is off — confirm both start times refer to the same event.',
  lowered_threshold:
    'Matched on a score below the default floor because the attach threshold was lowered — double-check this is the right master.',
  master_from_stream:
    'The master identity was read from its attached stream name (parse-from-stream), not the channel name.',
};

export interface EventSyncPreviewPanelProps {
  preview: EventSyncPreviewResponse | null;
  loading: boolean;
  error: string | null;
  onRunPreview: () => void;
  /** Non-null blocks the preview button and explains why. */
  disabledReason?: string | null;
  /**
   * Compact mode (bead m1s38.1): render only the run affordance, pre-flight
   * warnings, and the one-line summary — the detailed match cards, unmatched
   * table, and parse-failure lists are omitted so the panel fits the rail on
   * the configuration steps. The Review step passes false to show everything.
   */
  compact?: boolean;
  /**
   * Stale marking (bead m1s38.1): the results reflect a config that has since
   * changed. Dim them and show a "Settings changed — re-run" notice; the
   * results are NOT cleared (the operator can still read the last run).
   */
  stale?: boolean;
}

function formatStart(iso: string | null): string {
  if (!iso) return '—';
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString(getDateLocale());
}

function summaryLine(summary: EventSyncPreviewResponse['summary']): string {
  let line =
    `${summary.would_attach} would attach, ` +
    `${summary.ambiguous_skipped} ambiguous (skipped), ` +
    `${summary.unmatched} unmatched, ` +
    `${summary.parse_failed} parse failure${summary.parse_failed === 1 ? '' : 's'}`;
  // ti939.3.5: operator never-attach exclusions — a suppressed pairing the
  // operator cannot see is the exact loop this feature closes.
  const excluded = summary.excluded_by_operator ?? 0;
  if (excluded > 0) {
    line += `, ${excluded} excluded by operator`;
  }
  // ti939.3.2: review-queue context — decision-driven attaches and open
  // questions must be visible in the one-line summary too.
  if (summary.would_attach_via_review > 0) {
    line += ` (${summary.would_attach_via_review} via review-queue accept)`;
  }
  if (summary.candidates_pending_review > 0) {
    line += ` · ${summary.candidates_pending_review} pairing${
      summary.candidates_pending_review === 1 ? '' : 's'
    } pending review`;
  }
  // bead 2ey2y: staleness-signal counts (jqwfq Stage 1) belong in the stats
  // line — a stale-suspect or unknown-freshness population the operator
  // cannot see is exactly the silent-rail problem this surfaces.
  const staleSuspect = summary.stale_suspect_streams ?? 0;
  const freshnessUnknown = summary.freshness_unknown_streams ?? 0;
  if (staleSuspect > 0) {
    line += ` · ${staleSuspect} stale-suspect name${staleSuspect === 1 ? '' : 's'}`;
  }
  if (freshnessUnknown > 0) {
    line += ` · ${freshnessUnknown} name${
      freshnessUnknown === 1 ? '' : 's'
    } of unknown freshness`;
  }
  return line;
}

function DispositionBadge({ disposition }: { disposition: EventSyncStreamRow['disposition'] }) {
  const meta = DISPOSITION_META[disposition];
  return (
    <span className={`event-sync-badge event-sync-badge-${disposition}`}>
      <span className="material-icons" aria-hidden="true">{meta.icon}</span>
      {meta.label}
    </span>
  );
}

function MatchCard({ stream }: { stream: EventSyncStreamRow }) {
  const top = stream.candidates[0] ?? null;
  const attachTarget = stream.would_attach_master;
  const flags: string[] = [];
  if (stream.disposition === 'would_attach' && top) {
    if (Math.abs(top.time_delta_minutes) > 0) {
      flags.push(`Start times differ by ${Math.abs(top.time_delta_minutes)} min`);
    }
    if (top.team_verdict !== 'agree') {
      flags.push(TEAM_VERDICT_META[top.team_verdict].label);
    }
  }
  // ti939.3.2: a queue-driven attach prediction is visibly not a
  // score-driven one — the operator's own prior accept is the reason.
  if (stream.disposition === 'would_attach' && stream.attach_source === 'review_queue') {
    flags.push('Via review-queue accept');
  }

  return (
    <li
      className="event-sync-card"
      tabIndex={0}
      aria-label={`${stream.stream_name}: ${DISPOSITION_META[stream.disposition].label}`}
    >
      <div className="event-sync-card-header">
        <DispositionBadge disposition={stream.disposition} />
        {stream.provider && <span className="event-sync-provider">{stream.provider}</span>}
        {flags.map(flag => (
          <span key={flag} className="event-sync-flag">
            <span className="material-icons" aria-hidden="true">flag</span>
            {flag}
          </span>
        ))}
        {/* S5 (bead sf8dj): provenance chips — this row would attach only
            because an optional relaxation was enabled. */}
        {(stream.matched_via ?? []).map(via => (
          <span
            key={via.key}
            className="badge badge-sm badge-warning event-sync-matched-via"
            title={MATCHED_VIA_TITLES[via.key] ?? via.label}
          >
            {via.label}
          </span>
        ))}
      </div>

      <div className="event-sync-card-sides">
        <div className="event-sync-side">
          <span className="event-sync-side-role">Secondary stream</span>
          <span className="event-sync-raw-name">{stream.stream_name}</span>
          <dl className="event-sync-parsed">
            <dt>Parsed title</dt>
            <dd>{stream.parsed_title ?? '—'}</dd>
            <dt>Parsed start</dt>
            <dd>{formatStart(stream.parsed_start)}</dd>
          </dl>
          {stream.unmatchable_reason && (
            <span className="event-sync-reject-reason">
              Reason: {stream.unmatchable_reason}
            </span>
          )}
          {/* ti939.3.5: say WHICH master(s) the operator excluded — on
              excluded rows and on rows that still resolve elsewhere. */}
          {(stream.excluded_masters ?? []).length > 0 && (
            <span className="event-sync-reject-reason">
              Never attaches to: {(stream.excluded_masters ?? []).join(', ')}
            </span>
          )}
        </div>
        {attachTarget && top && (
          <div className="event-sync-side">
            <span className="event-sync-side-role">Master channel (would attach)</span>
            <span className="event-sync-raw-name">{attachTarget.name}</span>
            <dl className="event-sync-parsed">
              <dt>Parsed title</dt>
              <dd>{top.master_parsed_title ?? '—'}</dd>
              <dt>Parsed start</dt>
              <dd>{formatStart(top.master_parsed_start)}</dd>
            </dl>
          </div>
        )}
      </div>

      {stream.candidates.length > 0 && (
        <div className="event-sync-candidates">
          <table>
            <caption>Scored candidates</caption>
            <thead>
              <tr>
                <th scope="col">Master channel</th>
                <th scope="col">Score</th>
                <th scope="col">Band</th>
                <th scope="col">Team tokens</th>
                <th scope="col">Time delta</th>
                <th scope="col">Reject reason</th>
                <th scope="col">Review</th>
              </tr>
            </thead>
            <tbody>
              {stream.candidates.map(candidate => (
                <tr key={candidate.master_channel_name}>
                  <td className="event-sync-raw-name">{candidate.master_channel_name}</td>
                  <td>{candidate.score.toFixed(2)}</td>
                  <td>
                    <span
                      className={`event-sync-badge event-sync-badge-${candidate.band}`}
                      aria-label={`Confidence band: ${BAND_META[candidate.band].label}`}
                    >
                      <span className="material-icons" aria-hidden="true">
                        {BAND_META[candidate.band].icon}
                      </span>
                      {BAND_META[candidate.band].label}
                    </span>
                  </td>
                  <td>
                    <span className="event-sync-verdict">
                      <span className="material-icons" aria-hidden="true">
                        {TEAM_VERDICT_META[candidate.team_verdict].icon}
                      </span>
                      {TEAM_VERDICT_META[candidate.team_verdict].label}
                    </span>
                  </td>
                  <td>{candidate.time_delta_minutes} min</td>
                  <td>{candidate.reject_reason ?? '—'}</td>
                  <td>
                    {candidate.excluded ? (
                      /* ti939.3.5: the standing order outranks queue state —
                         show it in place of any review marker. */
                      <span className="event-sync-verdict event-sync-review-marker-excluded">
                        <span className="material-icons" aria-hidden="true">block</span>
                        Excluded (never attaches)
                      </span>
                    ) : candidate.review_status ? (
                      <span
                        className={`event-sync-verdict event-sync-review-marker-${candidate.review_status}`}
                      >
                        <span className="material-icons" aria-hidden="true">
                          {REVIEW_STATUS_META[candidate.review_status].icon}
                        </span>
                        {REVIEW_STATUS_META[candidate.review_status].label}
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </li>
  );
}

export function EventSyncPreviewPanel({
  preview,
  loading,
  error,
  onRunPreview,
  disabledReason = null,
  compact = false,
  stale = false,
}: EventSyncPreviewPanelProps) {
  const [visibleCards, setVisibleCards] = useState(CARDS_PER_PAGE);

  return (
    <div className="event-sync-preview" data-testid="event-sync-preview">
      <div className="event-sync-preview-actions">
        <button
          type="button"
          className="btn-primary event-sync-preview-run"
          onClick={() => {
            setVisibleCards(CARDS_PER_PAGE);
            onRunPreview();
          }}
          disabled={loading || disabledReason != null}
          title={disabledReason ?? undefined}
        >
          <span className={`material-icons ${loading ? 'spinning' : ''}`}>
            {loading ? 'sync' : 'visibility'}
          </span>
          {loading ? 'Previewing...' : 'Preview matches'}
        </button>
        {stale && preview && (
          <span
            className="event-sync-preview-stale"
            role="status"
            data-testid="event-sync-preview-stale"
          >
            <span className="material-icons" aria-hidden="true">warning</span>
            Settings changed — re-run
          </span>
        )}
        <span className="form-hint">
          Read-only dry run against live Dispatcharr data — nothing is written
          and no group settings are touched. A manual pipeline Run attaches
          what the preview shows (same resolver) — capped per run, journaled,
          and reversible via execution rollback.
        </span>
      </div>

      {disabledReason && (
        <span className="form-hint event-sync-disabled-reason">{disabledReason}</span>
      )}

      {error && (
        <div className="error-banner" role="alert">
          <span className="material-icons">error</span>
          {error}
        </div>
      )}

      {preview && (
        <div
          className={`event-sync-preview-results${
            stale ? ' event-sync-preview-results--stale' : ''
          }`}
        >
          {/* Pre-flight failures never block the preview — surface them loudly.
              Warnings (bead 2ey2y) are advisory: rendered in the same block,
              same teaching shape, without flipping ok. */}
          {(!preview.preflight.ok || (preview.preflight.warnings ?? []).length > 0) && (
            <div className="event-sync-preflight" data-testid="event-sync-preflight">
              {preview.preflight.failures.map(failure => (
                <div key={`${failure.group_id}-${failure.check}`} className="warning-message" role="alert">
                  <span className="material-icons">warning</span>
                  <span>
                    <strong>
                      Pre-flight ({failure.role} group {failure.group_id}):
                    </strong>{' '}
                    {failure.message}{' '}
                    <em>Expected {failure.expected}; got {failure.got}.</em>
                  </span>
                </div>
              ))}
              {(preview.preflight.warnings ?? []).map(warning => (
                <div key={warning.check} className="warning-message" role="alert">
                  <span className="material-icons">warning</span>
                  <span>
                    <strong>Pre-flight warning:</strong> {warning.message}{' '}
                    <em>Expected {warning.expected}; got {warning.got}.</em>
                  </span>
                </div>
              ))}
            </div>
          )}

          <p className="event-sync-summary" data-testid="event-sync-summary">
            <strong>{summaryLine(preview.summary)}</strong>
            {' · '}
            {preview.summary.master_channels} master channel
            {preview.summary.master_channels === 1 ? '' : 's'}
            {preview.summary.master_channels_unparsed > 0 &&
              ` (${preview.summary.master_channels_unparsed} unparsed)`}
            {preview.truncated && (
              <span className="event-sync-truncated">
                {' · '}Results truncated at the fetch ceiling — narrow the groups
                for a complete preview.
              </span>
            )}
          </p>

          {/* Compact mode (rail on steps 1-3) stops at the summary; the Review
              step renders the full detail below. */}
          {!compact && (<>
          {preview.streams.length === 0 ? (
            <p className="form-hint">No secondary streams were found in the configured groups.</p>
          ) : (
            <>
              <ul className="event-sync-cards" aria-label="Match results">
                {preview.streams.slice(0, visibleCards).map(stream => (
                  <MatchCard
                    key={`${stream.group_id}-${stream.stream_id ?? stream.stream_name}`}
                    stream={stream}
                  />
                ))}
              </ul>
              {preview.streams.length > visibleCards && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setVisibleCards(count => count + CARDS_PER_PAGE)}
                >
                  Show {Math.min(CARDS_PER_PAGE, preview.streams.length - visibleCards)} more
                  ({preview.streams.length - visibleCards} remaining)
                </button>
              )}
            </>
          )}

          {preview.unmatched_streams.length > 0 && (
            <section className="event-sync-section" data-testid="event-sync-unmatched">
              <h4>
                Unmatched secondary streams ({preview.unmatched_streams.length})
              </h4>
              <p className="form-hint">
                No master channel within the time window — events only a
                secondary provider carries get no channel in this model.
              </p>
              <div className="event-sync-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Stream</th>
                      <th scope="col">Provider</th>
                      <th scope="col">Parsed title</th>
                      <th scope="col">Parsed start</th>
                      <th scope="col">Best candidate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.unmatched_streams.map(row => (
                      <tr key={`${row.group_id}-${row.stream_id ?? row.stream_name}`}>
                        <td className="event-sync-raw-name">{row.stream_name}</td>
                        <td>{row.provider ?? '—'}</td>
                        <td>{row.parsed_title ?? '—'}</td>
                        <td>{formatStart(row.parsed_start)}</td>
                        <td>
                          {row.best_candidate
                            ? `${row.best_candidate.master_channel_name} — score ${row.best_candidate.score.toFixed(2)}, ` +
                              `${BAND_META[row.best_candidate.band].label}` +
                              (row.best_candidate.reject_reason
                                ? ` (${row.best_candidate.reject_reason})`
                                : '')
                            : 'None in time window'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {preview.parse_failures.length > 0 && (
            <section className="event-sync-section" data-testid="event-sync-parse-failures">
              <h4>Parse failures</h4>
              <p className="form-hint">
                These names matched no pattern completely. Adjust the pattern
                selection (or add a per-group override) and use the Test
                Patterns panel to verify.
              </p>
              {preview.parse_failures.map(group => (
                <div key={`${group.group_id}-${group.reason}`} className="event-sync-parse-failure">
                  <strong>
                    {group.group_name ?? `Group ${group.group_id}`}
                  </strong>{' '}
                  — {group.count} stream{group.count === 1 ? '' : 's'}
                  {group.reason && ` (${group.reason})`}
                  <ul>
                    {/* Key includes the index: sample names may repeat. */}
                    {group.stream_names.map((name, nameIndex) => (
                      <li key={`${nameIndex}-${name}`} className="event-sync-raw-name">{name}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </section>
          )}

          {preview.unparsed_master_channels.length > 0 && (
            <details className="event-sync-section">
              <summary>
                Unparsed master channels ({preview.unparsed_master_channels.length}) —
                can never be attach targets
              </summary>
              <ul>
                {/* Key includes the index: channel names may repeat. */}
                {preview.unparsed_master_channels.map((name, nameIndex) => (
                  <li key={`${nameIndex}-${name}`} className="event-sync-raw-name">{name}</li>
                ))}
              </ul>
            </details>
          )}
          </>)}
        </div>
      )}
    </div>
  );
}
