/**
 * Tests for ShowMoreRows — incremental-rendering sentinel (bd-bed9r).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ShowMoreRows } from './ShowMoreRows';

type IOCallback = (entries: Array<{ isIntersecting: boolean }>) => void;

let observerCallbacks: IOCallback[];
let observedElements: Element[];
let disconnectCount: number;

class MockIntersectionObserver {
  constructor(callback: IOCallback) {
    observerCallbacks.push(callback);
  }

  observe(el: Element) {
    observedElements.push(el);
  }

  disconnect() {
    disconnectCount++;
  }

  unobserve() {}
}

beforeEach(() => {
  observerCallbacks = [];
  observedElements = [];
  disconnectCount = 0;
  vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ShowMoreRows', () => {
  it('renders a button with the remaining count and noun', () => {
    render(<ShowMoreRows remaining={327} noun="channels" onShowMore={() => {}} />);

    expect(screen.getByRole('button', { name: /show more \(327 more channels\)/i })).toBeInTheDocument();
  });

  it('clicking the button calls onShowMore', () => {
    const onShowMore = vi.fn();
    render(<ShowMoreRows remaining={100} noun="streams" onShowMore={onShowMore} />);

    fireEvent.click(screen.getByRole('button'));

    expect(onShowMore).toHaveBeenCalledTimes(1);
  });

  it('observes the sentinel and calls onShowMore when it scrolls into view', () => {
    const onShowMore = vi.fn();
    render(<ShowMoreRows remaining={100} noun="channels" onShowMore={onShowMore} />);

    expect(observedElements).toHaveLength(1);

    // Simulate the sentinel entering the viewport
    observerCallbacks[0]([{ isIntersecting: true }]);
    expect(onShowMore).toHaveBeenCalledTimes(1);

    // Not intersecting: no extra call
    observerCallbacks[0]([{ isIntersecting: false }]);
    expect(onShowMore).toHaveBeenCalledTimes(1);
  });

  it('re-arms the observer when remaining changes (next chunk)', () => {
    const onShowMore = vi.fn();
    const { rerender } = render(
      <ShowMoreRows remaining={200} noun="channels" onShowMore={onShowMore} />,
    );
    expect(observerCallbacks).toHaveLength(1);

    rerender(<ShowMoreRows remaining={100} noun="channels" onShowMore={onShowMore} />);

    // Old observer disconnected, new one attached
    expect(disconnectCount).toBe(1);
    expect(observerCallbacks).toHaveLength(2);

    observerCallbacks[1]([{ isIntersecting: true }]);
    expect(onShowMore).toHaveBeenCalledTimes(1);
  });

  it('disconnects the observer on unmount', () => {
    const { unmount } = render(
      <ShowMoreRows remaining={50} noun="streams" onShowMore={() => {}} />,
    );

    unmount();

    expect(disconnectCount).toBe(1);
  });
});
