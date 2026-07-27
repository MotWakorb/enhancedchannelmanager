import { describe, expect, it } from 'vitest';
import { ROUTE_HIERARCHY } from './routeHierarchy';
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

  it('uses only current stable Settings hashes for directly related configuration', () => {
    const links = Object.values(ROUTE_HIERARCHY).flatMap((route) => route.settingsLinks ?? []);
    expect(links.map((link) => link.href)).toEqual([
      '#settings/channel-defaults',
      '#settings/linked-accounts',
      '#settings/channel-pipeline',
      '#settings/m3u-digest',
    ]);
  });
});
