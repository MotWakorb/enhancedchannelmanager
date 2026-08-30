import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { EcmLogo } from './EcmLogo';

const rightEdge = (rect: Element) =>
  Number(rect.getAttribute('x')) + Number(rect.getAttribute('width'));

describe('EcmLogo', () => {
  it('is decorative — the wrapping control carries the accessible name', () => {
    const { container } = render(<EcmLogo />);
    const svg = container.querySelector('svg')!;
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg).toHaveAttribute('focusable', 'false');
    expect(svg).toHaveAttribute('viewBox', '0 0 32 32');
  });

  it('scales from a single viewBox rather than swapping artwork', () => {
    const { container } = render(<EcmLogo size={24} />);
    const svg = container.querySelector('svg')!;
    expect(svg).toHaveAttribute('width', '24');
    expect(svg).toHaveAttribute('height', '24');
    expect(svg).toHaveAttribute('viewBox', '0 0 32 32');
  });

  // The E only reads as a letter if its middle arm stops short of the other two.
  it('keeps the middle row shorter than the top and bottom rows', () => {
    const { container } = render(<EcmLogo />);
    const cells = [...container.querySelectorAll('rect')];
    const rowRight = (y: string) => Math.max(
      ...cells.filter((c) => c.getAttribute('y') === y).map(rightEdge),
    );

    const top = rowRight('4');
    const middle = rowRight('13.5');
    const bottom = rowRight('23');

    expect(top).toBe(bottom);
    expect(middle).toBeLessThan(top);
  });

  // Colour is what separates the C (outer path) and M (middle arm) readings from
  // the schedule, so the middle row must never be drawn in the ink class.
  it('draws the schedule in ink and the middle row in the accent', () => {
    const { container } = render(<EcmLogo />);
    const middleCells = [...container.querySelectorAll('rect')]
      .filter((c) => c.getAttribute('y') === '13.5');

    expect(container.querySelectorAll('.ecm-logo-ink')).toHaveLength(7);
    expect(container.querySelector('path.ecm-logo-accent')).toBeInTheDocument();
    for (const cell of middleCells) {
      expect(cell.getAttribute('class')).toMatch(/ecm-logo-accent/);
    }
  });

  it('rounds the M by stroking it, which is what rounds the counters', () => {
    const { container } = render(<EcmLogo />);
    const m = container.querySelector('path.ecm-logo-accent')!;
    expect(Number(m.getAttribute('stroke-width'))).toBeGreaterThan(0);
    expect(m).toHaveAttribute('stroke-linejoin', 'round');
    expect(m).toHaveAttribute('stroke-linecap', 'round');
  });
});
