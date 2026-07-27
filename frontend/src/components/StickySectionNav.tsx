import { useCallback, useEffect, useState, type RefObject } from 'react';
import './StickySectionNav.css';

type SectionItem = { id: string; label: string };

const slug = (value: string) => value.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');

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
      const id = section.id || `${routeKey}-section-${slug(label) || index + 1}`;
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
      requestAnimationFrame(() => {
        const target = document.getElementById(requested);
        if (typeof target?.scrollIntoView === 'function') target.scrollIntoView({ block: 'start' });
      });
    }
    return () => observer.disconnect();
  }, [containerRef, items]);

  if (items.length < 2) return null;
  const activate = (id: string) => {
    setActiveId(id);
    const base = window.location.hash.split('?')[0];
    window.history.replaceState(null, '', `${base}?section=${encodeURIComponent(id)}`);
    const target = document.getElementById(id);
    if (typeof target?.scrollIntoView === 'function') target.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
