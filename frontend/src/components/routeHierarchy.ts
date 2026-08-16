import type { TabId } from './TabNavigation';
import type { RouteChangeGuardDetail, SettingsPage } from '../hooks/useHashRoute';

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

/**
 * What to do about a Back/Forward the router is asking permission for
 * (bead enhancedchannelmanager-6fi7p).
 *
 * `getGuardedRouteDecision` was only ever reachable from `handleRouteChange`,
 * which Back/Forward does not go through, so browser history navigation left
 * Edit Mode with staged changes and no confirmation at all. This is the
 * adapter that puts the same decision on the `ecm:before-route-change` event.
 *
 * `ignore` covers two very different things on purpose: a `push`-sourced
 * event, which `handleRouteChange` has ALREADY decided (guarding it again
 * would veto the operator's own confirmed exit), and a genuinely unguarded
 * destination.
 */
export function getGuardedPopStateDecision(
  detail: RouteChangeGuardDetail | undefined,
  isEditMode: boolean,
  stagedOperationCount: number,
): 'confirm' | 'exit-and-navigate' | 'ignore' {
  if (!detail || detail.source !== 'pop') return 'ignore';
  const decision = getGuardedRouteDecision(isEditMode, stagedOperationCount, detail.tab);
  return decision === 'navigate' ? 'ignore' : decision;
}

/**
 * Signing out ends the session, and the Edit Mode ledger is in memory, so it
 * discards staged work exactly as leaving the route does — but it is an SPA
 * state transition, so neither the route guard nor `beforeunload` can see it
 * (bead epic enhancedchannelmanager-r93hq).
 *
 * There is no destination tab to exempt here: `getGuardedRouteDecision` lets
 * a navigation to `channel-manager` through because Edit Mode SURVIVES it.
 * Nothing survives a sign-out. And unlike a route change there is no
 * `exit-and-navigate` case worth having — with no staged operations there is
 * nothing to lose and nothing to tidy, because the whole tree unmounts.
 */
export function getGuardedSignOutDecision(
  isEditMode: boolean,
  stagedOperationCount: number,
): 'confirm' | 'sign-out' {
  return isEditMode && stagedOperationCount > 0 ? 'confirm' : 'sign-out';
}

export interface PendingRouteChange {
  tab: TabId;
  settingsPage?: SettingsPage;
  /**
   * Set when the deferred navigation was a Back/Forward: the
   * `window.history.go()` argument that re-runs it. See
   * {@link resolvePendingRouteResume}.
   */
  historyDelta?: number | null;
  /** Undoes whatever the REQUESTER did before asking to navigate. */
  onCancel?: () => void;
}

/**
 * How to carry the operator to a deferred route once they have chosen to
 * leave Edit Mode (bead enhancedchannelmanager-6fi7p).
 *
 * A vetoed Back has already been rewound to the entry the operator was on, so
 * `history` replays it as a real Back: they land on the entry they asked for,
 * with the surrounding forward/back entries intact. `hash` is the fallback for
 * a deferred navigation that was never a history transition (a tab click, a
 * task-editor handoff) and for the one case where a Back cannot be replayed —
 * a target entry with no route index, which the router rewinds with
 * `replaceState` and therefore cannot address by delta.
 */
export function resolvePendingRouteResume(
  pending: PendingRouteChange,
): { kind: 'history'; delta: number } | { kind: 'hash'; tab: TabId; settingsPage?: SettingsPage } {
  if (typeof pending.historyDelta === 'number' && pending.historyDelta !== 0) {
    return { kind: 'history', delta: pending.historyDelta };
  }
  return { kind: 'hash', tab: pending.tab, settingsPage: pending.settingsPage };
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
  // No related-settings link, on the same reasoning that removed M3U Manager's
  // (see below, bead enhancedchannelmanager-hmr0e). Channel Defaults is a
  // standing Settings destination with its own navigation entry, so the header
  // link was a second path to a page that was never at risk of being orphaned.
  // Removing it leaves this header's meta row with no occupant, so
  // PageHeader.css collapses it here too — Channel Manager was the negative case
  // in that rule's test and is now a second positive one
  // (bead enhancedchannelmanager-mer2o).
  'channel-manager': route(
    'OPERATIONS',
    'channel-manager',
    'Build and maintain the channel lineup and its assigned streams.',
  ),
  guide: route('INSIGHTS', 'guide', 'Review scheduled programming across the active channel lineup.'),
  // No related-settings link. `#settings/linked-accounts` is a standing Settings
  // destination in its own right — it has a navigation entry of its own, and the
  // per-account case is covered by the account list's own "Manage Links" action —
  // so the header link was a third path to a page that was never at risk of being
  // orphaned (bead enhancedchannelmanager-hmr0e). Dropping it also leaves the
  // header's meta row with no occupant, which is what lets PageHeader.css collapse
  // it here for the first time.
  'm3u-manager': route(
    'OPERATIONS',
    'm3u-manager',
    'Configure and maintain provider playlists and their account connections.',
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
    'INSIGHTS',
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
  dashboard: { primaryAction: null, exception: 'Read-only operational summary; actions remain on destination pages.' },
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
