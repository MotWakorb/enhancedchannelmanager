import { describe, expect, it } from 'vitest';
import {
  getGuardedPopStateDecision,
  getGuardedRouteDecision,
  getGuardedSignOutDecision,
  isPlainPrimaryActivation,
  resolvePendingRouteResume,
  ROUTE_HEADER_POLICIES,
  ROUTE_HIERARCHY,
} from './routeHierarchy';
import type { RouteChangeGuardDetail } from '../hooks/useHashRoute';
import { ROUTE_TITLES } from './routeTitles';
import { GROUPS } from './navigationGroups';
import {
  SETTINGS_GROUP_ORDER,
  SETTINGS_PAGE_IDS,
  SETTINGS_SECTIONS,
  isSettingsPage,
  settingsSectionGroups,
  settingsSectionHeading,
  visibleSettingsSections,
} from './settingsSections';

describe('primary route hierarchy', () => {
  // The sidebar group and the page-header breadcrumb are separate declarations.
  // Moving a destination between nav groups without updating ROUTE_HIERARCHY
  // leaves the breadcrumb contradicting the sidebar, which is invisible to any
  // test that hardcodes the old heading.
  it('gives every destination the same group in the sidebar and the page header', () => {
    const navGroupByRoute = new Map(
      GROUPS.flatMap((group) => group.destinations.map((destination) => [destination.id, group.label.toUpperCase()])),
    );

    expect([...navGroupByRoute.keys()].sort()).toEqual(Object.keys(ROUTE_HIERARCHY).sort());
    for (const [route, navGroup] of navGroupByRoute) {
      expect(ROUTE_HIERARCHY[route].group, `${route} sidebar group vs page-header group`).toBe(navGroup);
    }
  });

  it('defines an approved group and one-sentence purpose for every primary route', () => {
    expect(Object.keys(ROUTE_HIERARCHY).sort()).toEqual(Object.keys(ROUTE_TITLES).sort());
    for (const [route, hierarchy] of Object.entries(ROUTE_HIERARCHY)) {
      expect(hierarchy.heading).toBe(`${hierarchy.group} / ${ROUTE_TITLES[route as keyof typeof ROUTE_TITLES].toUpperCase()}`);
      expect(hierarchy.purpose).toMatch(/^[A-Z].*[.!?]$/);
    }
    expect(ROUTE_HIERARCHY['channel-manager'].heading).toBe('OPERATIONS / CHANNEL MANAGER');
  });

  // Settings section headings were removed from the content pane and are now
  // rendered only by the route breadcrumb, so every section must resolve to a
  // usable title there or the page ends up with no heading at all.
  it('resolves a breadcrumb heading for every Settings section', () => {
    for (const section of SETTINGS_SECTIONS) {
      const heading = settingsSectionHeading(section.id);
      expect(heading.title, `${section.id} breadcrumb title`).toBeTruthy();
      expect(heading.title).toBe(section.title ?? section.label);
      if (heading.description !== undefined) {
        expect(heading.description, `${section.id} description`).toMatch(/^[A-Z].*[.!?]$/s);
      }
    }
  });

  // The grouping analogue of the crumb check above. A section whose `group` is
  // not a declared group renders nowhere at all — `settingsSectionGroups`
  // iterates SETTINGS_GROUP_ORDER, so an unrecognised value silently drops the
  // destination out of the sidebar rather than failing loudly.
  it('places every Settings section in exactly one declared group', () => {
    const declared = new Set<string>(SETTINGS_GROUP_ORDER);
    for (const section of SETTINGS_SECTIONS) {
      expect(declared.has(section.group), `${section.id} group "${section.group}"`).toBe(true);
    }
    expect(new Set(SETTINGS_GROUP_ORDER).size).toBe(SETTINGS_GROUP_ORDER.length);

    const grouped = settingsSectionGroups(true).flatMap((group) => group.sections.map((section) => section.id));
    expect(grouped).toHaveLength(SETTINGS_SECTIONS.length);
    expect(new Set(grouped).size).toBe(grouped.length);
  });

  // The approved assignment, pinned literally. PO decision D1 on epic
  // enhancedchannelmanager-70u0r, including the amendment that moved Scheduled
  // Tasks out of Notifications & Reports into Upkeep: it is EPG refresh, M3U
  // refresh and database cleanup, which is upkeep rather than reporting.
  //
  // Written out longhand rather than derived, so moving a destination between
  // groups has to argue with a named expectation instead of quietly passing.
  // Channel Processing is four destinations since bead
  // enhancedchannelmanager-70u0r.1 retired Lookup Tables (PO decision D2).
  it('renders the approved Settings groups, names and order for an administrator', () => {
    expect(settingsSectionGroups(true).map((group) => [group.label, group.sections.map((section) => section.label)])).toEqual([
      ['Connections', ['General', 'Integrations']],
      ['Channel Processing', ['Channel Defaults', 'Channel Normalization', 'Tags', 'Channel Pipeline']],
      ['Notifications & Reports', ['Notification Settings', 'M3U Digest']],
      ['Upkeep', ['Scheduled Tasks', 'Maintenance', 'Backup & Restore']],
      ['Workspace', ['Appearance', 'Linked Accounts']],
      ['Administration', ['Authentication', 'User Management', 'TLS Certificates', 'MCP Integration']],
    ]);
  });

  // Administration must stay derived from `adminOnly` rather than from the
  // group label. If the two ever disagree, a non-admin either loses a section
  // they are entitled to or is shown an Administration heading over an empty
  // list — the failure the empty-group filter exists to prevent.
  it('keeps the Administration group and adminOnly in agreement', () => {
    for (const section of SETTINGS_SECTIONS) {
      expect(Boolean(section.adminOnly), `${section.id} adminOnly vs group`).toBe(section.group === 'Administration');
    }

    const nonAdmin = settingsSectionGroups(false);
    expect(nonAdmin.map((group) => group.label)).toEqual(
      SETTINGS_GROUP_ORDER.filter((label) => label !== 'Administration'),
    );
    for (const group of [...nonAdmin, ...settingsSectionGroups(true)]) {
      expect(group.sections.length, `${group.label} is rendered with no sections`).toBeGreaterThan(0);
    }
    expect(nonAdmin.flatMap((group) => group.sections)).toEqual(visibleSettingsSections(false));
  });

  // Bead enhancedchannelmanager-70u0r.7: the SettingsPage union,
  // VALID_SETTINGS_PAGES and SETTINGS_SECTIONS were three hand-maintained lists
  // of the same ids. They are now one declaration with the other two derived,
  // so this asserts the derivation rather than three lists agreeing by hand.
  it('routes exactly the declared Settings ids and nothing else', () => {
    expect(SETTINGS_PAGE_IDS).toEqual(SETTINGS_SECTIONS.map((section) => section.id));
    expect(new Set(SETTINGS_PAGE_IDS).size).toBe(SETTINGS_PAGE_IDS.length);
    for (const id of SETTINGS_PAGE_IDS) {
      expect(isSettingsPage(id), `${id} is not routable`).toBe(true);
    }
    // Retired and never-declared ids must fail the guard so they reach the
    // alias table or the invalid-sub-page fallback instead of routing.
    for (const id of ['security', 'auto-creation', 'not-a-settings-page', '']) {
      expect(isSettingsPage(id), `${id} must not be routable`).toBe(false);
    }
  });

  it('identifies one primary action or records an approved route exception', () => {
    expect(Object.keys(ROUTE_HEADER_POLICIES).sort()).toEqual(Object.keys(ROUTE_HIERARCHY).sort());
    for (const policy of Object.values(ROUTE_HEADER_POLICIES)) {
      expect(Boolean(policy.primaryAction) !== Boolean(policy.exception)).toBe(true);
    }
    expect(ROUTE_HEADER_POLICIES.settings.exception).toContain('.5');
    expect(ROUTE_HEADER_POLICIES.guide.exception).toContain('source-scoped');
  });

  it('uses only current stable Settings hashes for directly related configuration', () => {
    const links = Object.values(ROUTE_HIERARCHY).flatMap((route) => route.settingsLinks ?? []);
    expect(links.map((link) => link.href)).toEqual([
      '#settings/channel-pipeline',
      '#settings/m3u-digest',
    ]);
    // Bead enhancedchannelmanager-hmr0e removed M3U Manager's
    // `#settings/linked-accounts` link. Pinned by route rather than left to the
    // list above, so a re-add has to argue with a named expectation instead of
    // quietly extending an array: Linked Accounts carries its own Settings
    // navigation entry and the account list's own "Manage Links" action, so the
    // header link was a third path to a page that was never at risk of being
    // orphaned.
    expect(ROUTE_HIERARCHY['m3u-manager'].settingsLinks).toBeUndefined();
    // Bead enhancedchannelmanager-mer2o removed Channel Manager's
    // `#settings/channel-defaults` link on the same reasoning, and is pinned the
    // same way: Channel Defaults carries its own Settings navigation entry, so
    // the header link was a second path to a page never at risk of being
    // orphaned.
    expect(ROUTE_HIERARCHY['channel-manager'].settingsLinks).toBeUndefined();
  });

  it.each([
    [{ button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false }, true],
    [{ button: 0, ctrlKey: true, metaKey: false, shiftKey: false, altKey: false }, false],
    [{ button: 0, ctrlKey: false, metaKey: true, shiftKey: false, altKey: false }, false],
    [{ button: 0, ctrlKey: false, metaKey: false, shiftKey: true, altKey: false }, false],
    [{ button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: true }, false],
    [{ button: 1, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false }, false],
  ])('guards only plain primary link activation (%o)', (activation, expected) => {
    expect(isPlainPrimaryActivation(activation)).toBe(expected);
  });

  it.each([
    [true, 2, 'settings', 'confirm'],
    [true, 0, 'settings', 'exit-and-navigate'],
    [false, 0, 'settings', 'navigate'],
    [true, 2, 'channel-manager', 'navigate'],
  ] as const)(
    'uses the existing edit-mode exit policy (edit=%s, staged=%s, target=%s)',
    (isEditMode, stagedCount, target, expected) => {
      expect(getGuardedRouteDecision(isEditMode, stagedCount, target)).toBe(expected);
    },
  );
});

