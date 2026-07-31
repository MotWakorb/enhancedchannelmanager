import { createRef, type RefObject } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

/**
 * bead enhancedchannelmanager-ue130 — the deep-link scroll is ONE SHOT PER
 * NAVIGATION, not a standing instruction re-executed for the life of the page.
 *
 * THE MECHANISM. `discover()` runs on every DOM mutation inside the container
 * and always calls `setItems` with a FRESH array, so `items` changes identity
 * on every mutation. The deep-link effect is keyed on `items`, so it used to
 * re-read `?section=` and scroll again — forever, for any DOM change anywhere
 * on the page, however long after the link was opened. Measured in a browser
 * on Settings → Maintenance: a reader who had clicked a rail entry and scrolled
 * back to the top was thrown back down 2812px when a backend-SCHEDULED probe
 * started, and again when it ended. 22fef24d removed one CAUSE (a section that
 * arrived late); this pins the mechanism shut everywhere.
 *
 * WHAT "PER NAVIGATION" MEANS HERE. The consumed target is remembered by VALUE,
 * so a URL naming a DIFFERENT section re-arms — otherwise fixing the yank would
 * break in-page navigation. `activate()` marks its own target consumed, because
 * a click has already scrolled there and does not need the effect to repeat it.
 */
describe('StickySectionNav — the deep link fires once per navigation (bead ue130)', () => {
  /** Two sections, plus an optional third that arrives later. */
  const Harness = ({ containerRef, late }: { containerRef: RefObject<HTMLDivElement | null>; late?: boolean }) => (
    <div ref={containerRef}>
      <StickySectionNav containerRef={containerRef} selector=".target" routeKey="stats" />
      <section className="target"><h2>Current activity</h2></section>
      <section className="target"><h2>Watch history</h2></section>
      {late && <section className="target"><h2>Late arrival</h2></section>}
    </div>
  );

  // Loosely typed on purpose: vitest's `Mock<T>` exposes only the LAST overload
  // of `Element.scrollTo`, so a mock typed as `Element['scrollTo']` will not
  // assign back onto the prototype. Nothing here reads the arguments — these
  // tests count calls.
  let scrollTo: ReturnType<typeof vi.fn<(...args: unknown[]) => void>>;

  beforeEach(() => {
    vi.stubGlobal('IntersectionObserver', IntersectionObserverMock);
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false }));
    window.history.replaceState(null, '', '#stats');
    scrollTo = vi.fn<(...args: unknown[]) => void>();
    // On the prototype, not on the instance, so the FIRST scroll is counted
    // too — the point of these tests is how many times it fires, not whether.
    Element.prototype.scrollTo = scrollTo;
    Element.prototype.scrollIntoView = vi.fn();
  });

  /** Lets the queued `requestAnimationFrame` scroll run. */
  const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 40)); });

  it('does not re-fire when the page changes underneath the reader', async () => {
    window.history.replaceState(null, '', '#stats?section=stats-section-watch-history');
    const ref = createRef<HTMLDivElement>();
    const { rerender } = render(<Harness containerRef={ref} />);

    await screen.findByRole('button', { name: 'Watch history' });
    await waitFor(() => expect(scrollTo).toHaveBeenCalledTimes(1));
    scrollTo.mockClear();

    // Something else on the page changes — a status banner mounting, a
    // data-gated section arriving. The reader asked for none of it.
    rerender(<Harness containerRef={ref} late />);
    await screen.findByRole('button', { name: 'Late arrival' });
    await settle();

    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('does not re-fire after an in-page rail click, however the page changes later', async () => {
    const ref = createRef<HTMLDivElement>();
    const { rerender } = render(<Harness containerRef={ref} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Watch history' }));
    expect(window.location.hash).toBe('#stats?section=stats-section-watch-history');
    await waitFor(() => expect(scrollTo).toHaveBeenCalled());
    scrollTo.mockClear();

    rerender(<Harness containerRef={ref} late />);
    await screen.findByRole('button', { name: 'Late arrival' });
    await settle();

    expect(scrollTo).not.toHaveBeenCalled();
  });

  it('re-arms when the URL names a different section', async () => {
    window.history.replaceState(null, '', '#stats?section=stats-section-watch-history');
    const ref = createRef<HTMLDivElement>();
    const { rerender } = render(<Harness containerRef={ref} />);

    await screen.findByRole('button', { name: 'Watch history' });
    await waitFor(() => expect(scrollTo).toHaveBeenCalledTimes(1));
    scrollTo.mockClear();

    // A genuinely new navigation — a link opened into the page already on
    // screen. This one MUST be honoured; a latch that swallowed it would have
    // traded a stray scroll for a broken link.
    window.history.replaceState(null, '', '#stats?section=stats-section-late-arrival');
    rerender(<Harness containerRef={ref} late />);

    await screen.findByRole('button', { name: 'Late arrival' });
    await waitFor(() => expect(scrollTo).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('button', { name: 'Late arrival' })).toHaveAttribute('aria-current', 'location');
  });

  it('drops a ?section= that names nothing, says so, and never fires it later', async () => {
    window.history.replaceState(null, '', '#stats?section=stats-section-late-arrival');
    const ref = createRef<HTMLDivElement>();
    const { rerender } = render(<Harness containerRef={ref} />);

    await screen.findByRole('navigation', { name: 'On this page' });
    // The URL stops claiming a section this page does not have, so a reload —
    // or a copy of the address bar passed on to someone else — cannot revive
    // it, and so it can never fire late.
    await waitFor(() => expect(window.location.hash).toBe('#stats'));
    expect(await screen.findByRole('status')).toHaveTextContent(/not on this page/i);
    await settle();
    expect(scrollTo).not.toHaveBeenCalled();

    // The named section turns up after all. The reader has been reading the
    // top of the page for as long as that took: it must not move under them.
    rerender(<Harness containerRef={ref} late />);
    await screen.findByRole('button', { name: 'Late arrival' });
    await settle();
    expect(scrollTo).not.toHaveBeenCalled();

    // Choosing a section is what makes the notice moot, so that is what
    // clears it. The live region itself stays in the DOM — empty, and hidden
    // by `:empty` — because a region added at the same moment as its text is
    // not reliably announced.
    fireEvent.click(screen.getByRole('button', { name: 'Late arrival' }));
    await waitFor(() => expect(screen.getByRole('status')).toBeEmptyDOMElement());
  });
});
