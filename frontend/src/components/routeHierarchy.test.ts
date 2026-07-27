import { describe, expect, it } from 'vitest';
import { getGuardedRouteDecision, isPlainPrimaryActivation, ROUTE_HEADER_POLICIES, ROUTE_HIERARCHY } from './routeHierarchy';
import { ROUTE_TITLES } from './routeTitles';

describe('primary route hierarchy', () => {
  it('defines an approved group and one-sentence purpose for every primary route', () => {
    expect(Object.keys(ROUTE_HIERARCHY).sort()).toEqual(Object.keys(ROUTE_TITLES).sort());
    for (const [route, hierarchy] of Object.entries(ROUTE_HIERARCHY)) {
      expect(hierarchy.heading).toBe(`${hierarchy.group} / ${ROUTE_TITLES[route as keyof typeof ROUTE_TITLES].toUpperCase()}`);
      expect(hierarchy.purpose).toMatch(/^[A-Z].*[.!?]$/);
    }
    expect(ROUTE_HIERARCHY['channel-manager'].heading).toBe('OPERATIONS / CHANNEL MANAGER');
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
      '#settings/linked-accounts',
      '#settings/channel-pipeline',
      '#settings/m3u-digest',
    ]);
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
