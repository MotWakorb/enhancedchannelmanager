import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { formatRelativeTime } from './formatting';

// bd-juy2e: formatRelativeTime is the one recency-threshold rule shared by
// M3UChangesTab and JournalTab (previously each table had its own drifted
// copy, or — for Journal — no relative-time handling at all). Pin "now" so
// the thresholds are deterministic.
const NOW = new Date('2026-07-10T12:00:00Z');

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

function isoMinutesAgo(minutes: number): string {
  return new Date(NOW.getTime() - minutes * 60_000).toISOString();
}

function isoDaysAgo(days: number): string {
  return new Date(NOW.getTime() - days * 86_400_000).toISOString();
}

describe('formatRelativeTime', () => {
  it('returns "just now" for under a minute', () => {
    expect(formatRelativeTime(isoMinutesAgo(0))).toBe('just now');
  });

  it('capitalizes "Just now" when capitalize=true', () => {
    expect(formatRelativeTime(isoMinutesAgo(0), true)).toBe('Just now');
  });

  it('returns "Xm ago" under an hour', () => {
    expect(formatRelativeTime(isoMinutesAgo(5))).toBe('5m ago');
    expect(formatRelativeTime(isoMinutesAgo(59))).toBe('59m ago');
  });

  it('returns "Xh ago" under a day', () => {
    expect(formatRelativeTime(isoMinutesAgo(90))).toBe('1h ago');
    expect(formatRelativeTime(isoMinutesAgo(23 * 60 + 30))).toBe('23h ago');
  });

  it('returns "Xd ago" under a week', () => {
    expect(formatRelativeTime(isoDaysAgo(1))).toBe('1d ago');
    expect(formatRelativeTime(isoDaysAgo(6))).toBe('6d ago');
  });

  it('falls back to an absolute date at 7 days and beyond', () => {
    const result = formatRelativeTime(isoDaysAgo(7));
    expect(result).not.toMatch(/ago$/);
    // e.g. "Jul 3, 12:00 PM" — month + day present, no relative suffix.
    expect(result).toMatch(/[A-Z][a-z]{2} \d{1,2}/);
  });

  it('is consistent regardless of the capitalize flag once past the threshold', () => {
    const plain = formatRelativeTime(isoDaysAgo(30));
    const capitalized = formatRelativeTime(isoDaysAgo(30), true);
    // capitalize only affects the "just now" string; absolute dates are
    // already capitalized by Intl and should be identical either way.
    expect(plain).toBe(capitalized);
  });
});
