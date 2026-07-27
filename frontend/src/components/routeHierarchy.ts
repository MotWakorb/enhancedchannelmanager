import type { TabId } from './TabNavigation';

type LinkActivation = Pick<MouseEvent, 'button' | 'ctrlKey' | 'metaKey' | 'shiftKey' | 'altKey'>;

export function isPlainPrimaryActivation(event: LinkActivation): boolean {
  return event.button === 0
    && !event.ctrlKey
    && !event.metaKey
    && !event.shiftKey
    && !event.altKey;
}

export function getGuardedRouteDecision(
  isEditMode: boolean,
  stagedOperationCount: number,
  target: TabId,
): 'confirm' | 'exit-and-navigate' | 'navigate' {
  if (!isEditMode || target === 'channel-manager') return 'navigate';
  return stagedOperationCount > 0 ? 'confirm' : 'exit-and-navigate';
}
import { ROUTE_TITLES } from './routeTitles';

export interface RouteSettingsLink {
  href: `#settings/${string}`;
  label: string;
}

interface RouteHierarchy {
  group: 'OVERVIEW' | 'OPERATIONS' | 'AUTOMATION' | 'INSIGHTS' | 'SYSTEM';
  heading: string;
  purpose: string;
  settingsLinks?: RouteSettingsLink[];
}

function route(
  group: RouteHierarchy['group'],
  tab: TabId,
  purpose: string,
  settingsLinks?: RouteSettingsLink[],
): RouteHierarchy {
  return { group, heading: `${group} / ${ROUTE_TITLES[tab].toUpperCase()}`, purpose, settingsLinks };
}

export const ROUTE_HIERARCHY: Record<TabId, RouteHierarchy> = {
  dashboard: route('OVERVIEW', 'dashboard', 'Review ECM status and move directly to the workspace that needs attention.'),
  'channel-manager': route(
    'OPERATIONS',
    'channel-manager',
    'Build and maintain the channel lineup and its assigned streams.',
    [{ href: '#settings/channel-defaults', label: 'Channel default settings' }],
  ),
  guide: route('OPERATIONS', 'guide', 'Review scheduled programming across the active channel lineup.'),
  'm3u-manager': route(
    'OPERATIONS',
    'm3u-manager',
    'Configure and maintain provider playlists and their account connections.',
    [{ href: '#settings/linked-accounts', label: 'Linked account settings' }],
  ),
  'epg-manager': route('OPERATIONS', 'epg-manager', 'Configure the programme-guide sources used to enrich channels.'),
  'logo-manager': route('OPERATIONS', 'logo-manager', 'Organize and maintain artwork used throughout the channel lineup.'),
  'channel-pipeline': route(
    'AUTOMATION',
    'channel-pipeline',
    'Define and monitor rules that automate channel processing.',
    [{ href: '#settings/channel-pipeline', label: 'Channel Pipeline settings' }],
  ),
  'm3u-changes': route(
    'AUTOMATION',
    'm3u-changes',
    'Review provider playlist changes before acting on lineup differences.',
    [{ href: '#settings/m3u-digest', label: 'M3U digest settings' }],
  ),
  stats: route('INSIGHTS', 'stats', 'Monitor current playback activity and channel performance.'),
  journal: route('INSIGHTS', 'journal', 'Trace recorded channel, guide, provider, and automation events.'),
  settings: route('SYSTEM', 'settings', 'Configure ECM behavior, integrations, access, and maintenance.'),
};

export interface RouteHeaderPolicy {
  primaryAction: string | null;
  exception?: string;
}

export const ROUTE_HEADER_POLICIES: Record<TabId, RouteHeaderPolicy> = {
  dashboard: { primaryAction: null, exception: 'Read-only placeholder until the Dashboard delivery.' },
  'channel-manager': { primaryAction: 'Edit Mode' },
  guide: { primaryAction: null, exception: 'Guide refresh, print, and temporal selectors remain one source-scoped control group.' },
  'm3u-manager': { primaryAction: 'Add M3U Account' },
  'epg-manager': { primaryAction: 'Add Standard EPG' },
  'logo-manager': { primaryAction: 'Add Logo' },
  'channel-pipeline': { primaryAction: 'Create Rule' },
  'm3u-changes': { primaryAction: 'Refresh' },
  stats: { primaryAction: 'Refresh' },
  journal: { primaryAction: 'Refresh' },
  settings: { primaryAction: null, exception: 'Save remains form-scoped for the long-page/save-safety delivery (.5).' },
};
