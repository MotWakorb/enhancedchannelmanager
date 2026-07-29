/**
 * ECM brand mark.
 *
 * A programme guide is a channel column down the left with time blocks running
 * right from it — which is already the shape of an E. The spine is the channel
 * column and the three arms are schedule rows, each broken into programme cells.
 *
 * Two letters ride on that one geometry:
 *   · the outer path (top arm, spine, bottom arm) traces a C, open to the right;
 *   · the middle arm is drawn as an M, at the same row height as every other
 *     band so it reads as part of the schedule rather than an addition to it.
 *
 * Inline SVG rather than a raster asset so it takes ink and accent from the
 * active theme. Decorative — the sidebar control that wraps it carries the
 * accessible name, so the SVG is hidden from assistive technology.
 */

/** Geometry of the M, tuned against the surrounding cells (PO-selected). */
const M_GEOMETRY = {
  x: 9.5,
  y: 13.5,
  width: 8.5,
  height: 5,
  /** Stem thickness. */
  stem: 2,
  /** Corner radius; matches the rx="1.3" feel of the programme cells. */
  radius: 0.55,
  /** How far the V descends, as a fraction of the band height. */
  vertexDepth: 0.51,
} as const;

/**
 * Builds the M as a path that is inset by the radius and then stroked in its
 * own colour at twice that radius. Stroking is what rounds the counters inside
 * the V — a plain filled polygon can only round its outer corners.
 */
function buildMPath(): { d: string; strokeWidth: number } {
  const { x, y, width, height, stem, radius, vertexDepth } = M_GEOMETRY;
  const r = Math.min(radius, stem / 2 - 0.05, height / 2 - 0.05);
  const ix = x + r;
  const iy = y + r;
  const iw = width - 2 * r;
  const ih = height - 2 * r;
  const inset = Math.max(0.25, stem - 2 * r);
  const vx = ix + iw / 2;
  const vy = iy + vertexDepth * ih;
  const counter = Math.max(iy + 0.1, vy - inset * 1.35);
  const n = (value: number) => Number(value.toFixed(3));

  const d = [
    `M ${n(ix)} ${n(iy + ih)}`,
    `L ${n(ix)} ${n(iy)}`,
    `L ${n(ix + inset)} ${n(iy)}`,
    `L ${n(vx)} ${n(vy)}`,
    `L ${n(ix + iw - inset)} ${n(iy)}`,
    `L ${n(ix + iw)} ${n(iy)}`,
    `L ${n(ix + iw)} ${n(iy + ih)}`,
    `L ${n(ix + iw - inset)} ${n(iy + ih)}`,
    `L ${n(ix + iw - inset)} ${n(counter)}`,
    `L ${n(vx)} ${n(iy + ih)}`,
    `L ${n(ix + inset)} ${n(counter)}`,
    `L ${n(ix + inset)} ${n(iy + ih)}`,
    'Z',
  ].join(' ');

  return { d, strokeWidth: n(2 * r) };
}

const M_PATH = buildMPath();

export function EcmLogo({ size = 38, className }: { size?: number; className?: string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden="true"
      focusable="false"
    >
      {/* Channel column */}
      <rect x="3.5" y="4" width="4.5" height="24" rx="1.5" className="ecm-logo-ink" />

      {/* Top row — programme cells */}
      <rect x="9.5" y="4" width="5.5" height="5" rx="1.3" className="ecm-logo-ink" />
      <rect x="16.2" y="4" width="4.5" height="5" rx="1.3" className="ecm-logo-ink" />
      <rect x="21.9" y="4" width="6.6" height="5" rx="1.3" className="ecm-logo-ink" />

      {/* Middle row — the M, then a shorter programme cell. The row stops at
          23.5 while the arms above and below run to 28.5, which is what gives
          the E its proper short middle arm. */}
      <path
        d={M_PATH.d}
        className="ecm-logo-accent"
        strokeWidth={M_PATH.strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <rect x="19.4" y="13.5" width="4.1" height="5" rx="1.3" className="ecm-logo-accent-soft" />

      {/* Bottom row — programme cells */}
      <rect x="9.5" y="23" width="4" height="5" rx="1.3" className="ecm-logo-ink" />
      <rect x="14.7" y="23" width="7.8" height="5" rx="1.3" className="ecm-logo-ink" />
      <rect x="23.7" y="23" width="4.8" height="5" rx="1.3" className="ecm-logo-ink" />
    </svg>
  );
}
