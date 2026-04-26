import { useState, useCallback, useEffect } from 'react';
import type { TabId } from '../components/TabNavigation';

export type SettingsPage = 'general' | 'channel-defaults' | 'normalization' | 'tag-engine' | 'lookup-tables' | 'appearance' | 'email' | 'scheduled-tasks' | 'auto-creation' | 'm3u-digest' | 'maintenance' | 'linked-accounts' | 'auth-settings' | 'user-management' | 'tls-settings' | 'mcp-settings' | 'backup-restore';

const VALID_TABS: Set<string> = new Set([
  'm3u-manager', 'epg-manager', 'channel-manager', 'guide',
  'logo-manager', 'm3u-changes', 'auto-creation', 'journal',
  'stats', 'ffmpeg-builder', 'settings',
]);

const VALID_SETTINGS_PAGES: Set<string> = new Set([
  'general', 'channel-defaults', 'normalization', 'tag-engine', 'lookup-tables',
  'appearance', 'email', 'scheduled-tasks', 'auto-creation',
  'm3u-digest', 'maintenance', 'linked-accounts', 'auth-settings',
  'user-management', 'tls-settings', 'mcp-settings', 'backup-restore',
]);

const DEFAULT_TAB: TabId = 'channel-manager';

interface HashRoute {
  tab: TabId;
  settingsPage: SettingsPage | null;
}

function parseHash(hash: string): HashRoute {
  // Strip leading '#'
  const raw = hash.replace(/^#/, '');
  if (!raw) return { tab: DEFAULT_TAB, settingsPage: null };

  // Check for settings/sub-page format
  if (raw.startsWith('settings/')) {
    const subPage = raw.slice('settings/'.length);
    if (VALID_SETTINGS_PAGES.has(subPage)) {
      return { tab: 'settings', settingsPage: subPage as SettingsPage };
    }
    // Invalid settings sub-page → fall back to settings/general
    return { tab: 'settings', settingsPage: null };
  }

  if (raw === 'settings') {
    return { tab: 'settings', settingsPage: null };
  }

  if (VALID_TABS.has(raw)) {
    return { tab: raw as TabId, settingsPage: null };
  }

  // Invalid hash → default
  return { tab: DEFAULT_TAB, settingsPage: null };
}

function buildHash(tab: TabId, settingsPage?: SettingsPage | null): string {
  if (tab === 'settings' && settingsPage && settingsPage !== 'general') {
    return `#settings/${settingsPage}`;
  }
  return `#${tab}`;
}

export interface UseHashRouteReturn {
  activeTab: TabId;
  settingsPage: SettingsPage | null;
  setHash: (tab: TabId, settingsPage?: SettingsPage | null) => void;
  setSettingsPage: (page: SettingsPage) => void;
}

export function useHashRoute(): UseHashRouteReturn {
  const [route, setRoute] = useState<HashRoute>(() => parseHash(window.location.hash));

  // Bail out when the route is unchanged so a caller that loops can't churn pushState + a fresh-object re-render. Uses pushState (not assign) to avoid a hashchange/popstate echo.
  const setHash = useCallback((tab: TabId, settingsPage?: SettingsPage | null) => {
    const nextSettingsPage = settingsPage ?? null;
    setRoute((prev) => {
      if (prev.tab === tab && prev.settingsPage === nextSettingsPage) {
        return prev;
      }
      window.history.pushState(null, '', buildHash(tab, settingsPage));
      return { tab, settingsPage: nextSettingsPage };
    });
  }, []);

  // Update just the settings sub-page
  const setSettingsPage = useCallback((page: SettingsPage) => {
    setHash('settings', page);
  }, [setHash]);

  // Listen for popstate (back/forward buttons)
  useEffect(() => {
    const handlePopState = () => {
      const parsed = parseHash(window.location.hash);
      setRoute(parsed);
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Set initial hash if none present (so URL reflects current tab)
  useEffect(() => {
    if (!window.location.hash) {
      window.history.replaceState(null, '', buildHash(DEFAULT_TAB));
    }
  }, []);

  return { activeTab: route.tab, settingsPage: route.settingsPage, setHash, setSettingsPage };
}

// Export for testing
export { parseHash as _parseHash, buildHash as _buildHash };
