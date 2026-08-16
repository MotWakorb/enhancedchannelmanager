import { describe, it, expect } from 'vitest';
import { computeAutoRename, nameCarriesChannelNumber } from './channelRename';

// PINS. Everything in this first block records behaviour that already shipped;
// the matcher extraction (bead enhancedchannelmanager-ic884.5) has to leave
// every one of them unchanged.
describe('computeAutoRename (pins)', () => {
  it('rewrites a leading number and keeps its separator', () => {
    expect(computeAutoRename('5 | ESPN', 5, 9)).toBe('9 | ESPN');
    expect(computeAutoRename('5 - ESPN', 5, 9)).toBe('9 - ESPN');
    expect(computeAutoRename('5: ESPN', 5, 9)).toBe('9 : ESPN');
    expect(computeAutoRename('5 ESPN', 5, 9)).toBe('9 ESPN');
  });

  it('rewrites a number in the middle', () => {
    expect(computeAutoRename('US | 5034 - DABL', 5034, 9)).toBe('US | 9 - DABL');
    expect(computeAutoRename('US | 5034: DABL', 5034, 9)).toBe('US | 9 : DABL');
  });

  it('rewrites a trailing number', () => {
    // The doubled space is pre-existing: the trailing pattern's `(.*)` is
    // greedy, so it keeps the space before the separator and the rebuild adds
    // another. Pinned as-is rather than tidied — it is what operators' names
    // already look like, and changing it is a separate product question.
    expect(computeAutoRename('ESPN | 5', 5, 9)).toBe('ESPN  | 9');
  });

  it('leaves a name with no number alone', () => {
    expect(computeAutoRename('ESPN', 5, 9)).toBeUndefined();
  });

  it('returns undefined when the name already carries the new number', () => {
    expect(computeAutoRename('9 | ESPN', 5, 9)).toBeUndefined();
  });

  it('returns undefined when the number is cleared, whatever the name is', () => {
    expect(computeAutoRename('5 | ESPN', 5, null)).toBeUndefined();
    expect(computeAutoRename('ESPN', 5, null)).toBeUndefined();
  });

  it('carries a one-decimal number through', () => {
    expect(computeAutoRename('5 | ESPN', 5, 9.1)).toBe('9.1 | ESPN');
    expect(computeAutoRename('5.1 | ESPN', 5.1, 9)).toBe('9 | ESPN');
  });
});

describe('nameCarriesChannelNumber', () => {
  it('is true for every shape the rename recognises', () => {
    expect(nameCarriesChannelNumber('5 | ESPN')).toBe(true);
    expect(nameCarriesChannelNumber('US | 5034 - DABL')).toBe(true);
    expect(nameCarriesChannelNumber('ESPN | 5')).toBe(true);
    expect(nameCarriesChannelNumber('5.1 ESPN')).toBe(true);
  });

  it('is false for a name with no number in it', () => {
    expect(nameCarriesChannelNumber('ESPN')).toBe(false);
    expect(nameCarriesChannelNumber('BBC News')).toBe(false);
  });

  it('agrees with computeAutoRename on every name', () => {
    // The property the extraction exists to guarantee: a name the rename would
    // rewrite is exactly a name that carries a number. Probed with two
    // different targets so a name that already holds one of them still counts.
    const names = [
      '5 | ESPN',
      'US | 5034 - DABL',
      'ESPN | 5',
      'ESPN',
      '1 | One',
      '2 | Two',
      'BBC News HD',
      '10.1 - Local',
    ];
    for (const name of names) {
      const renames =
        computeAutoRename(name, null, 1) !== undefined ||
        computeAutoRename(name, null, 2) !== undefined;
      expect(nameCarriesChannelNumber(name)).toBe(renames);
    }
  });
});
