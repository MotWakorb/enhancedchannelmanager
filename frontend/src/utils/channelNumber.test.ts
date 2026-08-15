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
