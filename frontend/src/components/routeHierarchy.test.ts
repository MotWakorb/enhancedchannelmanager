import { describe, expect, it } from 'vitest';
import { getGuardedRouteDecision, isPlainPrimaryActivation, ROUTE_HEADER_POLICIES, ROUTE_HIERARCHY } from './routeHierarchy';
import { ROUTE_TITLES } from './routeTitles';
import { GROUPS } from './navigationGroups';
import { SETTINGS_SECTIONS, settingsSectionHeading } from './settingsSections';

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
      '#settings/channel-defaults',
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
