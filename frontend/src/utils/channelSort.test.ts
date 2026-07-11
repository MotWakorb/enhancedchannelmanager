/**
 * Unit tests for channelSort — the shared comparator + name-normalization
 * helper consumed by ChannelsPane.tsx's manual Sort & Renumber modal, and
 * ported to the backend for the sort_group pipeline action (see
 * backend/channel_pipeline_sort.py and its test,
 * backend/tests/unit/test_channel_pipeline_sort.py — fixtures are kept in
 * sync between the two).
 */
import { describe, it, expect } from 'vitest';
import {
  compareChannelNames,
  getNameForSorting,
  sortByChannelName,
  stripCountryPrefix,
} from './channelSort';

describe('getNameForSorting', () => {
  it('strips a numeric prefix', () => {
    expect(getNameForSorting('123 | ESPN')).toBe('ESPN');
  });

  it('strips a numeric suffix', () => {
    expect(getNameForSorting('ESPN | 123')).toBe('ESPN');
  });

  it('strips a mid-position number', () => {
    // See the module doc comment: the captured pipe/spaces are retained,
    // so the result keeps "| -" rather than collapsing to a bare dash.
    expect(getNameForSorting('US | 5034 - ESPN')).toBe('US | - ESPN');
  });

  it('returns the name unchanged when there is no number', () => {
    expect(getNameForSorting('ESPN')).toBe('ESPN');
  });
});

describe('stripCountryPrefix', () => {
  it('strips a pipe-separated prefix', () => {
    expect(stripCountryPrefix('US | ESPN')).toBe('ESPN');
  });

  it('strips a colon-separated prefix', () => {
    expect(stripCountryPrefix('UK: BBC One')).toBe('BBC One');
  });

  it('strips a dash-separated prefix', () => {
    expect(stripCountryPrefix('CA - CBC')).toBe('CBC');
  });

  it('strips a no-separator prefix', () => {
    expect(stripCountryPrefix('US ESPN')).toBe('ESPN');
  });

  it('leaves names without a country prefix unchanged', () => {
    expect(stripCountryPrefix('ESPN')).toBe('ESPN');
  });

  it('does not strip a lowercase two-letter prefix', () => {
    expect(stripCountryPrefix('us | ESPN')).toBe('us | ESPN');
  });
});

describe('compareChannelNames', () => {
  it('sorts "Channel 2" before "Channel 10" ascending (natural sort)', () => {
    expect(compareChannelNames('Channel 2', 'Channel 10')).toBeLessThan(0);
  });

  it('sorts "Channel 10" before "Channel 2" descending', () => {
    expect(compareChannelNames('Channel 2', 'Channel 10', { order: 'desc' })).toBeGreaterThan(0);
  });

  it('is case-insensitive', () => {
    expect(compareChannelNames('espn', 'ESPN')).toBe(0);
  });

  it('ignores embedded numbers when stripNumbers is true (default)', () => {
    expect(compareChannelNames('100 | ESPN', '5 | ESPN')).toBe(0);
  });

  it('does not ignore embedded numbers when stripNumbers is false', () => {
    // Without stripping, the raw strings compare via natural sort:
    // 100 > 5 numerically, so "100 | ESPN" sorts AFTER "5 | ESPN" — the
    // opposite of the stripNumbers:true case above, which treats them
    // as equal.
    expect(compareChannelNames('100 | ESPN', '5 | ESPN', { stripNumbers: false })).toBeGreaterThan(0);
  });

  it('ignores country prefix when ignoreCountry is true', () => {
    expect(compareChannelNames('US | ESPN', 'UK | ESPN', { ignoreCountry: true })).toBe(0);
  });

  it('does not ignore country prefix by default', () => {
    expect(compareChannelNames('US | ESPN', 'UK | ESPN')).not.toBe(0);
  });
});

describe('sortByChannelName', () => {
  interface Item {
    id: number;
    name: string;
  }

  it('sorts ascending by default', () => {
    const items: Item[] = [
      { id: 1, name: 'Channel 10' },
      { id: 2, name: 'Channel 2' },
    ];
    const sorted = sortByChannelName(items, (i) => i.name);
    expect(sorted.map((i) => i.id)).toEqual([2, 1]);
  });

  it('sorts descending', () => {
    const items: Item[] = [
      { id: 1, name: 'Channel 2' },
      { id: 2, name: 'Channel 10' },
    ];
    const sorted = sortByChannelName(items, (i) => i.name, { order: 'desc' });
    expect(sorted.map((i) => i.id)).toEqual([2, 1]);
  });

  it('does not mutate the input array', () => {
    const items: Item[] = [
      { id: 1, name: 'Zeta' },
      { id: 2, name: 'Alpha' },
    ];
    const original = [...items];
    sortByChannelName(items, (i) => i.name);
    expect(items).toEqual(original);
  });

  it('preserves relative order of ties in descending mode (stable sort)', () => {
    const items: Item[] = [
      { id: 1, name: '100 | ESPN' },
      { id: 2, name: '200 | ESPN' },
      { id: 3, name: 'Zeta' },
    ];
    const sorted = sortByChannelName(items, (i) => i.name, { order: 'desc' });
    // "Zeta" sorts after (stripped) "ESPN", so it comes first descending;
    // the two ESPN entries (equal keys) keep their original relative order.
    expect(sorted.map((i) => i.id)).toEqual([3, 1, 2]);
  });
});
