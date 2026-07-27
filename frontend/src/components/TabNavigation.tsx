import { useState } from 'react';
import ECMLogo from '../assets/ECMLogo.png';
import './TabNavigation.css';

export type TabId = 'dashboard' | 'm3u-manager' | 'epg-manager' | 'channel-manager' | 'guide' | 'logo-manager' | 'm3u-changes' | 'channel-pipeline' | 'journal' | 'stats' | 'settings';

interface Destination {
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

const COLLAPSED_STORAGE_KEY = 'ecm.navigation.collapsed';

const GROUPS: Array<{ label: string; destinations: Destination[] }> = [
  { label: 'Overview', destinations: [{ id: 'dashboard', label: 'Dashboard', icon: 'dashboard' }] },
  { label: 'Operations', destinations: [
    { id: 'channel-manager', label: 'Channel Manager', icon: 'tv' },
    { id: 'guide', label: 'Guide', icon: 'grid_on' },
    { id: 'm3u-manager', label: 'M3U Manager', icon: 'playlist_play' },
    { id: 'epg-manager', label: 'EPG Manager', icon: 'schedule' },
    { id: 'logo-manager', label: 'Logo Manager', icon: 'image' },
  ] },
  { label: 'Automation', destinations: [
    { id: 'channel-pipeline', label: 'Channel Pipeline', icon: 'auto_fix_high' },
    { id: 'm3u-changes', label: 'M3U Changes', icon: 'compare_arrows' },
  ] },
  { label: 'Insights', destinations: [
    { id: 'stats', label: 'Stats', icon: 'analytics' },
    { id: 'journal', label: 'Journal', icon: 'history' },
  ] },
  { label: 'System', destinations: [{ id: 'settings', label: 'Settings', icon: 'settings' }] },
];

function readCollapsedPreference(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

export function TabNavigation({ activeTab, onTabChange, disabled, editModeActive }: TabNavigationProps) {
  const [collapsed, setCollapsed] = useState(readCollapsedPreference);

  const toggleCollapsed = () => {
    setCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(COLLAPSED_STORAGE_KEY, String(next));
      } catch {
        // Navigation remains usable when storage is unavailable.
      }
      return next;
    });
  };

  return (
    <aside className={`primary-sidebar ${collapsed ? 'is-collapsed' : ''} ${editModeActive ? 'edit-mode-active' : ''}`}>
      <div className="sidebar-brand">
        <img src={ECMLogo} alt="" className="sidebar-logo" />
        <span className="sidebar-product-name">Enhanced Channel Manager</span>
      </div>
      <nav aria-label="Primary" className="primary-navigation tab-navigation">
        {disabled && <span id="navigation-disabled-reason" className="visually-hidden">Navigation is unavailable while changes are being saved.</span>}
        {GROUPS.map((group) => (
          <section className="navigation-group" key={group.label}>
            <h2>{group.label}</h2>
            <ul>
              {group.destinations.map((destination) => (
                <li key={destination.id}>
                  <button
                    type="button"
                    data-tab={destination.id}
                    className={`navigation-destination tab-button ${activeTab === destination.id ? 'active' : ''}`}
                    aria-label={destination.label}
                    aria-current={activeTab === destination.id ? 'page' : undefined}
                    aria-describedby={disabled ? 'navigation-disabled-reason' : undefined}
                    title={disabled ? `${destination.label} — unavailable while changes are being saved` : destination.label}
                    onClick={() => onTabChange(destination.id)}
                    disabled={disabled}
                  >
                    <span className="material-icons" aria-hidden="true">{destination.icon}</span>
                    <span className="navigation-label">{destination.label}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </nav>
      <button
        type="button"
        className="navigation-collapse"
        onClick={toggleCollapsed}
        aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        aria-expanded={!collapsed}
        title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
      >
        <span className="material-icons" aria-hidden="true">{collapsed ? 'chevron_right' : 'chevron_left'}</span>
        <span className="navigation-collapse-label">Collapse</span>
      </button>
    </aside>
  );
}
