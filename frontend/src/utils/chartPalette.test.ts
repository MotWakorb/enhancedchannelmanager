/**
 * Unit tests for the categorical chart palette.
 *
 * Focus: bd-skqln.17 — colorblind-safe categorical palette for the
 * Stats v2 Providers panel (GH-59). The palette is consumed by index;
 * reordering or shrinking it is a breaking change for any panel that
 * has assigned providers to slots. The snapshot test below is the
 * canary that flags such a change in code review.
 */
import { describe, it, expect } from 'vitest';
import {
  CATEGORICAL_PALETTE,
  CATEGORICAL_PALETTE_HEX,
  paletteColorAt,
  sequentialColor,
} from './chartPalette';

describe('CATEGORICAL_PALETTE', () => {
  it('exposes a stable ordered list of HEX entries', () => {
    // Snapshot the HEX array specifically. If a future PR reorders or
    // removes an entry, this test fails and the reviewer must
    // explicitly accept the change. Names/rationales can drift; the
    // ordered HEX list is the contract.
    expect(CATEGORICAL_PALETTE_HEX).toEqual([
      '#4477aa',
      '#ee6677',
      '#228833',
      '#ccbb44',
      '#66ccee',
      '#aa3377',
      '#bbbbbb',
    ]);
  });

  it('provides at least 7 colors to cover GH-59 5+ provider series', () => {
    expect(CATEGORICAL_PALETTE.length).toBeGreaterThanOrEqual(7);
  });

  it('every entry has a valid 6-digit lowercase HEX value', () => {
    const hexRe = /^#[0-9a-f]{6}$/;
    for (const entry of CATEGORICAL_PALETTE) {
      expect(entry.hex).toMatch(hexRe);
    }
  });

  it('every entry has a non-empty name and rationale', () => {
    for (const entry of CATEGORICAL_PALETTE) {
      expect(entry.name.length).toBeGreaterThan(0);
      expect(entry.rationale.length).toBeGreaterThan(0);
    }
  });

  it('CATEGORICAL_PALETTE_HEX matches the order of CATEGORICAL_PALETTE', () => {
    expect(CATEGORICAL_PALETTE_HEX).toEqual(
      CATEGORICAL_PALETTE.map((c) => c.hex),
    );
  });
});

describe('paletteColorAt', () => {
  it('returns the entry at the given index', () => {
    expect(paletteColorAt(0)).toBe('#4477aa');
    expect(paletteColorAt(2)).toBe('#228833');
  });

  it('wraps with modulo for out-of-range indices', () => {
    const len = CATEGORICAL_PALETTE_HEX.length;
    expect(paletteColorAt(len)).toBe(paletteColorAt(0));
    expect(paletteColorAt(len + 3)).toBe(paletteColorAt(3));
  });

  it('returns the first slot for invalid input (negative, NaN, Infinity)', () => {
    expect(paletteColorAt(-1)).toBe('#4477aa');
    expect(paletteColorAt(Number.NaN)).toBe('#4477aa');
    expect(paletteColorAt(Number.POSITIVE_INFINITY)).toBe('#4477aa');
  });
});

describe('sequentialColor', () => {
  it('returns the low endpoint at t=0', () => {
    expect(sequentialColor(0)).toBe('#2a2a35');
  });

  it('returns the high endpoint at t=1', () => {
    expect(sequentialColor(1)).toBe('#66ccee');
  });

  it('returns a midpoint color between the endpoints at t=0.5', () => {
    // Midpoint of (0x2a, 0x2a, 0x35) and (0x66, 0xcc, 0xee) is
    // (0x48, 0x7b, 0x92) = "#487b92".
    expect(sequentialColor(0.5)).toBe('#487b92');
  });

  it('clamps values below 0 to the low endpoint', () => {
    expect(sequentialColor(-0.5)).toBe('#2a2a35');
  });

  it('clamps values above 1 to the high endpoint', () => {
    expect(sequentialColor(2)).toBe('#66ccee');
  });

  it('treats non-finite inputs (NaN, Infinity) as 0 and returns the low endpoint', () => {
    // The implementation guards with Number.isFinite, so any non-finite
    // value (NaN, +Infinity, -Infinity) becomes 0 before clamping.
    expect(sequentialColor(Number.NaN)).toBe('#2a2a35');
    expect(sequentialColor(Number.POSITIVE_INFINITY)).toBe('#2a2a35');
    expect(sequentialColor(Number.NEGATIVE_INFINITY)).toBe('#2a2a35');
  });
});
