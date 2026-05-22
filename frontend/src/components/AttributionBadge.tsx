/**
 * AttributionBadge (bd-r5f0c.5 / W5).
 *
 * Reusable badge for media-server attribution. Renders a Material Icon +
 * "via <Source>" text in a styled <span>. A11y-correct: icon + text, NOT
 * color-only differentiation. The icon span carries an aria-label so
 * screen-reader users hear the source name from the icon alone.
 *
 * Usage:
 *   <AttributionBadge source="emby" />
 *   <AttributionBadge source="plex" />
 *   <AttributionBadge source="jellyfin" />
 *
 * Icon mapping (Material Icons):
 *   emby     → live_tv
 *   plex     → smart_display
 *   jellyfin → play_circle
 */
import './AttributionBadge.css';

export type AttributionBadgeSource = 'emby' | 'plex' | 'jellyfin';

const SOURCE_CONFIG: Record<AttributionBadgeSource, { icon: string; label: string }> = {
  emby: { icon: 'live_tv', label: 'Emby' },
  plex: { icon: 'smart_display', label: 'Plex' },
  jellyfin: { icon: 'play_circle', label: 'Jellyfin' },
};

export interface AttributionBadgeProps {
  source: AttributionBadgeSource;
  /** Additional CSS class names on the outer <span>. */
  className?: string;
}

export function AttributionBadge({ source, className }: AttributionBadgeProps) {
  const { icon, label } = SOURCE_CONFIG[source];
  const classes = ['attribution-badge', `attribution-badge--${source}`, className]
    .filter(Boolean)
    .join(' ');
  return (
    <span className={classes}>
      <span
        className="material-icons attribution-badge__icon"
        aria-label={`Attributed via ${label}`}
      >
        {icon}
      </span>
      <span className="attribution-badge__text">via {label}</span>
    </span>
  );
}
