/**
 * StreamReattachNotice — the restore-complete "these channels cannot play"
 * action item (bead d0bd3), sibling of {@link CredentialReentryNotice}.
 *
 * Backup/restore drill run 2026-08-06-run9 restored a redacted artifact onto a
 * fresh target. The report was right about everything:
 * `channels_needing_stream_reattach: 12`, `channels_with_no_playable_stream: 12`,
 * and all twelve channels named in `stream_reattach_details`. The
 * restore-complete dialog showed the credentials panel and the per-category
 * count grid, and nothing else. Nowhere on screen did it say that not one
 * channel could play — measured at the same moment as 0/2 playback, HTTP 500.
 *
 * The product's own documentation tells operators "the UI shows matching panels
 * after a restore … use these instead of re-deriving the same information by
 * hand", so an operator who followed that advice concluded the only outstanding
 * item was a credential, on an instance that played nothing. The report data was
 * already correct; only this panel was missing.
 *
 * TWO POPULATIONS, ONE OF WHICH IS NOT BROKEN
 * -------------------------------------------
 * `channels_needing_stream_reattach` counts every channel left holding a
 * URL-less placeholder slot. `channels_with_no_playable_stream` is the SUBSET
 * with no URL-bearing stream at all (bead daziw). Only the subset is broken:
 * a channel that kept its real streams and holds one leftover placeholder is
 * the designed output of the ixdaw fix ("costs one slot instead of the entire
 * channel") and plays fine. Collapsing the two would either cry wolf over a
 * working channel or bury a dead one, so the panel names them separately and
 * only the dead population raises `role="alert"`.
 *
 * DRY RUN: silent. Both counters read `null` — NOT PREDICTED — on a preview
 * (bead dgnms), because the rebind pass that answers them re-matches against
 * provider streams a deferred M3U refresh materializes, and a preview refreshes
 * nothing. Rendering `null` as `0` is the confident claim that null exists to
 * stop making.
 *
 * Colorblind-safe (WCAG 1.4.1): meaning is carried by the icon and the words
 * "no playable stream" / "placeholder" in the copy, never by colour alone.
 */
import type { RestoreReport, StreamReattachDetail } from '../services/api';
import type { RestoreSummaryMode } from './RestoreCompleteSummary';
import './StreamReattachNotice.css';

interface StreamReattachNoticeProps {
  /** The restore report; the two stream-reattach aggregates gate the notice. */
  report: RestoreReport;
  /**
   * Framing override. When omitted, inferred from `report.is_dry_run`. A
   * `dry-run` renders nothing — the counters are not predicted.
   */
  mode?: RestoreSummaryMode;
}

/** The channels that cannot play at all. */
function unplayableRows(details: StreamReattachDetail[]): StreamReattachDetail[] {
  return details.filter((detail) => detail.has_playable_stream === false);
}

/**
 * The channels that hold a placeholder and still play.
 *
 * `has_playable_stream` is additive-optional and defaults to `true` on the
 * backend, so a row from a report written before the field existed must never be
 * counted as dead.
 */
function holdingRows(details: StreamReattachDetail[]): StreamReattachDetail[] {
  return details.filter((detail) => detail.has_playable_stream !== false);
}

function ChannelRows({ rows, kind }: { rows: StreamReattachDetail[]; kind: string }) {
  if (rows.length === 0) return null;
  return (
    <ul className="stream-reattach-list" data-testid={`stream-reattach-${kind}-list`}>
      {rows.map((row, index) => (
        <li
          className="stream-reattach-row"
          data-testid={`stream-reattach-${kind}-row`}
          key={row.channel_id ?? `idx-${index}`}
        >
          <span className="stream-reattach-channel">{row.name?.trim() || 'Unnamed channel'}</span>
          {typeof row.channel_id === 'number' && (
            <span className="stream-reattach-id">(channel {row.channel_id})</span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function StreamReattachNotice({ report, mode }: StreamReattachNoticeProps) {
  const effectiveMode: RestoreSummaryMode = mode ?? (report.is_dry_run ? 'dry-run' : 'applied');
  if (effectiveMode === 'dry-run') {
    return null;
  }

  const unplayable = report.channels_with_no_playable_stream ?? 0;
  const holdingTotal = report.channels_needing_stream_reattach ?? 0;
  // The counters are the gate; the per-channel list is an additive drill-down,
  // the same shape the credential and logo-miss panels use.
  if (unplayable <= 0 && holdingTotal <= 0) {
    return null;
  }

  const details = report.stream_reattach_details ?? [];
  const dead = unplayableRows(details);
  const holding = holdingRows(details);
  // `channels_needing_stream_reattach` is a SUPERSET, so the playing population
  // is the remainder — never the raw count, which would report the dead channels
  // twice under two different descriptions.
  const holdingRemainder = Math.max(holdingTotal - unplayable, 0);

  return (
    <div
      className={`stream-reattach-notice ${unplayable > 0 ? 'stream-reattach-dead' : ''}`}
      data-testid="stream-reattach-notice"
      role={unplayable > 0 ? 'alert' : 'status'}
    >
      <span
        className="material-icons stream-reattach-icon"
        data-testid="stream-reattach-icon"
        aria-hidden="true"
      >
        {unplayable > 0 ? 'error' : 'link_off'}
      </span>
      <div className="stream-reattach-text">
        {unplayable > 0 && (
          <>
            <span className="stream-reattach-title">
              {unplayable === 1
                ? '1 channel has no playable stream'
                : `${unplayable} channels have no playable stream`}
            </span>
            <span className="stream-reattach-detail">
              Not one of the streams left on{' '}
              {unplayable === 1 ? 'this channel' : 'these channels'} carries a URL, so nothing
              will play until you attach a real stream to each. Re-entering any credential the
              panel above names, then refreshing that account, is what usually resolves it.
            </span>
            <ChannelRows rows={dead} kind="unplayable" />
          </>
        )}
        {holdingRemainder > 0 && (
          <>
            <span
              className={unplayable > 0 ? 'stream-reattach-subtitle' : 'stream-reattach-title'}
            >
              {holdingRemainder === 1
                ? '1 channel still holds a placeholder stream'
                : `${holdingRemainder} channels still hold a placeholder stream`}
            </span>
            <span className="stream-reattach-detail">
              {holdingRemainder === 1 ? 'This channel keeps' : 'These channels keep'} a real,
              working stream alongside the leftover slot, so{' '}
              {holdingRemainder === 1 ? 'it plays' : 'they play'}. Clearing the slot is tidy-up,
              not a repair.
            </span>
            <ChannelRows rows={holding} kind="holding" />
          </>
        )}
      </div>
    </div>
  );
}
