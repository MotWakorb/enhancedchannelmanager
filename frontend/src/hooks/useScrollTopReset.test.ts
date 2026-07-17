/**
 * Tests for useScrollTopReset — resets a scroll container's scrollTop to 0
 * whenever a tracked key changes (e.g. Settings sub-page navigation).
 */
import { describe, it, expect } from 'vitest';
import { renderHook } from '@testing-library/react';
import { createRef } from 'react';
import { useScrollTopReset } from './useScrollTopReset';

describe('useScrollTopReset', () => {
  it('resets scrollTop to 0 when the key changes', () => {
    const ref = createRef<HTMLDivElement>();
    const div = document.createElement('div');
    // jsdom doesn't lay out content, so scrollTop is a plain assignable prop.
    div.scrollTop = 2340;
    (ref as { current: HTMLDivElement | null }).current = div;

    const { rerender } = renderHook(({ page }) => useScrollTopReset(ref, page), {
      initialProps: { page: 'notifications' },
    });
    expect(div.scrollTop).toBe(2340); // unaffected on initial mount at the same page

    div.scrollTop = 1431; // simulate user having scrolled mid-page
    rerender({ page: 'integrations' });

    expect(div.scrollTop).toBe(0);
  });

  it('does not reset scrollTop when the key stays the same', () => {
    const ref = createRef<HTMLDivElement>();
    const div = document.createElement('div');
    div.scrollTop = 500;
    (ref as { current: HTMLDivElement | null }).current = div;

    const { rerender } = renderHook(({ page }) => useScrollTopReset(ref, page), {
      initialProps: { page: 'general' },
    });

    div.scrollTop = 500;
    rerender({ page: 'general' });

    expect(div.scrollTop).toBe(500);
  });

  it('does nothing when the ref has no current element', () => {
    const ref = createRef<HTMLDivElement>();

    const { rerender } = renderHook(({ page }) => useScrollTopReset(ref, page), {
      initialProps: { page: 'general' },
    });

    // Should not throw when switching pages with no attached element.
    expect(() => rerender({ page: 'integrations' })).not.toThrow();
  });
});
