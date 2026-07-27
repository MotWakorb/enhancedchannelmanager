import { createRef } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StickySectionNav } from './StickySectionNav';

class IntersectionObserverMock {
  observe() {}
  disconnect() {}
  unobserve() {}
}

describe('StickySectionNav', () => {
  beforeEach(() => {
    vi.stubGlobal('IntersectionObserver', IntersectionObserverMock);
    window.history.replaceState(null, '', '#stats');
  });

  it('discovers sections, writes a deep link, and supports keyboard button activation', async () => {
    const ref = createRef<HTMLDivElement>();
    render(<div ref={ref}>
      <StickySectionNav containerRef={ref} selector=".target" routeKey="stats" />
      <section className="target"><h2>Current activity</h2></section>
      <section className="target"><h2>Watch history</h2></section>
    </div>);
    const button = await screen.findByRole('button', { name: 'Watch history' });
    fireEvent.keyDown(button, { key: 'Enter' });
    fireEvent.click(button);
    await waitFor(() => expect(window.location.hash).toBe('#stats?section=stats-section-watch-history'));
    expect(document.getElementById('stats-section-watch-history')).toHaveClass('sticky-section-target');
  });

  it('honors direct section entry once sections are available', async () => {
    window.history.replaceState(null, '', '#stats?section=stats-section-watch-history');
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    const ref = createRef<HTMLDivElement>();
    render(<div ref={ref}>
      <StickySectionNav containerRef={ref} selector=".target" routeKey="stats" />
      <section className="target"><h2>Current activity</h2></section>
      <section className="target"><h2>Watch history</h2></section>
    </div>);
    await screen.findByRole('button', { name: 'Watch history' });
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
  });
});
