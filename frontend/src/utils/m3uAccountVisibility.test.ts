/**
 * Unit tests for the shared M3U account displayability predicate (bead 381gu).
 */
import { describe, it, expect } from 'vitest';

import {
  isActiveDisplayableM3UAccount,
  isDisplayableM3UAccount,
} from './m3uAccountVisibility';

describe('isDisplayableM3UAccount', () => {
  it('hides the "custom" pseudo-account', () => {
    expect(isDisplayableM3UAccount({ name: 'custom' })).toBe(false);
  });

  it.each(['Custom', 'CUSTOM', 'cUsToM'])('hides %s case-insensitively', (name) => {
    expect(isDisplayableM3UAccount({ name })).toBe(false);
  });

  it('shows ordinary accounts', () => {
    expect(isDisplayableM3UAccount({ name: 'Standard Playlist' })).toBe(true);
  });

  it('matches the whole name only, not a substring', () => {
    expect(isDisplayableM3UAccount({ name: 'custom playlist' })).toBe(true);
    expect(isDisplayableM3UAccount({ name: 'my-custom' })).toBe(true);
  });

  it('does not trim: a padded name is a different name', () => {
    expect(isDisplayableM3UAccount({ name: ' custom ' })).toBe(true);
  });
});

describe('isActiveDisplayableM3UAccount', () => {
  it('accepts an active, ordinary account', () => {
    expect(isActiveDisplayableM3UAccount({ name: 'A', is_active: true })).toBe(true);
  });

  it('rejects an inactive ordinary account', () => {
    expect(isActiveDisplayableM3UAccount({ name: 'A', is_active: false })).toBe(false);
  });

  it('rejects an active "custom" account', () => {
    expect(isActiveDisplayableM3UAccount({ name: 'Custom', is_active: true })).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Single-definition guard: no consumer may re-inline the name check.
// ---------------------------------------------------------------------------
describe('single definition of the "custom" account check', () => {
  it('appears in no source file other than the helper', () => {
    const sources = import.meta.glob(['../**/*.ts', '../**/*.tsx', '!../**/*.test.ts', '!../**/*.test.tsx'], {
      query: '?raw',
      import: 'default',
      eager: true,
    }) as Record<string, string>;
    const inlineCheck = /toLowerCase\(\)\s*[!=]==?\s*['"]custom['"]/;
    const offenders = Object.entries(sources)
      .filter(([path]) => !path.endsWith('/utils/m3uAccountVisibility.ts'))
      .filter(([, text]) => inlineCheck.test(text))
      .map(([path]) => path);
    expect(Object.keys(sources).length).toBeGreaterThan(50);
    expect(offenders).toEqual([]);
  });
});
