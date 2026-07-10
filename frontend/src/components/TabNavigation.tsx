import { useCallback, useEffect, useRef, useState } from 'react';
import './TabNavigation.css';

export type TabId = 'm3u-manager' | 'epg-manager' | 'channel-manager' | 'guide' | 'logo-manager' | 'm3u-changes' | 'channel-pipeline' | 'journal' | 'stats' | 'settings';

interface Tab {
  id: TabId;
  label: string;
  icon: string;
}

interface TabNavigationProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
  disabled?: boolean;
  editModeActive?: boolean;
}

const TABS: Tab[] = [
  { id: 'm3u-manager', label: 'M3U Manager', icon: 'playlist_play' },
  { id: 'epg-manager', label: 'EPG Manager', icon: 'schedule' },
  { id: 'channel-manager', label: 'Channel Manager', icon: 'tv' },
  { id: 'guide', label: 'Guide', icon: 'grid_on' },
  { id: 'logo-manager', label: 'Logo Manager', icon: 'image' },
  { id: 'm3u-changes', label: 'M3U Changes', icon: 'compare_arrows' },
  { id: 'channel-pipeline', label: 'Channel Pipeline', icon: 'auto_fix_high' },
  { id: 'journal', label: 'Journal', icon: 'history' },
  { id: 'stats', label: 'Stats', icon: 'analytics' },
  { id: 'settings', label: 'Settings', icon: 'settings' },
];

// How far a chevron click scrolls the strip, in px.
const SCROLL_STEP = 240;

/**
 * bd-3thyn: below ~1280px the tab strip (10 tabs) is wider than the
 * viewport. It used to be `overflow-x: visible` with no way to reach the
 * clipped tabs (Settings/Stats/Journal) by mouse OR keyboard. Making the
 * strip itself the horizontally-scrollable element keeps Tab-key
 * navigation working for free — browsers auto-scroll a focused element
 * into view within its nearest scrollable ancestor — and the chevron
 * buttons give an explicit, discoverable, keyboard-operable affordance
 * for mouse users who won't discover a bare scrollable region.
 */
export function TabNavigation({ activeTab, onTabChange, disabled, editModeActive }: TabNavigationProps) {
  const scrollRef = useRef<HTMLElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const updateScrollState = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 1);
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  }, []);

  useEffect(() => {
    updateScrollState();
    const el = scrollRef.current;
    if (!el) return undefined;

    el.addEventListener('scroll', updateScrollState, { passive: true });
    window.addEventListener('resize', updateScrollState);

    // Tab count/labels are static, but a viewport resize (or a font/zoom
    // change) can flip overflow on/off without firing a scroll event.
    const resizeObserver = new ResizeObserver(updateScrollState);
    resizeObserver.observe(el);

    return () => {
      el.removeEventListener('scroll', updateScrollState);
      window.removeEventListener('resize', updateScrollState);
      resizeObserver.disconnect();
    };
  }, [updateScrollState]);

  const scrollByStep = (direction: 1 | -1) => {
    scrollRef.current?.scrollBy({ left: direction * SCROLL_STEP, behavior: 'smooth' });
  };

  return (
    <div className="tab-navigation-wrapper">
      {canScrollLeft && (
        <button
          type="button"
          className="tab-nav-scroll-btn tab-nav-scroll-left"
          onClick={() => scrollByStep(-1)}
          aria-label="Scroll tabs left"
        >
          <span className="material-icons" aria-hidden="true">chevron_left</span>
        </button>
      )}
      <nav
        ref={scrollRef}
        className={`tab-navigation ${editModeActive ? 'edit-mode-active' : ''}`}
      >
        {TABS.map((tab) => (
          <button
            key={tab.id}
            data-tab={tab.id}
            className={`tab-button ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => onTabChange(tab.id)}
            disabled={disabled}
          >
            <span className="material-icons">{tab.icon}</span>
            <span className="tab-label">{tab.label}</span>
          </button>
        ))}
      </nav>
      {canScrollRight && (
        <button
          type="button"
          className="tab-nav-scroll-btn tab-nav-scroll-right"
          onClick={() => scrollByStep(1)}
          aria-label="Scroll tabs right"
        >
          <span className="material-icons" aria-hidden="true">chevron_right</span>
        </button>
      )}
    </div>
  );
}
