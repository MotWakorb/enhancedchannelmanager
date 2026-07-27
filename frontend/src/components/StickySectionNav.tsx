import { useCallback, useEffect, useState, type RefObject } from 'react';
import './StickySectionNav.css';

type SectionItem = { id: string; label: string };

const slug = (value: string) => value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
const preferredScrollBehavior = (): ScrollBehavior => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';

export function StickySectionNav({
  containerRef,
  selector,
  routeKey,
}: {
  containerRef: RefObject<HTMLElement | null>;
  selector: string;
  routeKey: string;
}) {
  const [items, setItems] = useState<SectionItem[]>([]);
  const [activeId, setActiveId] = useState('');

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
    const requested = new URLSearchParams(window.location.hash.split('?')[1] || '').get('section');
    if (requested && items.some((item) => item.id === requested)) {
      setActiveId(requested);
      requestAnimationFrame(() => {
        const target = document.getElementById(requested);
        if (typeof target?.scrollIntoView === 'function') target.scrollIntoView({ behavior: preferredScrollBehavior(), block: 'start' });
      });
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
        const topBoundary = nav?.getBoundingClientRect().bottom ?? container.getBoundingClientRect().top;
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

  if (items.length < 2) return null;
  const activate = (id: string) => {
    setActiveId(id);
    const base = window.location.hash.split('?')[0];
    window.history.replaceState(null, '', `${base}?section=${encodeURIComponent(id)}`);
    window.dispatchEvent(new CustomEvent('ecm:route-replaced', { detail: { hash: window.location.hash } }));
    const target = document.getElementById(id);
    if (typeof target?.scrollIntoView === 'function') target.scrollIntoView({ behavior: preferredScrollBehavior(), block: 'start' });
  };
  return <nav className="sticky-section-nav" aria-label="On this page">
    <span>On this page</span>
    <div>
      {items.map((item) => <button
        type="button"
        key={item.id}
        aria-current={activeId === item.id ? 'location' : undefined}
        onClick={() => activate(item.id)}
      >{item.label}</button>)}
    </div>
  </nav>;
}
