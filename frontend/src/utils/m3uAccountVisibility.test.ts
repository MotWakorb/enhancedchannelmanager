/**
 * Unit tests for the shared M3U account displayability predicate.
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
  // Files whose "custom" literals are unrelated to the hidden M3U account:
  // they are discriminated-union members / option values for other features.
  const UNRELATED_CUSTOM_LITERAL = [
    '/components/ChannelsPane.tsx', // channel-numbering option 'custom'
    '/components/moveChannelNumbering.ts', // NumberingOption union 'custom'
    '/components/patternBuilder/AnnotationPopover.tsx', // pattern variable type 'custom'
    '/components/patternBuilder/regexEngine.ts', // pattern variable type 'custom'
    '/components/patternBuilder/types.ts', // VariableType union 'custom'
  ];

  // Comments are stripped first: they may name the account in prose.
  const stripComments = (text: string) =>
    text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1');

  it('appears in no source file other than the helper', () => {
    const sources = import.meta.glob(['../**/*.ts', '../**/*.tsx', '!../**/*.test.ts', '!../**/*.test.tsx'], {
      query: '?raw',
      import: 'default',
      eager: true,
    }) as Record<string, string>;
    const customLiteral = /['"`]custom['"`]/i;
    const offenders = Object.entries(sources)
      .filter(([path]) => !path.endsWith('m3uAccountVisibility.ts'))
      .filter(([path]) => !UNRELATED_CUSTOM_LITERAL.some((allowed) => path.endsWith(allowed)))
      .filter(([, text]) => customLiteral.test(stripComments(text)))
      .map(([path]) => path);
    expect(Object.keys(sources).length).toBeGreaterThan(50);
    expect(offenders).toEqual([]);
  });
});
