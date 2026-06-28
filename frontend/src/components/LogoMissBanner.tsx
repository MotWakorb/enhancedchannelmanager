/**
 * LogoMissBanner — the restore-complete logo-miss RED banner (bead 0i2vt.19).
 *
 * ADR-012 **D9** ("Logo-miss severity"): a logo that cannot be matched on
 * restore produces a WARN log + an aggregate count + a **prominent red banner on
 * the restore-complete screen** — never a silent DEBUG line. This component is
 * that banner. A restored channel with a missing logo looks identical to a
 * correctly-restored one at the API level (DBAS's silent-DEBUG pattern hides
 * this); the banner makes the problem unmissable at the moment the operator can
 * act on it.
 *
 * Contract: the merged {@link RestoreReport} carries the logo-miss signal as an
 * AGGREGATE COUNT (`logo_misses: number`) AND — since bead qhui4 — an OPTIONAL
 * per-logo detail list (`logo_miss_details: LogoMissDetail[]`, each id + name).
 * The aggregate count gates the red banner (D9, unchanged); when the detail list
 * is present and non-empty the banner ADDS a drill-down enumerating which logos
 * are missing. The banner also names the count and links to the Dispatcharr
 * **Channels** admin page, where the operator fixes the affected channels
 * one-off. The link is built with the same `${dispatcharrUrl}/…` pattern the rest
 * of the app uses (see ChannelsPane).
 *
 * Colorblind-safe (WCAG 1.4.1): the meaning is carried by an icon + the word
 * "missing" in the copy, not by red colour alone. `role="alert"` so assistive
 * tech announces it.
 *
 * Renders into {@link RestoreCompleteSummary}'s `bannerSlot`, which places it at
 * the very top of the summary, above the tri-state outcome banner.
 */
import type { RestoreReport } from '../services/api';
import './LogoMissBanner.css';

interface LogoMissBannerProps {
  /** The realized restore report; `logo_misses` drives the banner. */
  report: RestoreReport;
  /**
   * The Dispatcharr base URL (from settings — `App` exposes it as
   * `dispatcharrUrl`). When present, the banner offers a "Fix in Dispatcharr"
   * link to the Channels admin page. When absent/empty, the banner still warns;
   * it just omits the link.
   */
  dispatcharrUrl?: string;
}

/** Plain-language count phrase — singular vs. plural, never the raw field name. */
function missCopy(count: number): string {
  return count === 1
    ? '1 channel is missing its logo'
    : `${count} channels are missing their logo`;
}

export function LogoMissBanner({ report, dispatcharrUrl }: LogoMissBannerProps) {
  const count = report.logo_misses;

  // Never fire when every logo was attached (count === 0). This is the
  // success-signal contract from the bead: no banner on a clean restore.
  if (!count || count <= 0) {
    return null;
  }

  // Build the Dispatcharr Channels-page link with the same `${base}/…` pattern
  // ChannelsPane uses; strip a trailing slash so we never double it.
  const base = dispatcharrUrl?.replace(/\/+$/, '');
  const channelsHref = base ? `${base}/channels` : null;

  // Optional per-logo drill-down (bead qhui4). Additive to the aggregate count:
  // present + non-empty => enumerate which logos are missing.
  const details = report.logo_miss_details ?? [];
  const hasDetails = details.length > 0;

  return (
    <div className="logo-miss-banner" data-testid="logo-miss-banner" role="alert">
      <span className="material-icons logo-miss-icon" data-testid="logo-miss-icon" aria-hidden="true">
        broken_image
      </span>
      <div className="logo-miss-text">
        <span className="logo-miss-title">{missCopy(count)}</span>
        <span className="logo-miss-detail">
          These channels were restored without a logo. Open them in Dispatcharr to set a logo on each.
        </span>
        {hasDetails && (
          <ul className="logo-miss-detail-list" data-testid="logo-miss-detail-list">
            {details.map((miss, index) => (
              <li
                className="logo-miss-detail-row"
                data-testid="logo-miss-detail-row"
                key={miss.source_export_id ?? `idx-${index}`}
              >
                {miss.label?.trim() || 'Unnamed logo'}
              </li>
            ))}
          </ul>
        )}
        {channelsHref && (
          <a
            className="logo-miss-link"
            data-testid="logo-miss-detail-link"
            href={channelsHref}
            target="_blank"
            rel="noopener noreferrer"
          >
            Fix in Dispatcharr
            <span className="material-icons logo-miss-link-icon" aria-hidden="true">
              open_in_new
            </span>
          </a>
        )}
      </div>
    </div>
  );
}
