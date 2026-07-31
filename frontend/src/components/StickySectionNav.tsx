import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import './StickySectionNav.css';

type SectionItem = { id: string; label: string };

const slug = (value: string) => value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
const preferredScrollBehavior = (): ScrollBehavior => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
const requestedSection = () => new URLSearchParams(window.location.hash.split('?')[1] || '').get('section');

/**
 * Drops the `?section=` the URL is carrying, keeping the route it names.
 *
 * `ecm:route-replaced` is what tells `useHashRoute` this hash change was ours
 * and not a navigation — same contract as `activate()`. The history STATE is
 * preserved rather than nulled, because `useHashRoute` keeps its route index
 * there and nothing here is a navigation.
 */
function dropSectionFromHash() {
  const base = window.location.hash.split('?')[0];
  if (base === window.location.hash) return;
  window.history.replaceState(window.history.state, '', base);
  window.dispatchEvent(new CustomEvent('ecm:route-replaced', { detail: { hash: window.location.hash } }));
}

/**
 * Scrolls `target` into view within `container` and nothing else.
 *
 * `scrollIntoView` cannot be constrained to one ancestor — it scrolls every
 * scrollable ancestor including the document. `overflow: hidden` on <html> only
 * blocks *user* scrolling, not programmatic scrolling, so on a route whose
 * content overflows the root box the page itself would slide under the fixed
 * shell and leave empty space below it. Scrolling the known container directly
 * is the only way to guarantee the shell stays put.
 *
 * `scroll-margin-top` is read off the target so the offset that `scrollIntoView`
 * used to honour still applies.
 */
function scrollWithinContainer(container: HTMLElement, target: HTMLElement, behavior: ScrollBehavior) {
  if (typeof container.scrollTo !== 'function') return;
  const margin = Number.parseFloat(getComputedStyle(target).scrollMarginTop) || 0;
  const delta = target.getBoundingClientRect().top - container.getBoundingClientRect().top - margin;
  container.scrollTo({ top: container.scrollTop + delta, behavior });
}

