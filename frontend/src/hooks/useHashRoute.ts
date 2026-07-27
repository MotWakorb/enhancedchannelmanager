import { useState, useCallback, useEffect } from 'react';
import type { TabId } from '../components/TabNavigation';

export type SettingsPage = 'general' | 'channel-defaults' | 'normalization' | 'tag-engine' | 'lookup-tables' | 'appearance' | 'email' | 'integrations' | 'scheduled-tasks' | 'channel-pipeline' | 'm3u-digest' | 'maintenance' | 'linked-accounts' | 'auth-settings' | 'user-management' | 'tls-settings' | 'mcp-settings' | 'backup-restore';

const VALID_TABS: Set<string> = new Set([
  'dashboard', 'm3u-manager', 'epg-manager', 'channel-manager', 'guide',
  'logo-manager', 'm3u-changes', 'channel-pipeline', 'journal',
  'stats', 'settings',
]);

const VALID_SETTINGS_PAGES: Set<string> = new Set([
  'general', 'channel-defaults', 'normalization', 'tag-engine', 'lookup-tables',
  'appearance', 'email', 'integrations', 'scheduled-tasks', 'channel-pipeline',
  'm3u-digest', 'maintenance', 'linked-accounts', 'auth-settings',
  'user-management', 'tls-settings', 'mcp-settings', 'backup-restore',
]);

/**
 * Legacy hash values from before the Auto-Creation → Channel Pipeline rename
 * (enhancedchannelmanager-3udrl phase 4). Bookmarked/shared URLs using the old
 * tab id or settings sub-page must keep resolving to the renamed destination
 * instead of silently falling back to the default tab.
 */
const LEGACY_TAB_ALIASES: Record<string, TabId> = {
  'auto-creation': 'channel-pipeline',
};

const LEGACY_SETTINGS_PAGE_ALIASES: Record<string, SettingsPage> = {
  'auto-creation': 'channel-pipeline',
  // Administration → "Security" page removed (bead 09x38.12): its one
  // setting (backup-destination SSRF allowlist) relocated to Backup &
  // Restore. Bookmarked/shared #settings/security URLs keep resolving there
  // instead of silently falling back to settings/general.
  security: 'backup-restore',
};

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
    if (subPage in LEGACY_SETTINGS_PAGE_ALIASES) {
      return { tab: 'settings', settingsPage: LEGACY_SETTINGS_PAGE_ALIASES[subPage] };
    }
    if (VALID_SETTINGS_PAGES.has(subPage)) {
      return { tab: 'settings', settingsPage: subPage as SettingsPage };
    }
    // Invalid settings sub-page → fall back to settings/general
    return { tab: 'settings', settingsPage: null };
  }

  if (raw === 'settings') {
    return { tab: 'settings', settingsPage: null };
  }

  if (raw in LEGACY_TAB_ALIASES) {
    return { tab: LEGACY_TAB_ALIASES[raw], settingsPage: null };
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
    if (route.tab === tab && route.settingsPage === nextSettingsPage) {
      return;
    }
    window.history.pushState(null, '', buildHash(tab, settingsPage));
    setRoute({ tab, settingsPage: nextSettingsPage });
  }, [route]);

  // Update just the settings sub-page
  const setSettingsPage = useCallback((page: SettingsPage) => {
    setHash('settings', page);
  }, [setHash]);

  // Listen for popstate (back/forward buttons)
  useEffect(() => {
    const canonicalizeCurrentHash = () => {
      const parsed = parseHash(window.location.hash);
      const canonicalHash = buildHash(parsed.tab, parsed.settingsPage);
      if (window.location.hash !== canonicalHash) {
        window.history.replaceState(null, '', canonicalHash);
      }
      return parsed;
    };

    const handlePopState = () => {
      setRoute(canonicalizeCurrentHash());
    };

    setRoute(canonicalizeCurrentHash());
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  return { activeTab: route.tab, settingsPage: route.settingsPage, setHash, setSettingsPage };
}

// Export for testing
export { parseHash as _parseHash, buildHash as _buildHash };
