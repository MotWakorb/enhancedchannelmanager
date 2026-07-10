/**
 * ShowMoreRows — incremental-rendering sentinel for long lists (bd-bed9r).
 *
 * Large channel/stream groups no longer render every row on expand. The
 * panes render an initial chunk and place this sentinel after it; scrolling
 * the sentinel into view (IntersectionObserver) or activating the button
 * renders the next chunk. This bounds the DOM cost of expanding a huge group
 * (427-channel group ≈ 2,000+ nodes previously) without a virtualization
 * dependency and without disturbing @dnd-kit drag-drop or keyboard focus —
 * rendered rows are real rows; nothing is unmounted while scrolling.
 */
import { useEffect, useRef } from 'react';
import './ShowMoreRows.css';

interface ShowMoreRowsProps {
  /** Number of items not yet rendered. */
  remaining: number;
  /** Noun for the button label, e.g. "channels" or "streams". */
  noun: string;
  /** Render the next chunk. */
  onShowMore: () => void;
}

export function ShowMoreRows({ remaining, noun, onShowMore }: ShowMoreRowsProps) {
  const sentinelRef = useRef<HTMLDivElement>(null);

  // Keep the latest callback in a ref so the observer effect doesn't
  // reconnect on every render (onShowMore is an inline closure in the panes).
  const onShowMoreRef = useRef(onShowMore);
  onShowMoreRef.current = onShowMore;

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          onShowMoreRef.current();
        }
      },
      // Start rendering slightly before the sentinel is actually visible so
      // continuous scrolling feels seamless.
      { rootMargin: '200px' },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [remaining]); // re-arm after each chunk so a still-visible sentinel keeps filling

  return (
    <div ref={sentinelRef} className="show-more-rows">
      <button type="button" className="show-more-rows-btn" onClick={onShowMore}>
        <span className="material-icons" aria-hidden="true">expand_more</span>
        Show more ({remaining} more {noun})
      </button>
    </div>
  );
}

export default ShowMoreRows;
