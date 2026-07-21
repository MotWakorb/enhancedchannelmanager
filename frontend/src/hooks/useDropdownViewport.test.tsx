import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRef } from 'react';
import { useDropdownViewport } from './useDropdownViewport';

function Harness({ isOpen = true }: { isOpen?: boolean }) {
  const triggerRef = useRef<HTMLDivElement>(null);
  const position = useDropdownViewport({
    isOpen,
    triggerRef,
    desiredHeight: 300,
    minimumUsableHeight: 124,
    onPlacementUnavailable: vi.fn(),
  });
  return <div ref={triggerRef} data-testid="trigger" data-top={position.top} />;
}

describe('useDropdownViewport event scheduling', () => {
  let queuedFrame: FrameRequestCallback | undefined;
  let rectSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
    rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      x: 24, y: 100, top: 100, right: 244, bottom: 136, left: 24, width: 220, height: 36,
      toJSON: () => ({}),
    } as DOMRect);
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => {
      queuedFrame = callback;
      return 17;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined);
  });

  afterEach(() => vi.restoreAllMocks());

  it('coalesces repeated capture-scroll events into one animation frame', () => {
    render(<Harness />);
    expect(screen.getByTestId('trigger')).toHaveAttribute('data-top', '140');
    rectSpy.mockReturnValue({
      x: 24, y: 200, top: 200, right: 244, bottom: 236, left: 24, width: 220, height: 36,
      toJSON: () => ({}),
    } as DOMRect);

    window.dispatchEvent(new Event('scroll'));
    window.dispatchEvent(new Event('scroll'));
    window.dispatchEvent(new Event('scroll'));

    expect(window.requestAnimationFrame).toHaveBeenCalledTimes(1);
    act(() => queuedFrame?.(0));
    expect(screen.getByTestId('trigger')).toHaveAttribute('data-top', '240');
  });

  it('cancels a pending frame when the dropdown closes', () => {
    const { rerender } = render(<Harness />);
    window.dispatchEvent(new Event('scroll'));
    expect(window.requestAnimationFrame).toHaveBeenCalledTimes(1);

    rerender(<Harness isOpen={false} />);

    expect(window.cancelAnimationFrame).toHaveBeenCalledWith(17);
  });
});