export function StickySectionNav({
  containerRef,
  selector,
  routeKey,
  placement = 'top',
}: {
  containerRef: RefObject<HTMLElement | null>;
  selector: string;
  routeKey: string;
  /**
   * 'top' is the original horizontal sticky bar above the content. 'rail'
   * moves the list into a sticky right-hand column, which occupies no vertical
   * space and therefore does not bound the content from above.
   */
  placement?: 'top' | 'rail';
}) {
  const [items, setItems] = useState<SectionItem[]>([]);
  const [activeId, setActiveId] = useState('');
  /** The `?section=` that named nothing on this page, once one has. */
  const [unresolvedSection, setUnresolvedSection] = useState('');
  /**
   * The `?section=` this mount has already acted on — the latch that makes the
   * deep link ONE SHOT PER NAVIGATION (bead enhancedchannelmanager-ue130).
   * A ref, not state: it must survive every re-render without causing one.
   */
  const honouredSection = useRef<string | null>(null);

  const discover = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const next = [...container.querySelectorAll<HTMLElement>(selector)].flatMap((section, index) => {
      const heading = section.querySelector<HTMLElement>('h2, h3');
      const label = section.dataset.sectionLabel || heading?.textContent?.trim();
      if (!label) return [];
      const generatedId = `${routeKey}-section-${slug(label) || index + 1}`;
      const id = section.dataset.sectionId
        || (section.id && !section.id.startsWith('settings-') ? section.id : generatedId);
      section.id = id;
      section.classList.add('sticky-section-target');
      return [{ id, label }];
    });
    setItems(next);
    setActiveId((current) => current || next[0]?.id || '');
  }, [containerRef, routeKey, selector]);

  useEffect(() => {
    discover();
    const observer = new MutationObserver(discover);
    if (containerRef.current) observer.observe(containerRef.current, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [containerRef, discover]);

  useEffect(() => {
    if (items.length === 0) return;
    const root = containerRef.current;
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible[0]) setActiveId(visible[0].target.id);
    }, { root, rootMargin: '-72px 0px -65% 0px', threshold: 0 });
    items.forEach(({ id }) => {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    });
    // ONE SHOT PER NAVIGATION, not a standing order.
    //
    // `discover()` returns a FRESH array on every DOM mutation in the
    // container, so `items` changes identity for the life of the page and this
    // effect re-runs with it. Without the latch the `?section=` sitting in the
    // URL is re-read and re-scrolled every time — measured on Settings →
    // Maintenance, a reader who had clicked a rail entry and scrolled back to
    // the top was thrown 2812px down when a backend-scheduled probe started,
    // and again when it ended, minutes after opening the page. 22fef24d
    // removed one CAUSE of a late `items` change; this removes the mechanism
    // (bead enhancedchannelmanager-ue130).
    //
    // The latch is keyed on the requested VALUE, so a URL naming a DIFFERENT
    // section still navigates — latching per mount would have traded the stray
    // scroll for a broken link. `activate()` marks its own target honoured for
    // the same reason: a click has already scrolled there.
    //
    // It is armed whether or not the target resolved. A section that is not
    // here now is exactly the one whose later arrival yanks the reader, and
    // every StickySectionNav consumer renders its sections from first paint
    // (22fef24d, 4af8f487) — so "not found now" means absent by design, not
    // still loading.
    const requested = requestedSection();
    if (requested && honouredSection.current !== requested) {
      honouredSection.current = requested;
      if (items.some((item) => item.id === requested)) {
        setUnresolvedSection('');
        setActiveId(requested);
        requestAnimationFrame(() => {
          const target = document.getElementById(requested);
          const container = containerRef.current;
          if (target && container) scrollWithinContainer(container, target, preferredScrollBehavior());
        });
      } else {
        // Failing silently left the reader at the top of the page with
        // `aria-current` on some unrelated section and no sign the link had
        // named anything. Say so, and stop the URL claiming a section this
        // page does not have — otherwise a reload revives the dead target and
        // the address bar propagates it to the next person.
        setUnresolvedSection(requested);
        dropSectionFromHash();
      }
    }
    return () => observer.disconnect();
  }, [containerRef, items]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || items.length < 2) return;
    let frame = 0;
    const keepFocusedControlVisible = (event: FocusEvent) => {
      const focused = event.target as HTMLElement | null;
      if (!focused || !container.contains(focused)
        || focused.closest('.sticky-section-nav, .settings-pending-actions')) return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const nav = container.querySelector<HTMLElement>('.sticky-section-nav');
        const pending = container.querySelector<HTMLElement>('.settings-pending-actions');
        const focusedRect = focused.getBoundingClientRect();
        // Decide from geometry, not the `placement` prop: the rail reverts to a
        // top bar below a CSS breakpoint, so the prop alone would misdescribe
        // the rendered layout. A nav only bounds the control from above when it
        // actually sits over the same horizontal band.
        const navRect = nav?.getBoundingClientRect();
        const navOverlapsHorizontally = navRect
          && focusedRect.right > navRect.left + 1
          && focusedRect.left < navRect.right - 1;
        const topBoundary = navOverlapsHorizontally
          ? navRect.bottom
          : container.getBoundingClientRect().top;
        const bottomBoundary = pending?.getBoundingClientRect().top ?? container.getBoundingClientRect().bottom;
        if (focusedRect.top < topBoundary + 8) {
          container.scrollTop -= topBoundary + 8 - focusedRect.top;
        } else if (focusedRect.bottom > bottomBoundary - 8) {
          container.scrollTop += focusedRect.bottom - bottomBoundary + 8;
        }
      });
    };
    container.addEventListener('focusin', keepFocusedControlVisible);
    return () => {
      cancelAnimationFrame(frame);
      container.removeEventListener('focusin', keepFocusedControlVisible);
    };
  }, [containerRef, items.length]);

  // Below two sections there is nothing to navigate — including, deliberately,
  // no place to put the notice below. A one-section page cannot be deep-linked
  // wrongly in a way worth reporting: the reader is already looking at the only
  // section there is. The stale `?section=` is still dropped by the effect.
  if (items.length < 2) return null;
  const activate = (id: string) => {
    setActiveId(id);
    // A click IS a navigation, and it has done its own scrolling — record it so
    // the next `items` change does not repeat it (bead ue130). It also settles
    // the "that link named nothing" notice: the reader has now chosen a
    // section, so the failed one no longer needs answering.
    honouredSection.current = id;
    setUnresolvedSection('');
    const base = window.location.hash.split('?')[0];
    window.history.replaceState(null, '', `${base}?section=${encodeURIComponent(id)}`);
    window.dispatchEvent(new CustomEvent('ecm:route-replaced', { detail: { hash: window.location.hash } }));
    const target = document.getElementById(id);
    const container = containerRef.current;
    if (target && container) scrollWithinContainer(container, target, preferredScrollBehavior());
  };
  return <nav className={`sticky-section-nav placement-${placement}`} aria-label="On this page">
    {/* `.micro-label` (shared/common.css § 24) owns the size, weight, case and
        tracking. See StickySectionNav.css for why it is a class here rather
        than declarations there (bead enhancedchannelmanager-6z299). */}
    <span className="micro-label">On this page</span>
    <div>
      {items.map((item) => <button
        type="button"
        key={item.id}
        aria-current={activeId === item.id ? 'location' : undefined}
        onClick={() => activate(item.id)}
      >{item.label}</button>)}
    </div>
    {/* Always rendered, empty until there is something to say: a live region
        has to be in the DOM before its text changes for the change to be
        announced. `:empty` hides it, so an empty region costs no layout — not
        even the flex gap (bead enhancedchannelmanager-ue130). */}
    <p className="sticky-section-nav-notice" role="status">
      {unresolvedSection ? 'That link named a section that is not on this page.' : ''}
    </p>
  </nav>;
}
