import type { TabId } from './TabNavigation';

export interface NavigationDestination {
  id: TabId;
  label: string;
  icon: string;
}

export interface NavigationGroup {
  label: string;
  destinations: NavigationDestination[];
}

// Lives outside TabNavigation.tsx so both the component and the route-hierarchy
// consistency check can read it without that file exporting a non-component.
//
// Operations follows the operator's setup order: sources first (M3U), then
// programme data (EPG), then artwork (logos), and finally the Channel Manager
// that consumes all three. Insights collects the read-mostly surfaces.
//
// Every destination's group must match ROUTE_HIERARCHY, which supplies the
// page-header breadcrumb; routeHierarchy.test.ts enforces that.
export const GROUPS: NavigationGroup[] = [
  { label: 'Overview', destinations: [{ id: 'dashboard', label: 'Dashboard', icon: 'dashboard' }] },
  { label: 'Operations', destinations: [
    { id: 'm3u-manager', label: 'M3U Manager', icon: 'playlist_play' },
    { id: 'epg-manager', label: 'EPG Manager', icon: 'schedule' },
    { id: 'logo-manager', label: 'Logo Manager', icon: 'image' },
    { id: 'channel-manager', label: 'Channel Manager', icon: 'tv' },
    { id: 'mapped-channels', label: 'Mapped channels', icon: 'link' },
  ] },
  { label: 'Automation', destinations: [
    { id: 'channel-pipeline', label: 'Channel Pipeline', icon: 'auto_fix_high' },
  ] },
  { label: 'Insights', destinations: [
    { id: 'guide', label: 'Guide', icon: 'grid_on' },
    { id: 'stats', label: 'Stats', icon: 'analytics' },
    { id: 'm3u-changes', label: 'M3U Changes', icon: 'compare_arrows' },
    { id: 'journal', label: 'Journal', icon: 'history' },
  ] },
  { label: 'System', destinations: [{ id: 'settings', label: 'Settings', icon: 'settings' }] },
];
