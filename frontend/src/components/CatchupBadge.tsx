/**
 * CatchupBadge (bead enhancedchannelmanager-sy1sz).
 *
 * Reusable badge indicating that a channel or stream supports catch-up
 * (timeshift / archive). Reads the two scalar Dispatcharr fields that reach
 * the frontend on both the channel and stream objects:
 *   - `is_catchup` (boolean) — THE authoritative "supported" flag.
 *   - `catchup_days` (integer) — archive depth in days.
 *
 * Rules (see the pinned contract on the bead):
 *   - Render ONLY when `isCatchup === true`. Support is NEVER inferred from
 *     `catchupDays` — the flag wins (days:5 with the flag false → no badge).
 *   - Tooltip: `catchupDays > 0` → "Catch-up: N day(s)"; when the flag is true
 *     but days is 0/absent → "Catch-up available" (never render "0 days").
 *
 * Self-contained (own component + CSS) because it is reused on channel rows,
 * stream rows, and — later — the provider badge (bead 4dpiz).
 *
 * Usage:
 *   <CatchupBadge isCatchup={channel.is_catchup} catchupDays={channel.catchup_days} />
 */
import './CatchupBadge.css';

export interface CatchupBadgeProps {
  /** Authoritative catch-up support flag. Badge renders only when this is true. */
  isCatchup?: boolean;
  /** Archive depth in days. Drives the day count in the label/tooltip. */
  catchupDays?: number;
  /** Additional CSS class names on the outer <span>. */
  className?: string;
  /**
   * Tooltip / aria-label override. When omitted the badge derives its own
   * copy from the day count (e.g. "Catch-up: 5 days"). Callers that need
   * context-specific wording — the provider badge (bead 4dpiz) says
   * "Provider supports catch-up …" — pass it here instead of forking the
   * component. The visible compact label is unaffected.
   */
  title?: string;
}

export function CatchupBadge({ isCatchup, catchupDays, className, title }: CatchupBadgeProps) {
  // Flag is authoritative — never infer support from catchupDays.
  if (isCatchup !== true) return null;

  const hasDays = typeof catchupDays === 'number' && catchupDays > 0;
  const label = title ?? (hasDays
    ? `Catch-up: ${catchupDays} day${catchupDays === 1 ? '' : 's'}`
    : 'Catch-up available');
  const classes = ['catchup-badge', className].filter(Boolean).join(' ');

  return (
    <span className={classes} title={label} aria-label={label}>
      <span className="material-icons catchup-badge__icon" aria-hidden="true">
        replay
      </span>
      <span className="catchup-badge__text" aria-hidden="true">
        {hasDays ? `${catchupDays}d` : 'Catch-up'}
      </span>
    </span>
  );
}
