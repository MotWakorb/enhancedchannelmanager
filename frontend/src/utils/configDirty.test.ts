import { describe, it, expect } from 'vitest';
import { stableStringify, sameConfig } from './configDirty';

describe('configDirty', () => {
  it('treats object key order as irrelevant', () => {
    expect(sameConfig({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(true);
    expect(stableStringify({ b: 2, a: 1 })).toBe('{"a":1,"b":2}');
  });

  it('treats array order as significant', () => {
    expect(sameConfig([1, 2], [2, 1])).toBe(false);
  });

  it('recurses into nested objects and arrays', () => {
    const a = { patterns: [{ name: 'x', title_pattern: 't' }], master: { group_id: 1, m3u_account_id: null } };
    const b = { master: { m3u_account_id: null, group_id: 1 }, patterns: [{ title_pattern: 't', name: 'x' }] };
    expect(sameConfig(a, b)).toBe(true);
  });

  it('detects a changed scalar value', () => {
    expect(sameConfig({ attach_threshold: 0.8 }, { attach_threshold: 0.9 })).toBe(false);
  });

  it('distinguishes a present-but-null key from an absent key', () => {
    expect(sameConfig({ a: null }, {})).toBe(false);
  });

  it('handles null configs', () => {
    expect(sameConfig(null, null)).toBe(true);
    expect(sameConfig(null, { a: 1 })).toBe(false);
  });
});
