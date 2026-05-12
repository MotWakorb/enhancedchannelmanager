/**
 * Unit tests for the Heatmap chart primitive.
 *
 * Focus: bd-skqln.17 — chart primitive consumed by GH-59 Providers
 * panel. The contract under test:
 *
 *   1. Renders the right number of cells for an N×M input.
 *   2. Cell colors come from the value→color scale (custom override or
 *      the default sequential ramp).
 *   3. Empty data renders a graceful empty state, not a broken SVG.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Heatmap } from './Heatmap';

describe('Heatmap', () => {
  it('renders a cell for every (row, column) pair in an N×M input', () => {
    // 3 rows × 4 columns = 12 cells expected.
    const data = [
      [1, 2, 3, 4],
      [5, 6, 7, 8],
      [9, 10, 11, 12],
    ];
    const { container } = render(<Heatmap data={data} />);

    const cells = container.querySelectorAll('rect.heatmap-cell');
    expect(cells.length).toBe(12);
  });

  it('renders cells using the provided value→color scale', () => {
    // Pin every value to a known color so we can assert the fill
    // attribute deterministically. The default ramp is
    // implementation-detail; the override path is what consumers rely
    // on for theme overrides.
    const data = [
      [0, 100],
      [50, 25],
    ];
    const colorFor = (value: number): string => {
      if (value === 0) return '#000001';
      if (value === 25) return '#000002';
      if (value === 50) return '#000003';
      return '#000004'; // 100
    };

    const { container } = render(<Heatmap data={data} colorFor={colorFor} />);

    const cell00 = container.querySelector(
      '[data-testid="heatmap-cell-0-0"]',
    ) as SVGRectElement | null;
    const cell01 = container.querySelector(
      '[data-testid="heatmap-cell-0-1"]',
    ) as SVGRectElement | null;
    const cell10 = container.querySelector(
      '[data-testid="heatmap-cell-1-0"]',
    ) as SVGRectElement | null;
    const cell11 = container.querySelector(
      '[data-testid="heatmap-cell-1-1"]',
    ) as SVGRectElement | null;

    expect(cell00?.getAttribute('fill')).toBe('#000001');
    expect(cell01?.getAttribute('fill')).toBe('#000004');
    expect(cell10?.getAttribute('fill')).toBe('#000003');
    expect(cell11?.getAttribute('fill')).toBe('#000002');
  });

  it('renders the default sequential color ramp when no override is provided', () => {
    // Uniform data → every cell at normalized 0 → low endpoint of the
    // default ramp (#2a2a35).
    const { container } = render(<Heatmap data={[[5, 5, 5]]} />);
    const cells = container.querySelectorAll('rect.heatmap-cell');
    expect(cells.length).toBe(3);
    cells.forEach((cell) => {
      expect(cell.getAttribute('fill')).toBe('#2a2a35');
    });
  });

  it('renders the empty state when data is an empty array', () => {
    render(<Heatmap data={[]} />);
    expect(screen.getByRole('status')).toHaveTextContent(/no data available/i);
  });

  it('renders the empty state when every row is empty', () => {
    render(<Heatmap data={[[], [], []]} />);
    expect(screen.getByRole('status')).toHaveTextContent(/no data available/i);
  });

  it('truncates jagged rows to the shortest row to keep the grid rectangular', () => {
    // Row 0 has 4 columns; row 1 has 2. Rendered grid = 2 rows × 2 cols
    // = 4 cells.
    const data = [
      [1, 2, 3, 4],
      [5, 6],
    ];
    const { container } = render(<Heatmap data={data} />);
    const cells = container.querySelectorAll('rect.heatmap-cell');
    expect(cells.length).toBe(4);
  });

  it('exposes row/column labels and value via the <title> child for accessibility', () => {
    // <title> inside an <svg> is the WCAG-accessible name for that
    // element. We assert one specific cell's title to confirm the
    // label / value plumbing.
    const data = [[42]];
    const { container } = render(
      <Heatmap data={data} rowLabels={['Provider A']} columnLabels={['00:00']} />,
    );
    const cell = container.querySelector(
      '[data-testid="heatmap-cell-0-0"]',
    ) as SVGRectElement | null;
    const title = cell?.querySelector('title');
    expect(title?.textContent).toBe('Provider A / 00:00: 42');
  });

  it('uses the ariaLabel prop for the chart-level accessible name', () => {
    render(
      <Heatmap data={[[1, 2]]} ariaLabel="Buffering events by hour" />,
    );
    expect(
      screen.getByRole('img', { name: /buffering events by hour/i }),
    ).toBeInTheDocument();
  });
});
