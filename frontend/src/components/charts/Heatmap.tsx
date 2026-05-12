/**
 * Heatmap chart primitive (bd-skqln.17).
 *
 * Renders a 2D matrix (rows × columns) of numeric values as colored
 * cells. Built on hand-rolled SVG — recharts 3.8.1 (the project's only
 * chart dep) has no first-class heatmap, and the bead explicitly
 * de-scopes adding a heavyweight chart lib (@nivo, visx) for this.
 *
 * Use case (GH-59 Providers panel): per-provider buffering events by
 * time-of-day. Rows are providers, columns are hours of the day, cell
 * value is event count.
 *
 * Design choices:
 *
 *   - Cell color comes from `sequentialColor` in utils/chartPalette,
 *     which maps [0, 1] to the project's dark-theme accent ramp.
 *     Consumers can override by passing `colorFor`.
 *   - Empty data (no rows OR no columns OR every row empty) renders a
 *     graceful empty state, not an empty SVG — empty SVGs at zero size
 *     break flexbox parent layouts.
 *   - SVG is rendered at a fixed cell size by default; the parent
 *     container is responsible for scrolling at narrow viewports
 *     (per UX: "downgrades gracefully to scrollable cells at <1024px").
 *   - Cells have <title> children so screen readers and hover tooltips
 *     get the underlying numeric value (per UX WCAG 1.4.1: color must
 *     not be the sole channel carrying meaning).
 */
import './Heatmap.css';
import { sequentialColor } from '../../utils/chartPalette';

export interface HeatmapProps {
  /**
   * 2D array of numeric values. Outer array is rows; inner array is
   * columns. Rows may be different lengths — the heatmap renders only
   * up to the shortest row's column count to keep the grid rectangular.
   */
  data: readonly (readonly number[])[];

  /**
   * Labels for each row (provider names, etc.). If shorter than
   * data.length, extra rows render unlabeled.
   */
  rowLabels?: readonly string[];

  /**
   * Labels for each column (hour of day, day of week, etc.). If
   * shorter than the column count, extra columns render unlabeled.
   */
  columnLabels?: readonly string[];

  /**
   * Cell width and height in pixels. Default 32px — large enough for a
   * two-digit numeric overlay if the consumer adds one later.
   */
  cellSize?: number;

  /**
   * Optional color override. Receives (value, normalized) where
   * normalized is value mapped to [0, 1] across the full data range.
   * Default: sequentialColor(normalized).
   */
  colorFor?: (value: number, normalized: number) => string;

  /**
   * Accessible name for the chart. Rendered as <title> on the root SVG
   * so assistive tech announces it.
   */
  ariaLabel?: string;
}

/** Width reserved on the left for row labels, in pixels. */
const ROW_LABEL_WIDTH = 96;
/** Height reserved on the top for column labels, in pixels. */
const COLUMN_LABEL_HEIGHT = 24;

function isEmpty(data: readonly (readonly number[])[]): boolean {
  if (data.length === 0) return true;
  // If every row is empty, there is no column — treat as empty.
  return data.every((row) => row.length === 0);
}

function dataRange(
  data: readonly (readonly number[])[],
): { min: number; max: number } {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const row of data) {
    for (const value of row) {
      if (!Number.isFinite(value)) continue;
      if (value < min) min = value;
      if (value > max) max = value;
    }
  }
  // If no finite values found, fall back to a degenerate range so
  // colorFor receives 0 for everything.
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return { min: 0, max: 0 };
  }
  return { min, max };
}

export function Heatmap({
  data,
  rowLabels = [],
  columnLabels = [],
  cellSize = 32,
  colorFor,
  ariaLabel = 'Heatmap',
}: HeatmapProps) {
  if (isEmpty(data)) {
    return (
      <div className="heatmap-empty" role="status">
        No data available.
      </div>
    );
  }

  // Column count = shortest non-empty row, so the rendered grid is
  // rectangular even if the input is jagged.
  const columnCount = data.reduce(
    (acc, row) => Math.min(acc, row.length),
    Number.POSITIVE_INFINITY,
  );

  const { min, max } = dataRange(data);
  const range = max - min;

  // Default value→color mapping. Pulled out so consumer can override.
  const resolveColor = colorFor ?? ((_v: number, n: number) => sequentialColor(n));

  const width = ROW_LABEL_WIDTH + columnCount * cellSize;
  const height = COLUMN_LABEL_HEIGHT + data.length * cellSize;

  return (
    <div className="heatmap" data-testid="heatmap-root">
      <svg
        className="heatmap-svg"
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={ariaLabel}
      >
        <title>{ariaLabel}</title>

        {/* Column labels along the top */}
        {columnLabels.slice(0, columnCount).map((label, colIdx) => (
          <text
            key={`col-${colIdx}`}
            className="heatmap-col-label"
            x={ROW_LABEL_WIDTH + colIdx * cellSize + cellSize / 2}
            y={COLUMN_LABEL_HEIGHT - 6}
            textAnchor="middle"
          >
            {label}
          </text>
        ))}

        {/* Row labels down the left side */}
        {data.map((_row, rowIdx) => (
          <text
            key={`row-${rowIdx}`}
            className="heatmap-row-label"
            x={ROW_LABEL_WIDTH - 6}
            y={COLUMN_LABEL_HEIGHT + rowIdx * cellSize + cellSize / 2 + 4}
            textAnchor="end"
          >
            {rowLabels[rowIdx] ?? ''}
          </text>
        ))}

        {/* Cells */}
        {data.map((row, rowIdx) =>
          row.slice(0, columnCount).map((value, colIdx) => {
            const normalized = range === 0 ? 0 : (value - min) / range;
            const fill = resolveColor(value, normalized);
            const x = ROW_LABEL_WIDTH + colIdx * cellSize;
            const y = COLUMN_LABEL_HEIGHT + rowIdx * cellSize;
            const rowName = rowLabels[rowIdx] ?? `Row ${rowIdx + 1}`;
            const colName = columnLabels[colIdx] ?? `Col ${colIdx + 1}`;
            return (
              <rect
                key={`cell-${rowIdx}-${colIdx}`}
                className="heatmap-cell"
                data-testid={`heatmap-cell-${rowIdx}-${colIdx}`}
                data-value={value}
                x={x}
                y={y}
                width={cellSize}
                height={cellSize}
                fill={fill}
              >
                {/* <title> gives the browser native hover tooltip and
                    is announced by screen readers. WCAG 1.4.1: color
                    must not be the sole channel carrying meaning. */}
                <title>{`${rowName} / ${colName}: ${value}`}</title>
              </rect>
            );
          }),
        )}
      </svg>
    </div>
  );
}
