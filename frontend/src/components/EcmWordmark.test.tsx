import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { EcmWordmark } from './EcmWordmark';

describe('EcmWordmark', () => {
  // The sidebar shows the product name only as this graphic, so it has to carry
  // the name itself rather than being decorative like the grid mark.
  it('announces the full product name', () => {
    const { container } = render(<EcmWordmark />);
    const svg = container.querySelector('svg')!;
    expect(svg).toHaveAttribute('role', 'img');
    expect(svg).toHaveAttribute('aria-label', 'Enhanced Channel Manager');
  });

  // The whole point of the lockup: both lines and the rule share one width.
  // textLength is what enforces it across platforms, so it must stay on both.
  it('forces both lines and the rule to a single shared width', () => {
    const { container } = render(<EcmWordmark />);
    const texts = [...container.querySelectorAll('text')];
    const rule = container.querySelector('rect')!;

    expect(texts).toHaveLength(2);
    const widths = new Set(texts.map((t) => t.getAttribute('textLength')));
    widths.add(rule.getAttribute('width'));
    expect(widths.size).toBe(1);

    for (const text of texts) {
      expect(text).toHaveAttribute('lengthAdjust', 'spacing');
    }
  });

  it('sets the second line in capitals', () => {
    const { container } = render(<EcmWordmark />);
    const [first, second] = [...container.querySelectorAll('text')];
    expect(first.textContent).toBe('Enhanced');
    expect(second.textContent).toBe('CHANNEL MANAGER');
    expect(second.textContent).toBe(second.textContent!.toUpperCase());
  });

  it('italicises only the first line', () => {
    const { container } = render(<EcmWordmark />);
    const [first, second] = [...container.querySelectorAll('text')];
    expect(first).toHaveAttribute('font-style', 'italic');
    expect(second).not.toHaveAttribute('font-style');
  });

  it('scales from one viewBox so the lockup cannot fall out of proportion', () => {
    const plain = render(<EcmWordmark />).container.querySelector('svg')!;
    const scaled = render(<EcmWordmark scale={2} />).container.querySelector('svg')!;

    expect(scaled.getAttribute('viewBox')).toBe(plain.getAttribute('viewBox'));
    expect(Number(scaled.getAttribute('width'))).toBeCloseTo(Number(plain.getAttribute('width')) * 2);
    expect(Number(scaled.getAttribute('height'))).toBeCloseTo(Number(plain.getAttribute('height')) * 2);
  });
});