/**
 * bead enhancedchannelmanager-6fi7p. `getGuardedRouteDecision` was reachable
 * only from `handleRouteChange`, and Back/Forward does not go through it, so
 * an operator with staged channel edits who pressed Back left Edit Mode with
 * no confirmation and no staged work. This is the same policy, put where the
 * router's own permission question can reach it.
 */
describe('browser Back/Forward guard', () => {
  const popDetail = (over: Partial<RouteChangeGuardDetail> = {}): RouteChangeGuardDetail => ({
    from: '#channel-manager',
    to: '#settings',
    source: 'pop',
    historyDelta: -1,
    tab: 'settings',
    settingsPage: null,
    ...over,
  });

  it('asks the operator before a Back that would discard staged work', () => {
    expect(getGuardedPopStateDecision(popDetail(), true, 3)).toBe('confirm');
  });

  it('exits Edit Mode and lets a Back through when nothing is staged', () => {
    expect(getGuardedPopStateDecision(popDetail(), true, 0)).toBe('exit-and-navigate');
  });

  it('leaves a Back alone when Edit Mode is not active', () => {
    expect(getGuardedPopStateDecision(popDetail(), false, 0)).toBe('ignore');
  });

  it('leaves a Back INTO Channel Manager alone — Edit Mode survives it', () => {
    expect(
      getGuardedPopStateDecision(popDetail({ tab: 'channel-manager', to: '#channel-manager' }), true, 3),
    ).toBe('ignore');
  });

  /**
   * The load-bearing one. `handleRouteChange` has already put this question to
   * the operator and acted on the answer; the `setHash` that follows is the
   * ACT of leaving, including the one that carries them out of Edit Mode after
   * they chose Apply or Discard. A guard that answered `confirm` here would
   * re-open the dialog it was closing.
   */
  it('never guards a programmatic navigation — handleRouteChange already did', () => {
    expect(getGuardedPopStateDecision(popDetail({ source: 'push', historyDelta: null }), true, 3))
      .toBe('ignore');
  });

  it('ignores an event with no detail rather than assuming a destination', () => {
    expect(getGuardedPopStateDecision(undefined, true, 3)).toBe('ignore');
  });
});

