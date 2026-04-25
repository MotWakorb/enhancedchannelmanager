/**
 * Unit tests for the SMTP recipient parsing helpers extracted from
 * SettingsTab.tsx (bd-cp14f). These pin down the validation, dedup, and
 * paste-normalization rules introduced in PR #163.
 */
import { describe, it, expect } from 'vitest';

import {
  isValidHtml5EmailAddress,
  normalizeSmtpRecipientsPaste,
  parseSmtpRecipients,
} from './smtpRecipients';

// ---------------------------------------------------------------------------
// isValidHtml5EmailAddress
// ---------------------------------------------------------------------------
describe('isValidHtml5EmailAddress', () => {
  it('accepts a plain address', () => {
    expect(isValidHtml5EmailAddress('alice@example.com')).toBe(true);
  });

  it('accepts a plus-tag address', () => {
    expect(isValidHtml5EmailAddress('alice+tag@x.co')).toBe(true);
  });

  it('rejects an address without an @', () => {
    expect(isValidHtml5EmailAddress('not-an-email')).toBe(false);
  });

  it('rejects an address with embedded CRLF (header-injection guard)', () => {
    expect(isValidHtml5EmailAddress('alice@x.co\r\nBcc:x')).toBe(false);
  });

  it('rejects an address with an embedded LF', () => {
    expect(isValidHtml5EmailAddress('alice@x.co\nBcc:x')).toBe(false);
  });

  it('rejects the empty string', () => {
    expect(isValidHtml5EmailAddress('')).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// parseSmtpRecipients
// ---------------------------------------------------------------------------
describe('parseSmtpRecipients', () => {
  it('parses a single valid address', () => {
    const result = parseSmtpRecipients('alice@example.com');
    expect(result.recipients).toEqual(['alice@example.com']);
    expect(result.normalized).toBe('alice@example.com');
    expect(result.invalid).toBeUndefined();
    expect(result.dedupedCount).toBe(0);
  });

  it('accepts a plus-tag address', () => {
    const result = parseSmtpRecipients('alice+tag@x.co');
    expect(result.recipients).toEqual(['alice+tag@x.co']);
    expect(result.invalid).toBeUndefined();
  });

  it('flags the first invalid token and returns the raw input', () => {
    const result = parseSmtpRecipients('not-an-email');
    expect(result.recipients).toEqual([]);
    expect(result.invalid).toBe('not-an-email');
    expect(result.normalized).toBe('not-an-email');
    expect(result.dedupedCount).toBe(0);
  });

  it('rejects header-injection payloads via CRLF in a token', () => {
    const result = parseSmtpRecipients('alice@x.co\r\nBcc:x');
    expect(result.recipients).toEqual([]);
    expect(result.invalid).toBe('alice@x.co\r\nBcc:x');
  });

  it('dedupes case-insensitively, preserving first-seen casing', () => {
    const result = parseSmtpRecipients('A@x.co, a@x.co');
    expect(result.recipients).toEqual(['A@x.co']);
    expect(result.normalized).toBe('A@x.co');
    expect(result.dedupedCount).toBe(1);
  });

  it('counts each duplicate in dedupedCount', () => {
    const result = parseSmtpRecipients('a@x.co, A@x.co, b@x.co, B@x.co');
    expect(result.recipients).toEqual(['a@x.co', 'b@x.co']);
    expect(result.dedupedCount).toBe(2);
  });

  it('returns empty for empty input', () => {
    const result = parseSmtpRecipients('');
    expect(result.recipients).toEqual([]);
    expect(result.normalized).toBe('');
    expect(result.invalid).toBeUndefined();
    expect(result.dedupedCount).toBe(0);
  });

  it('returns empty for whitespace-only input', () => {
    const result = parseSmtpRecipients('   ,  , ');
    expect(result.recipients).toEqual([]);
    expect(result.normalized).toBe('');
    expect(result.invalid).toBeUndefined();
    expect(result.dedupedCount).toBe(0);
  });

  it('trims whitespace around tokens', () => {
    const result = parseSmtpRecipients('  alice@x.co ,  bob@x.co  ');
    expect(result.recipients).toEqual(['alice@x.co', 'bob@x.co']);
    expect(result.normalized).toBe('alice@x.co, bob@x.co');
  });
});

// ---------------------------------------------------------------------------
// normalizeSmtpRecipientsPaste
// ---------------------------------------------------------------------------
describe('normalizeSmtpRecipientsPaste', () => {
  it('passes through commas-only input untouched', () => {
    const result = normalizeSmtpRecipientsPaste('alice@x.co, bob@x.co');
    expect(result.needsRewrite).toBe(false);
    expect(result.normalized).toBe('alice@x.co, bob@x.co');
  });

  it('rewrites semicolon separators to ", "', () => {
    const result = normalizeSmtpRecipientsPaste('alice@x.co;bob@x.co');
    expect(result.needsRewrite).toBe(true);
    expect(result.normalized).toBe('alice@x.co, bob@x.co');
  });

  it('rewrites newline separators to ", "', () => {
    const result = normalizeSmtpRecipientsPaste('alice@x.co\nbob@x.co');
    expect(result.needsRewrite).toBe(true);
    expect(result.normalized).toBe('alice@x.co, bob@x.co');
  });

  it('rewrites mixed ";"/"\\n"/"\\r" separators in one pass', () => {
    const result = normalizeSmtpRecipientsPaste('alice@x.co;bob@x.co\r\ncarol@x.co');
    expect(result.needsRewrite).toBe(true);
    expect(result.normalized).toBe('alice@x.co, bob@x.co, carol@x.co');
  });

  it('collapses runs of separators into a single ", "', () => {
    const result = normalizeSmtpRecipientsPaste('alice@x.co;;\n\nbob@x.co');
    expect(result.needsRewrite).toBe(true);
    expect(result.normalized).toBe('alice@x.co, bob@x.co');
  });

  it('flags rewrite for an empty string only when separators are present', () => {
    const result = normalizeSmtpRecipientsPaste('');
    expect(result.needsRewrite).toBe(false);
    expect(result.normalized).toBe('');
  });
});
