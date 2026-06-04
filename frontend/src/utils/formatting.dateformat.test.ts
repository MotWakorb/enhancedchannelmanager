import { afterEach, describe, expect, it } from 'vitest';
import {
  getDateLocale,
  setDateFormatLocale,
  formatDateCompact,
} from './formatting';

// The active date-format preference is module-level global state (bd-8j47e),
// so reset it to the default after every test to avoid leaking the locale
// into unrelated specs.
afterEach(() => {
  setDateFormatLocale('auto');
});

describe('setDateFormatLocale / getDateLocale', () => {
  it('defaults to undefined (browser locale) for "auto"', () => {
    setDateFormatLocale('auto');
    expect(getDateLocale()).toBeUndefined();
  });

  it('maps "mdy" to en-US', () => {
    setDateFormatLocale('mdy');
    expect(getDateLocale()).toBe('en-US');
  });

  it('maps "dmy" to en-GB', () => {
    setDateFormatLocale('dmy');
    expect(getDateLocale()).toBe('en-GB');
  });

  it('maps "iso" to en-CA', () => {
    setDateFormatLocale('iso');
    expect(getDateLocale()).toBe('en-CA');
  });

  it('falls back to browser locale for null/undefined/unknown values', () => {
    setDateFormatLocale('mdy');
    setDateFormatLocale(null);
    expect(getDateLocale()).toBeUndefined();

    setDateFormatLocale('dmy');
    setDateFormatLocale(undefined);
    expect(getDateLocale()).toBeUndefined();

    setDateFormatLocale('iso');
    setDateFormatLocale('not-a-real-format');
    expect(getDateLocale()).toBeUndefined();
  });
});

describe('formatDateCompact honors the active date format', () => {
  // 2026-06-04 — a date whose day (04) and month (06) differ, so the
  // ordering is unambiguous between US (m/d) and UK (d/m).
  const iso = '2026-06-04T12:00:00Z';

  it('renders US ordering with month before day for "mdy"', () => {
    setDateFormatLocale('mdy');
    // en-CA would render "2026", en-GB "4 Jun"; en-US renders "Jun 4".
    expect(formatDateCompact(iso)).toMatch(/Jun 4/);
  });

  it('renders ISO year-first ordering for "iso"', () => {
    setDateFormatLocale('iso');
    expect(formatDateCompact(iso)).toMatch(/2026/);
  });

  it('returns "Never" for null regardless of format', () => {
    setDateFormatLocale('dmy');
    expect(formatDateCompact(null)).toBe('Never');
  });
});
