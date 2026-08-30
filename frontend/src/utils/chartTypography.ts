/**
 * Chart typography — the JS half of the P1 type scale.
 *
 * recharts takes axis-tick size and colour as JS props (`tick={{ fontSize,
 * fill }}`), not CSS, so the role tokens in `index.css` cannot reach them.
 * Twenty-two call sites across the Stats panels were spelling the numbers
 * out inline, which meant the charts silently drifted whenever the scale
 * moved. These constants are the same numbers with the role they belong to
 * attached, so a future change to `--type-meta-size` has one obvious
 * counterpart here (bead enhancedchannelmanager-6z299.4).
 *
 * Keep these in step with the role tokens in `frontend/src/index.css`
 * (§ "Typography roles"). They are deliberately plain numbers: recharts
 * measures tick boxes arithmetically and cannot resolve a CSS custom
 * property.
 *
 * Colours stay as `var(--...)` strings — SVG `fill` does resolve custom
 * properties, so the charts still follow the theme.
 */

/** Axis-tick size for the `meta` role — matches `--type-meta-size` (11px). */
export const CHART_TICK_META = 11;

/** Axis-tick size for the `micro` role — matches `--type-micro-size` (10px). */
export const CHART_TICK_MICRO = 10;

/**
 * Fill for secondary chart text (axis ticks, unit labels).
 *
 * NOT `--text-muted`: it measures 2.61:1 on `--bg-secondary` in the light
 * theme, below the 4.5:1 AA floor. Every recharts tick in StatsTab.tsx and
 * EnhancedStatsPanel.tsx was passing it.
 */
export const CHART_TEXT_FILL = 'var(--text-secondary)';

/**
 * Fill for chart text that carries the reading, not the scaffolding —
 * axis-label captions and the ticks on the Providers / User Watch Time
 * charts, which have always read at full strength.
 */
export const CHART_TEXT_FILL_STRONG = 'var(--text-primary)';
