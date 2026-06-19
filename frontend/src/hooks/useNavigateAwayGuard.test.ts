/**
 * Tests for useNavigateAwayGuard — the beforeunload guard armed while a
 * critical operation is in progress.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useNavigateAwayGuard } from './useNavigateAwayGuard';

describe('useNavigateAwayGuard', () => {
  let addSpy: ReturnType<typeof vi.spyOn>;
  let removeSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    addSpy = vi.spyOn(window, 'addEventListener');
    removeSpy = vi.spyOn(window, 'removeEventListener');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('arms a beforeunload handler while active is true', () => {
    renderHook(() => useNavigateAwayGuard(true));
    expect(addSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function));
  });

  it('does not arm a handler while active is false', () => {
    renderHook(() => useNavigateAwayGuard(false));
    const beforeUnloadAdds = addSpy.mock.calls.filter((c: unknown[]) => c[0] === 'beforeunload');
    expect(beforeUnloadAdds).toHaveLength(0);
  });

  it('releases the handler when active flips to false', () => {
    const { rerender } = renderHook(({ active }) => useNavigateAwayGuard(active), {
      initialProps: { active: true },
    });
    expect(addSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function));

    rerender({ active: false });
    expect(removeSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function));
  });

  it('releases the handler on unmount', () => {
    const { unmount } = renderHook(() => useNavigateAwayGuard(true));
    unmount();
    expect(removeSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function));
  });

  it('the handler sets returnValue to trigger the native prompt', () => {
    renderHook(() => useNavigateAwayGuard(true));
    const call = addSpy.mock.calls.find((c: unknown[]) => c[0] === 'beforeunload');
    expect(call).toBeDefined();
    const handler = call![1] as (e: BeforeUnloadEvent) => unknown;

    const event = {
      preventDefault: vi.fn(),
      returnValue: '',
    } as unknown as BeforeUnloadEvent;
    handler(event);

    expect(event.preventDefault).toHaveBeenCalled();
    expect(event.returnValue).toContain('restore is in progress');
  });
});
