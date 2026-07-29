import { describe, expect, it } from 'vitest';
import { formatConnections } from './statsConnections';

/**
 * Three states that are easy to conflate, and one of them was wrong: a provider
 * with `max_streams: 0` has *unlimited* capacity, but printed as "0/0" it read
 * as a provider with no capacity at all.
 */
describe('formatConnections', () => {
  it('shows unlimited capacity as infinity, not as zero', () => {
    expect(formatConnections(0, 0)).toBe('0/∞');
    expect(formatConnections(4, 0)).toBe('4/∞');
    // The failure this replaced.
    expect(formatConnections(0, 0)).not.toBe('0/0');
  });

  it('shows a real limit as a ratio', () => {
    expect(formatConnections(0, 10)).toBe('0/10');
    expect(formatConnections(7, 10)).toBe('7/10');
  });

  // bd-lhxfu: the Unknown bucket has no upstream capacity to report, so it must
  // not gain an invented denominator — including an infinity one.
  it('leaves the unknown bucket as a bare count', () => {
    expect(formatConnections(3, null)).toBe('3');
    expect(formatConnections(0, null)).toBe('0');
  });
});
