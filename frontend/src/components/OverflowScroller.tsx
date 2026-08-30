import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import './OverflowScroller.css';

/**
 * Horizontal scroller that surfaces its own overflow.
 *
 * A strip with `overflow-x: auto` scrolls, but nothing tells the operator there
 * is more to the right — the content simply stops at the edge and reads as
 * truncated. This adds arrows at each end, shown only while the content
 * actually overflows and disabled at each limit, so the cut-off edge is
 * obviously navigable rather than obviously missing.
 *
 * The viewport is focusable because a scrollable region that cannot be reached
 * or panned by keyboard fails WCAG 2.1.1; arrow keys scroll it natively once
 * focused, and the arrow buttons give a pointer affordance.
 */
export function OverflowScroller({
  children,
  label,
  className,
}: {
  children: ReactNode;
  /** Names the region, and the scroll buttons that move it. */
  label: string;
  className?: string;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [overflowing, setOverflowing] = useState(false);
  const [atStart, setAtStart] = useState(true);
  const [atEnd, setAtEnd] = useState(false);

  const measure = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return;
    // Sub-pixel layout means these rarely land on exact integers.
    const slack = 2;
    const maxScroll = el.scrollWidth - el.clientWidth;
    setOverflowing(maxScroll > slack);
    setAtStart(el.scrollLeft <= slack);
    setAtEnd(el.scrollLeft >= maxScroll - slack);
  }, []);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    measure();

    el.addEventListener('scroll', measure, { passive: true });
    // Content arrives asynchronously (provider counts load after mount) and the
    // available width changes with the sidebar, so watch both.
    const resize = new ResizeObserver(measure);
    resize.observe(el);
    for (const child of el.children) resize.observe(child);
    const mutation = new MutationObserver(measure);
    mutation.observe(el, { childList: true, subtree: true, characterData: true });

    return () => {
      el.removeEventListener('scroll', measure);
      resize.disconnect();
      mutation.disconnect();
    };
  }, [measure]);

  const scroll = (direction: -1 | 1) => {
    const el = viewportRef.current;
    if (!el) return;
    const behavior: ScrollBehavior =
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
    el.scrollBy({ left: direction * Math.max(120, el.clientWidth * 0.7), behavior });
  };

  return (
    <div className={`overflow-scroller${overflowing ? ' is-overflowing' : ''}${className ? ` ${className}` : ''}`}>
      {overflowing && (
        <button
          type="button"
          className="overflow-scroller-arrow"
          onClick={() => scroll(-1)}
          disabled={atStart}
          aria-label={`Scroll ${label} left`}
        >
          <span className="material-icons" aria-hidden="true">chevron_left</span>
        </button>
      )}
      <div
        ref={viewportRef}
        className="overflow-scroller-viewport"
        tabIndex={0}
        role="group"
        aria-label={label}
      >
        {children}
      </div>
      {overflowing && (
        <button
          type="button"
          className="overflow-scroller-arrow"
          onClick={() => scroll(1)}
          disabled={atEnd}
          aria-label={`Scroll ${label} right`}
        >
          <span className="material-icons" aria-hidden="true">chevron_right</span>
        </button>
      )}
    </div>
  );
}
