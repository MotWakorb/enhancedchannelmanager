import { useEffect, useRef, useState } from 'react';
import { EcmLogo } from './EcmLogo';
import { EcmWordmark } from './EcmWordmark';
import { GROUPS } from './navigationGroups';
import { settingsSectionGroups, type SettingsPage } from './settingsSections';
import './TabNavigation.css';

export type TabId = 'dashboard' | 'm3u-manager' | 'epg-manager' | 'channel-manager' | 'mapped-channels' | 'guide' | 'logo-manager' | 'm3u-changes' | 'channel-pipeline' | 'journal' | 'stats' | 'settings';

interface TabNavigationProps {
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
  disabled?: boolean;
  editModeActive?: boolean;
  settingsPage?: SettingsPage | null;
  onSettingsPageChange?: (page: SettingsPage) => void;
  isAdmin?: boolean;
}

const COLLAPSED_STORAGE_KEY = 'ecm.navigation.collapsed';

function readCollapsedPreference(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

export function TabNavigation({
  activeTab,
  onTabChange,
  disabled,
  editModeActive,
  settingsPage,
  onSettingsPageChange,
  isAdmin = false,
}: TabNavigationProps) {
  const [collapsed, setCollapsed] = useState(readCollapsedPreference);
  // Entering Settings drills the sidebar into its sections; Back returns to the
  // groups without leaving the Settings page, so this cannot simply be derived
  // from activeTab.
  const [showSettingsSections, setShowSettingsSections] = useState(activeTab === 'settings');
  // Null on first render so the sidebar does not slide on initial load or on a
  // Settings deep link; only an actual drill-in/out animates.
  const [slide, setSlide] = useState<'forward' | 'back' | null>(null);
  const previousTab = useRef(activeTab);

  const openSettingsSections = () => {
    setShowSettingsSections(true);
    setSlide('forward');
  };

  const closeSettingsSections = () => {
    setShowSettingsSections(false);
    setSlide('back');
  };

  useEffect(() => {
    if (previousTab.current !== activeTab) {
      const wasSettings = previousTab.current === 'settings';
      previousTab.current = activeTab;
      if (activeTab === 'settings') {
        openSettingsSections();
      } else {
        setShowSettingsSections(false);
        // Only a Settings exit slides back; other route changes leave the
        // already-visible groups alone.
        if (wasSettings) setSlide('back');
      }
    }
  }, [activeTab]);

  // Grouping is a property of SETTINGS_SECTIONS, not of this component: it was
  // a hardcoded two-way `adminOnly` split until bead
  // `enhancedchannelmanager-70u0r.2` made it an N-way derivation over
  // SETTINGS_GROUP_ORDER, so moving a destination between groups is now a data
  // change. Administration is still driven by `adminOnly` — see
  // settingsSectionGroups().
  const settingsGroups = settingsSectionGroups(isAdmin);
  const activeSettingsPage = settingsPage ?? 'general';

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
        {/* The logo is the only collapse control. Exactly one control may carry
            the "Collapse navigation"/"Expand navigation" phrase: Playwright's
            getByRole name option matches substrings, so a second control
            sharing it makes every query for this one ambiguous. */}
        <button
          type="button"
          className="sidebar-brand-toggle"
          onClick={toggleCollapsed}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          aria-expanded={!collapsed}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
        >
          <EcmLogo className="sidebar-logo" />
        </button>
        <EcmWordmark className="sidebar-product-name" />
      </div>
      {showSettingsSections ? (
        // Keyed so React remounts the element on each swap and replays the
        // slide rather than reconciling the two <nav>s into one node.
        <nav
          key="settings-sections"
          aria-label="Settings sections"
          className={`primary-navigation settings-navigation ${slide ? `nav-slide-${slide}` : ''}`}
        >
          {disabled && <span id="navigation-disabled-reason" className="visually-hidden">Navigation is unavailable while changes are being saved.</span>}
          <button
            type="button"
            className="navigation-back"
            onClick={closeSettingsSections}
            // Explicit name: the visible label is inside .navigation-label,
            // which the collapsed rail hides, leaving no accessible name.
            aria-label="Back to main navigation"
            title="Back to main navigation"
          >
            <span className="material-icons" aria-hidden="true">arrow_back</span>
            {/* Visible "Back" fits the 244px rail; the accessible name above
                contains it, satisfying WCAG 2.5.3 Label in Name. */}
            <span className="navigation-label">Back</span>
          </button>
          {settingsGroups.map((group) => (
          <section className="navigation-group" key={group.label}>
            <h2>{group.label}</h2>
            <ul>
              {group.sections.map((section) => (
                <li key={section.id}>
                  <a
                    href={disabled ? undefined : `#settings/${section.id}`}
                    role="link"
                    tabIndex={0}
                    data-settings-page={section.id}
                    className={`navigation-destination settings-nav-item ${activeSettingsPage === section.id ? 'active' : ''}`}
                    aria-label={section.label}
                    aria-current={activeSettingsPage === section.id ? 'page' : undefined}
                    aria-describedby={disabled ? 'navigation-disabled-reason' : undefined}
                    title={disabled ? `${section.label} — unavailable while changes are being saved` : section.label}
                    aria-disabled={disabled ? 'true' : undefined}
                    onClick={(event) => {
                      if (disabled) {
                        event.preventDefault();
                        return;
                      }
                      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
                        return;
                      }
                      event.preventDefault();
                      onSettingsPageChange?.(section.id);
                    }}
                    onAuxClick={(event) => {
                      if (disabled) event.preventDefault();
                    }}
                    onContextMenu={(event) => {
                      if (disabled) event.preventDefault();
                    }}
                  >
                    <span className="material-icons" aria-hidden="true">{section.icon}</span>
                    <span className="navigation-label">{section.label}</span>
                  </a>
                </li>
              ))}
            </ul>
          </section>
          ))}
        </nav>
      ) : (
      <nav
        key="primary-groups"
        aria-label="Primary"
        className={`primary-navigation tab-navigation ${slide ? `nav-slide-${slide}` : ''}`}
      >
        {disabled && <span id="navigation-disabled-reason" className="visually-hidden">Navigation is unavailable while changes are being saved.</span>}
        {GROUPS.map((group) => (
          <section className="navigation-group" key={group.label}>
            <h2>{group.label}</h2>
            <ul>
              {group.destinations.map((destination) => (
                <li key={destination.id}>
                  <a
                    href={disabled ? undefined : `#${destination.id}`}
                    role="link"
                    tabIndex={0}
                    data-tab={destination.id}
                    className={`navigation-destination tab-button ${activeTab === destination.id ? 'active' : ''}`}
                    aria-label={destination.label}
                    aria-current={activeTab === destination.id ? 'page' : undefined}
                    aria-describedby={disabled ? 'navigation-disabled-reason' : undefined}
                    title={disabled ? `${destination.label} — unavailable while changes are being saved` : destination.label}
                    aria-disabled={disabled ? 'true' : undefined}
                    onClick={(event) => {
                      if (disabled) {
                        event.preventDefault();
                        return;
                      }
                      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
                        return;
                      }
                      event.preventDefault();
                      // Re-selecting Settings while already on it must reopen
                      // the sections; the activeTab effect cannot see that.
                      if (destination.id === 'settings') openSettingsSections();
                      onTabChange(destination.id);
                    }}
                    onAuxClick={(event) => {
                      if (disabled) event.preventDefault();
                    }}
                    onContextMenu={(event) => {
                      if (disabled) event.preventDefault();
                    }}
                  >
                    <span className="material-icons" aria-hidden="true">{destination.icon}</span>
                    <span className="navigation-label">{destination.label}</span>
                  </a>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </nav>
      )}
    </aside>
  );
}
