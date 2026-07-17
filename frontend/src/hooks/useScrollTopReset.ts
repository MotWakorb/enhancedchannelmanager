/**
 * Reset a scroll container's `scrollTop` to 0 whenever a tracked key changes.
 *
 * Used by the Settings tab: navigating between sub-pages (General,
 * Notifications, Integrations, ...) re-renders the same `.settings-content`
 * pane in place, so the browser preserves the previous sub-page's scroll
 * position — landing mid-page on the new one and burying top-of-page
 * warnings (bead enhancedchannelmanager-09x38.11).
 *
 * The reset is an instant `scrollTop` assignment, not an animated scroll —
 * that's always acceptable under `prefers-reduced-motion` (no motion is
 * introduced), so there's nothing further to gate on the media query here.
 * If a future caller wants an animated scroll instead, that caller is
 * responsible for checking `prefers-reduced-motion` before enabling it.
 */
import { useEffect, useRef, type RefObject } from 'react';

/**
 * @param ref - Ref to the scrollable container element.
 * @param key - Value that changes when navigation occurs (e.g. active page id).
 *   The container's scrollTop resets to 0 whenever this value changes after
 *   the initial mount.
 */
export function useScrollTopReset(ref: RefObject<HTMLElement | null>, key: unknown): void {
  const isFirstRender = useRef(true);

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    if (ref.current) {
      ref.current.scrollTop = 0;
    }
    // ref is a stable RefObject identity; only re-run when the tracked key changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}
