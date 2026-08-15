/**
 * Canonical channel-number contract (bead `enhancedchannelmanager-ic884.1`).
 *
 * These tests pin the domain itself. The tests that prove each entry point
 * rejects and renders the message live next to their components.
 */
import { describe, it, expect } from 'vitest';
import {
  CHANNEL_NUMBER_RULE_MESSAGE,
  channelNumberInputError,
  isValidChannelNumber,
  parseChannelNumberInput,
} from './channelNumber';

describe('isValidChannelNumber', () => {
  it.each([0, 0.0, 1, 1.0, 7, 38, 0.1, 1.1, 0.9, 38.4, 999.9, 100000, 100000.5, 1_000_000_000])(
    'accepts the in-contract value %s',
    (value) => {
      expect(isValidChannelNumber(value)).toBe(true);
    },
  );

  it.each([1.05, 0.05, 1.15, 1.01, 1.001, 1.234, 2.0001, -1, -0.1, NaN, Infinity, -Infinity])(
    'rejects the out-of-contract value %s',
    (value) => {
      expect(isValidChannelNumber(value)).toBe(false);
    },
  );

  it.each([null, undefined, '1', '1.0', 'abc', true, false, {}, []])(
    'rejects the non-numeric value %s',
    (value) => {
      expect(isValidChannelNumber(value)).toBe(false);
    },
  );

  it.each([1e15, 2 ** 53, 1e307, 1e308, Number.MAX_VALUE])(
    'answers rather than overflowing at the float limit %s',
    (value) => {
      // Scaling the whole value by ten reaches Infinity near Number.MAX_VALUE,
      // and Infinity - Infinity is NaN, which fails every comparison, so the
      // old form silently reported these as out of contract while
      // `backend/channel_number.py` reports them as in contract. The two halves
      // are documented as enforcing the identical rule, so they must agree.
      // Every float at or above 2**53 is an exact integer and carries no
      // fractional part, and the contract names no maximum.
      expect(isValidChannelNumber(value)).toBe(true);
    },
  );

  it.each([100000.1, 1000000.1, 10000000.1, 10000000.5, 123456789.9, 1e12 + 0.1, 1e12 + 0.5, 1e14 + 0.1])(
    'accepts the large-magnitude one-decimal value %s',
    (value) => {
      // Scaling only the fractional part to dodge the overflow near
      // Number.MAX_VALUE threw away the precision these depend on:
      // `10000000.1 % 1` is 0.09999999962747097, whose scaled distance from a
      // whole tenth is 3.7e-9 -- past the 1e-9 tolerance, so 10000000.1 was
      // rejected by both halves of the stack. Scaling the whole value returns
      // 100000001 exactly. The float-limit cases above could not catch this:
      // huge integers have no fractional part, so nothing was there to lose.
      expect(isValidChannelNumber(value)).toBe(true);
    },
  );

  it('accepts every one-decimal value across the representable range', () => {
    // A one-decimal value is exactly what `Number('N.M')` produces, so the
    // population to sweep is k / 10 for integer k. Scaling back by ten has to
    // return k for every one of them, and the deviation is not merely inside
    // the tolerance but exactly zero -- which is why the tolerance can stay
    // absolute. It does no work here and is reserved for arithmetic dust.
    const step = 7; // coprime with 10, so every tenths digit is exercised
    for (let exponent = 0; exponent <= 16; exponent += 1) {
      const low = 10 ** exponent;
      for (let offset = 0; offset < 2000; offset += 1) {
        const value = (low + offset * step) / 10;
        if (value >= 2 ** 53) break;
        expect(isValidChannelNumber(value)).toBe(true);
        expect(Math.abs(value * 10 - Math.round(value * 10))).toBe(0);
      }
    }
  });

  it.each([2 ** 53 - 2, 2 ** 53, 2 ** 53 + 2, 2 ** 52, 2 ** 52 + 0.5])(
    'answers consistently either side of the exact-integer floor for %s',
    (value) => {
      // Below 2**53 the value is scaled and compared; at and above it the
      // answer is returned directly. Every value straddling that boundary is
      // an exact integer, so both paths must agree. `2 ** 52 + 0.5` makes the
      // point that this is representability rather than the branch: float
      // spacing at 2**52 is already 1, so that expression IS 2**52.
      expect(isValidChannelNumber(value)).toBe(true);
    },
  );

  it('rejects a value that has no representation as a number', () => {
    // The backend's half of this: Python's `int` is arbitrary precision, so a
    // JSON body can carry 10**400. It reaches this half already collapsed to
    // Infinity, and `backend/channel_number.py` answers false rather than
    // raising OverflowError out of `float()`, so the two halves agree on an
    // input either side can be handed.
    expect(Number('1e400')).toBe(Infinity);
    expect(isValidChannelNumber(Number('1e400'))).toBe(false);
    // Representability, not magnitude: 1e308 does have a representation.
    expect(isValidChannelNumber(Number('1e308'))).toBe(true);
  });

  it('tolerates binary-float dust without admitting a half-tenth', () => {
    // 0.7 + 0.1 is 0.7999999999999999 and 0.2 + 0.1 is 0.30000000000000004.
    // Both are the channel numbers 0.8 and 0.3. 1.05 is a real two-decimal
    // value and must stay rejected, which is the boundary that matters most.
    expect(0.7 + 0.1).not.toBe(0.8);
    expect(isValidChannelNumber(0.7 + 0.1)).toBe(true);
    expect(0.2 + 0.1).not.toBe(0.3);
    expect(isValidChannelNumber(0.2 + 0.1)).toBe(true);
    expect(isValidChannelNumber(1.05)).toBe(false);
  });

  it('rejects the half-tenth at every magnitude that can hold one', () => {
    // A half-tenth is the nearest out-of-contract value to a tenth, so it is
    // the hardest thing to reject. Its scaled distance is exactly 0.5, a margin
    // of 5e8 over the 1e-9 tolerance, and that holds at every magnitude where
    // the half-tenth is a distinct number at all. It stops being one just above
    // 2**48: spacing reaches 0.125 at 2**49, which exceeds the 0.05 gap, so
    // `base + 0.05` is simply `base` there and is correctly accepted.
    for (let exponent = 0; exponent <= 14; exponent += 1) {
      const base = 10 ** exponent;
      const value = base + 0.05;
      expect(value).not.toBe(base);
      expect(Math.abs(value * 10 - Math.round(value * 10))).toBe(0.5);
      expect(isValidChannelNumber(value)).toBe(false);
    }

    // Where the distinction dissolves, and why accepting is then correct.
    expect(2 ** 49 + 0.05).toBe(2 ** 49);
    expect(isValidChannelNumber(2 ** 49 + 0.05)).toBe(true);
    expect(2 ** 48 + 0.05).not.toBe(2 ** 48);
    expect(isValidChannelNumber(2 ** 48 + 0.05)).toBe(false);
  });
});

