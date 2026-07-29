import { createRef } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false }));
    window.history.replaceState(null, '', '#stats');
  });

  it.each(['Enter', ' '])('discovers sections and supports %s keyboard activation', async (key) => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    const ref = createRef<HTMLDivElement>();
    render(<div ref={ref}>
      <StickySectionNav containerRef={ref} selector=".target" routeKey="stats" />
      <section className="target"><h2>Current activity</h2></section>
      <section className="target"><h2>Watch history</h2></section>
    </div>);
    const button = await screen.findByRole('button', { name: 'Watch history' });
    button.focus();
    await user.keyboard(key === ' ' ? '[Space]' : '[Enter]');
    await waitFor(() => expect(window.location.hash).toBe('#stats?section=stats-section-watch-history'));
    expect(document.getElementById('stats-section-watch-history')).toHaveClass('sticky-section-target');
  });

  it('defaults to the top bar and opts into the right-hand rail', async () => {
    const ref = createRef<HTMLDivElement>();
    const { rerender } = render(<div ref={ref}>
      <StickySectionNav containerRef={ref} selector=".target" routeKey="settings-general" />
      <section className="target"><h2>Current activity</h2></section>
      <section className="target"><h2>Watch history</h2></section>
    </div>);

    const nav = await screen.findByRole('navigation', { name: 'On this page' });
    expect(nav).toHaveClass('placement-top');
    expect(nav).not.toHaveClass('placement-rail');

    rerender(<div ref={ref}>
      <StickySectionNav containerRef={ref} selector=".target" routeKey="settings-general" placement="rail" />
      <section className="target"><h2>Current activity</h2></section>
      <section className="target"><h2>Watch history</h2></section>
    </div>);

    const rail = await screen.findByRole('navigation', { name: 'On this page' });
    expect(rail).toHaveClass('placement-rail');
    // Same landmark, name, and controls in either placement.
    expect(await screen.findByRole('button', { name: 'Watch history' })).toBeInTheDocument();
  });

  it('honors direct section entry once sections are available', async () => {
    window.history.replaceState(null, '', '#stats?section=stats-section-watch-history');
    const scrollTo = vi.fn();
    const ref = createRef<HTMLDivElement>();
    render(<div ref={ref}>
      <StickySectionNav containerRef={ref} selector=".target" routeKey="stats" />
      <section className="target"><h2>Current activity</h2></section>
      <section className="target"><h2>Watch history</h2></section>
    </div>);
    ref.current!.scrollTo = scrollTo;
    const button = await screen.findByRole('button', { name: 'Watch history' });
    expect(button).toHaveAttribute('aria-current', 'location');
    await waitFor(() => expect(scrollTo).toHaveBeenCalled());
  });

  it('uses instant scrolling when reduced motion is requested', async () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }));
    const scrollTo = vi.fn();
    const ref = createRef<HTMLDivElement>();
    render(<div ref={ref}>
      <StickySectionNav containerRef={ref} selector=".target" routeKey="stats" />
      <section className="target"><h2>Current activity</h2></section>
      <section className="target"><h2>Watch history</h2></section>
    </div>);
    ref.current!.scrollTo = scrollTo;
    fireEvent.click(await screen.findByRole('button', { name: 'Watch history' }));
    expect(scrollTo).toHaveBeenLastCalledWith(expect.objectContaining({ behavior: 'auto' }));
  });

  // Regression: scrollIntoView cannot be constrained to one ancestor, so it also
  // scrolled the document — sliding the fixed shell up and exposing empty space
  // below it on Stats, whose content overflows the root box.
  it('scrolls only its container, never the document', async () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    const scrollTo = vi.fn();
    const ref = createRef<HTMLDivElement>();
    render(<div ref={ref}>
      <StickySectionNav containerRef={ref} selector=".target" routeKey="stats" />
      <section className="target"><h2>Current activity</h2></section>
      <section className="target"><h2>Watch history</h2></section>
    </div>);
    ref.current!.scrollTo = scrollTo;

    fireEvent.click(await screen.findByRole('button', { name: 'Watch history' }));

    expect(scrollTo).toHaveBeenCalled();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
