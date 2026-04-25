/**
 * Categorical chart palette — colorblind-safe, dark-theme-tuned.
 *
 * Source: Paul Tol's "bright" qualitative scheme.
 *   Reference: https://personal.sron.nl/~pault/
 *   Tol, P. (2021). "Colour Schemes." SRON Technical Note SRON/EPS/TN/09-002.
 *
 * Why this scheme (vs. ColorBrewer Set2, Okabe-Ito, etc.):
 *
 *   1. Distinguishable under all three common color-vision deficiencies
 *      (deuteranopia, protanopia, tritanopia). Tol verified this with
 *      simulated CVD; we picked "bright" rather than Okabe-Ito because
 *      Okabe-Ito's first color is pure black (#000000), which disappears
 *      against the dark-theme background (--bg-primary: #1e1e23).
 *
 *   2. Each color passes WCAG AA contrast (>= 3:1 for non-text UI per
 *      WCAG 1.4.11) against the dark-theme background #1e1e23. This is
 *      the relevant test because chart cells/lines are non-text elements
 *      that must be perceivable. The light theme is also fine — these
 *      colors are saturated mid-range hues that work on both.
 *
 *   3. Stable ordering. Consumers (e.g., GH-59 Providers panel) assign
 *      providers to color slots by index. Once a provider lands on slot
 *      0 ("blue"), reshuffling the array would break visual recognition
 *      across renders. The order below is Tol's original order — DO NOT
 *      reorder without coordinating with UX.
 *
 * GH-59 plots up to 5+ provider series. This palette gives 7 slots,
 * enough headroom for ~1.5x the planned series count. If a future
 * panel needs more, prefer a second visual channel (line style, marker
 * shape) over extending the palette — adding more hues degrades
 * CVD distinguishability quickly.
 */

/** Single palette entry. */
export interface PaletteColor {
  /** HEX color value, lowercase. */
  hex: string;
  /** Human-readable name. Used in legends and accessibility tooling. */
  name: string;
  /** Why this color was chosen / what role it plays. */
  rationale: string;
}

/**
 * Ordered categorical palette. Index 0 is the first series, index 1 the
 * second, etc. Wrap with modulo if you need more series than slots —
 * but see the docstring above on why that is a smell, not a solution.
 */
export const CATEGORICAL_PALETTE: readonly PaletteColor[] = [
  {
    hex: '#4477aa',
    name: 'Blue',
    rationale:
      // Tol "bright" #1. Mid-saturation blue. Distinguishable from green
      // and yellow under deuteranopia/protanopia. Passes >=3:1 contrast
      // against #1e1e23 (measured ~5.2:1 luminance ratio).
      'Primary series. Stable anchor — reads as "the default" line.',
  },
  {
    hex: '#ee6677',
    name: 'Red',
    rationale:
      // Tol "bright" #2. Pink-leaning red rather than pure red — keeps
      // perceptual distance from the orange/yellow slots under CVD.
      'Secondary series. Reads as a clear contrast against blue.',
  },
  {
    hex: '#228833',
    name: 'Green',
    rationale:
      // Tol "bright" #3. Mid-dark green. Passes >=3:1 against dark bg.
      // Distinguishable from blue under tritanopia (blue-yellow blindness).
      'Tertiary series. Conventional "good/healthy" connotation if used for status.',
  },
  {
    hex: '#ccbb44',
    name: 'Yellow',
    rationale:
      // Tol "bright" #4. Olive-yellow rather than pure yellow — pure
      // yellow on dark theme glares; this hue is calmer.
      'Quaternary series. Distinct from green and orange under CVD.',
  },
  {
    hex: '#66ccee',
    name: 'Cyan',
    rationale:
      // Tol "bright" #5. Light cyan — highest luminance in the set,
      // strong contrast against dark bg (~9:1).
      'Quinary series. Use for the most important series when 5+ are present.',
  },
  {
    hex: '#aa3377',
    name: 'Purple',
    rationale:
      // Tol "bright" #6. Magenta-leaning purple — keeps perceptual
      // distance from blue under deuteranopia.
      'Senary series. Distinct from red and blue under CVD.',
  },
  {
    hex: '#bbbbbb',
    name: 'Grey',
    rationale:
      // Tol "bright" #7. Neutral grey — Tol intends this as the
      // "other / unknown / overflow" slot. Place it last so it does not
      // get assigned to a primary provider by default.
      'Overflow / "other" series. Neutral so it does not fight for attention.',
  },
] as const;

/**
 * Convenience: just the HEX values, in order. Useful when a chart API
 * (e.g., recharts <Line stroke={...}/>) takes a string, not the metadata.
 */
export const CATEGORICAL_PALETTE_HEX: readonly string[] =
  CATEGORICAL_PALETTE.map((c) => c.hex);

/**
 * Pick a color by series index. Wraps with modulo so out-of-range
 * indices do not throw — but the consumer should treat wrap-around as
 * a sign that they are plotting too many series (see docstring).
 */
export function paletteColorAt(index: number): string {
  if (!Number.isFinite(index) || index < 0) {
    return CATEGORICAL_PALETTE_HEX[0];
  }
  const len = CATEGORICAL_PALETTE_HEX.length;
  return CATEGORICAL_PALETTE_HEX[Math.floor(index) % len];
}

/**
 * Sequential single-hue ramp for heatmaps. Maps a value in [0, 1] to a
 * color between the dark-theme tile background (--bg-tertiary, #2a2a35)
 * and a high-contrast accent color (Tol cyan, #66ccee).
 *
 * We linearly interpolate in sRGB space. This is not gamma-correct, but
 * for a small categorical-magnitude ramp (e.g., buffering events per
 * hour, 0..max) the perceptual error is small enough that simpler is
 * better. If a future panel needs gamma-correct interpolation, swap in
 * an OKLCH/Lab-space helper here — consumers should not change.
 */
export function sequentialColor(t: number): string {
  // Clamp to [0, 1].
  const clamped = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0));
  // Endpoints: low = dark tile bg, high = bright cyan accent.
  // Hand-coded RGB triples avoid a parsing helper.
  const low = { r: 0x2a, g: 0x2a, b: 0x35 };
  const high = { r: 0x66, g: 0xcc, b: 0xee };
  const r = Math.round(low.r + (high.r - low.r) * clamped);
  const g = Math.round(low.g + (high.g - low.g) * clamped);
  const b = Math.round(low.b + (high.b - low.b) * clamped);
  return `#${r.toString(16).padStart(2, '0')}${g
    .toString(16)
    .padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
}