describe('parseChannelNumberInput', () => {
  it.each([
    ['0', 0],
    ['1', 1],
    ['7', 7],
    ['7.0', 7],
    ['07', 7],
    ['1.1', 1.1],
    ['1.10', 1.1],
    ['  38.4  ', 38.4],
    ['999.9', 999.9],
  ])('parses the in-contract text %s', (text, expected) => {
    expect(parseChannelNumberInput(text as string)).toEqual({ ok: true, value: expected });
  });

  it('treats canonical equivalents as the same number', () => {
    const seven = parseChannelNumberInput('7');
    expect(parseChannelNumberInput('7.0')).toEqual(seven);
    expect(parseChannelNumberInput('07')).toEqual(seven);
  });

  it.each(['', '   '])('treats empty text %s as unassigned', (text) => {
    expect(parseChannelNumberInput(text)).toEqual({ ok: true, value: null });
  });

  it('can refuse empty text when the field is required', () => {
    expect(parseChannelNumberInput('', { allowEmpty: false })).toEqual({
      ok: false,
      message: CHANNEL_NUMBER_RULE_MESSAGE,
    });
  });

  it.each([
    '1.05',
    '1.001',
    '-5',
    '-0.1',
    'abc',
    'NaN',
    'Infinity',
    '1e3',
    '+7',
    '7.',
    '.5',
    '1,5',
    '7 8',
  ])('rejects the out-of-contract text %s', (text) => {
    expect(parseChannelNumberInput(text)).toEqual({
      ok: false,
      message: CHANNEL_NUMBER_RULE_MESSAGE,
    });
  });

  it('rejects rather than rounding 1.05 to a neighbouring tenth', () => {
    const result = parseChannelNumberInput('1.05');
    expect(result.ok).toBe(false);
    expect(parseChannelNumberInput('1.0')).toEqual({ ok: true, value: 1 });
    expect(parseChannelNumberInput('1.1')).toEqual({ ok: true, value: 1.1 });
  });

  it('names the rule and gives a valid example', () => {
    expect(CHANNEL_NUMBER_RULE_MESSAGE).toContain('one decimal place');
    expect(CHANNEL_NUMBER_RULE_MESSAGE).toContain('1.1');
  });
});

describe('channelNumberInputError', () => {
  it('returns null for acceptable text', () => {
    expect(channelNumberInputError('1.1')).toBeNull();
    expect(channelNumberInputError('')).toBeNull();
  });

  it('returns the canonical message for unacceptable text', () => {
    expect(channelNumberInputError('1.05')).toBe(CHANNEL_NUMBER_RULE_MESSAGE);
  });
});