/**
 * bead epic enhancedchannelmanager-r93hq. Signing out unmounts the app and
 * takes the in-memory Edit Mode ledger with it. `handleLogout` consulted
 * nothing at all, and because it is an SPA state transition the `beforeunload`
 * guard never fired either.
 */
describe('sign-out guard', () => {
  it('asks the operator before signing out with staged work', () => {
    expect(getGuardedSignOutDecision(true, 3)).toBe('confirm');
  });

  it('signs straight out when Edit Mode is active but nothing is staged', () => {
    expect(getGuardedSignOutDecision(true, 0)).toBe('sign-out');
  });

  it('signs straight out when Edit Mode is not active', () => {
    expect(getGuardedSignOutDecision(false, 0)).toBe('sign-out');
  });

  /**
   * The route guard exempts `channel-manager` because Edit Mode SURVIVES a
   * navigation back to its own route. Nothing survives a sign-out, so the
   * sign-out guard has no such exemption — and this is exactly the case an
   * operator is in when they sign out: standing on Channel Manager, in Edit
   * Mode, with staged changes.
   */
  it('does not inherit the route guard exemption for Channel Manager', () => {
    expect(getGuardedRouteDecision(true, 3, 'channel-manager')).toBe('navigate');
    expect(getGuardedSignOutDecision(true, 3)).toBe('confirm');
  });
});

/**
 * bead enhancedchannelmanager-6fi7p — the "and then actually go back" half.
 * A vetoed Back is rewound to the entry the operator was on, so honouring it
 * afterwards has to REPLAY the history transition. A pushState to the same
 * hash would leave them on a new entry with the one they pressed Back from
 * still sitting behind them.
 */
describe('resuming a deferred navigation', () => {
  it('replays a deferred Back as a history transition', () => {
    expect(resolvePendingRouteResume({ tab: 'settings', historyDelta: -2 }))
      .toEqual({ kind: 'history', delta: -2 });
  });

  it('replays a deferred Forward the same way', () => {
    expect(resolvePendingRouteResume({ tab: 'stats', historyDelta: 1 }))
      .toEqual({ kind: 'history', delta: 1 });
  });

  it.each([
    ['a tab click, which was never a history transition', undefined],
    ['an entry with no route index, which cannot be addressed by delta', null],
    ['a zero delta, which would move nothing', 0],
  ])('falls back to the hash for %s', (_why, historyDelta) => {
    expect(resolvePendingRouteResume({ tab: 'settings', settingsPage: 'maintenance', historyDelta }))
      .toEqual({ kind: 'hash', tab: 'settings', settingsPage: 'maintenance' });
  });
});
