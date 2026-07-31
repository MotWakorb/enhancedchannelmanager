import { createRef } from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PageHeader } from './PageHeader';
import { isPlainPrimaryActivation, ROUTE_HIERARCHY } from './routeHierarchy';

describe('PageHeader', () => {
  it('renders the primary page hierarchy with one semantic h1', () => {
    const ref = createRef<HTMLHeadingElement>();
    render(
      <PageHeader
        headingLevel={1}
        headingRef={ref}
        group="OPERATIONS"
        title="CHANNEL MANAGER"
        description="Build and maintain the channel lineup and its assigned streams."
        actions={<button type="button">Edit Mode</button>}
      />,
    );
    const heading = screen.getByRole('heading', { level: 1 });
    const action = screen.getByRole('button', { name: 'Edit Mode' });
    expect(heading).toHaveTextContent('OPERATIONS / CHANNEL MANAGER');
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByText('Build and maintain the channel lineup and its assigned streams.'))
      .toHaveAttribute('title', 'Build and maintain the channel lineup and its assigned streams.');
    expect(ref.current).toBe(heading);
    expect(heading.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it.each([
    ['Control', { ctrlKey: true }],
    ['Meta', { metaKey: true }],
    ['Shift', { shiftKey: true }],
    ['auxiliary', { button: 1 }],
  ])('preserves native %s-click behavior for contextual links', (_label, init) => {
    const onClick = vi.fn((event: React.MouseEvent<HTMLAnchorElement>) => {
      if (isPlainPrimaryActivation(event.nativeEvent)) event.preventDefault();
    });
    render(
      <PageHeader
        title="Channel Pipeline"
        relatedLinks={[{ href: '#settings/channel-pipeline', label: 'Channel Pipeline settings', onClick }]}
      />,
    );
    const link = screen.getByRole('link', { name: 'Channel Pipeline settings' });
    expect(link).toHaveAttribute('href', '#settings/channel-pipeline');
    expect(fireEvent.click(link, init)).toBe(true);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('intercepts a plain primary contextual-link activation', () => {
    const onClick = vi.fn((event: React.MouseEvent<HTMLAnchorElement>) => {
      if (isPlainPrimaryActivation(event.nativeEvent)) event.preventDefault();
    });
    render(
      <PageHeader
        title="Channel Manager"
        relatedLinks={[{ href: '#settings/channel-defaults', label: 'Channel default settings', onClick }]}
      />,
    );
    expect(fireEvent.click(screen.getByRole('link', { name: 'Channel default settings' }))).toBe(false);
  });

  // Order changed in bead enhancedchannelmanager-sccol. It used to be
  // action -> status -> controls -> links (bead 57pp3), which put the status
  // between the two interactive clusters; with no column-gap on the header
  // row that rendered as "6 provider accounts" touching Add M3U Account and
  // Save Priorities on either side. The header now separates what is
  // operated (row one) from what is read (the meta row), so the status
  // travels with the related links.
  //
  // Posed on M3U Changes rather than M3U Manager: M3U Manager no longer declares
  // a related-settings link (bead enhancedchannelmanager-hmr0e), and a test that
  // hands PageHeader a link the route does not have pins a composition nothing
  // renders. M3U Changes still fills both meta slots, so the ordering it asserts
  // is one the app actually produces.
  it('orders primary action, controls, then a meta row of status and contextual links', () => {
    render(
      <PageHeader
        title="M3U Changes"
        actions={<button type="button">Refresh</button>}
        status={<span>Refreshing provider data…</span>}
        controls={<label>View <select><option>All accounts</option></select></label>}
        relatedLinks={[{ href: '#settings/m3u-digest', label: 'M3U digest settings' }]}
      />,
    );
    const action = screen.getByRole('button', { name: 'Refresh' });
    const status = screen.getByText('Refreshing provider data…');
    const controls = screen.getByRole('combobox', { name: 'View' });
    const link = screen.getByRole('link', { name: 'M3U digest settings' });
    for (const [before, after] of [[action, controls], [controls, status], [status, link]]) {
      expect(before.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    // Status and links share one meta row rather than being separate flex
    // items of the header row, which is what makes the collision structurally
    // impossible rather than merely spaced apart.
    expect(status.closest('.page-header-meta')).not.toBeNull();
    expect(status.closest('.page-header-meta')).toBe(link.closest('.page-header-meta'));
  });

  it('exposes distinct hierarchy slots instead of an opaque toolbar cluster', () => {
    render(
      <PageHeader
        title="Stats"
        actions={<button>Refresh</button>}
        status={<span>Auto-refresh: 30s</span>}
        controls={<button>Refresh interval</button>}
        relatedLinks={[{ href: '#settings/general', label: 'General settings' }]}
      />,
    );
    expect(screen.getByRole('button', { name: 'Refresh' }).closest('[data-page-header-slot]'))
      .toHaveAttribute('data-page-header-slot', 'primary-action');
    expect(screen.getByText('Auto-refresh: 30s').closest('[data-page-header-slot]'))
      .toHaveAttribute('data-page-header-slot', 'status');
    expect(screen.getByRole('button', { name: 'Refresh interval' }).closest('[data-page-header-slot]'))
      .toHaveAttribute('data-page-header-slot', 'controls');
    expect(screen.getByText('Auto-refresh: 30s').closest('[data-page-header-slot]'))
      .not.toHaveAttribute('aria-live');
  });

  it('only creates a concise live status region when explicitly requested', () => {
    render(<PageHeader title="M3U Manager" status={<span>Refresh complete</span>} statusLive />);
    expect(screen.getByText('Refresh complete').closest('[data-page-header-slot]'))
      .toHaveAttribute('aria-live', 'polite');
  });

  // Read from disk: vitest stubs CSS imports, so getComputedStyle in jsdom
  // would make these assertions vacuous (same reason as the TabNavigation
  // keyframe test). Bead enhancedchannelmanager-meh0a — the h2 used to be
  // `font-size: 1.5rem`, the same 24px as the route title one row above it.
  it('sizes the section heading and the route title from their roles, never a bare size', () => {
    const shared = readFileSync(resolve(process.cwd(), 'src/shared/common.css'), 'utf8');
    const sectionHeading = /^\.header-title h2 \{$[^}]*\}/m.exec(shared)?.[0];
    expect(sectionHeading).toBeDefined();
    expect(sectionHeading).toContain('font-size: var(--type-section-size);');
    expect(sectionHeading).toContain('font-weight: var(--type-section-weight);');
    expect(sectionHeading).toContain('line-height: var(--type-section-line-height);');
    expect(sectionHeading).not.toMatch(/font-size:\s*\d/);

    // Bead enhancedchannelmanager-tygwm: the route title was `font-size:
    // 1.5rem` here, frozen outside the scale. It is now the page-title role
    // (20px/700/1.3) — asserted through the token names, so a future value
    // change lands in index.css alone and this test does not have to move.
    const routeTitle = /^\.page-header \.header-title h1 \{$[^}]*\}/m
      .exec(readFileSync(resolve(process.cwd(), 'src/components/PageHeader.css'), 'utf8'))?.[0];
    expect(routeTitle).toBeDefined();
    expect(routeTitle).toContain('font-size: var(--type-page-title-size);');
    expect(routeTitle).toContain('font-weight: var(--type-page-title-weight);');
    expect(routeTitle).toContain('line-height: var(--type-page-title-line-height);');
    expect(routeTitle).not.toMatch(/font-size:\s*\d/);
  });

  // The role's numbers live in index.css, so pin them there rather than
  // leaving "20px" asserted nowhere. --text-3xl is the shared 20px primitive
  // that the metric role already consumes.
  it('presets the page-title role at 20px/700/1.3', () => {
    const tokens = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');
    expect(tokens).toContain('--text-3xl: 1.25rem;');
    expect(tokens).toContain('--type-page-title-size: var(--text-3xl);');
    expect(tokens).toContain('--type-page-title-weight: 700;');
    expect(tokens).toContain('--type-page-title-line-height: 1.3;');
  });

  // Bead enhancedchannelmanager-tygwm. The meta row is a full-width flex line,
  // so an empty one still costs the header row-gap plus its own margin-top.
  // Every route renders the status outlet (App.tsx portals into it), so the
  // collapse can only be decided in CSS, against the outlet being :empty.
  it('collapses the meta row when the status outlet is empty and there are no related links', () => {
    const header = readFileSync(resolve(process.cwd(), 'src/components/PageHeader.css'), 'utf8');
    const collapse = /^\.page-header-meta:has\([^{]*\{[^}]*\}/m.exec(header)?.[0];
    expect(collapse).toBeDefined();
    expect(collapse).toContain('.route-page-status-outlet:empty');
    expect(collapse).toContain(':not(:has(> .page-header-related-links))');
    expect(collapse).toContain('display: none;');
  });

  // Bead enhancedchannelmanager-hmr0e. tygwm's collapse was scoped to stand down
  // wherever the row still held a related link, and M3U Manager was the one route
  // keeping one — so the route the collapse was written for was the one route it
  // never reached. Rather than re-assert the selector text, compose the header the
  // way App.tsx composes it per route (status is the portal outlet div; the links
  // come from ROUTE_HIERARCHY) and run the stylesheet's own selector against the
  // rendered DOM. That fails if either the route data or the selector moves, and
  // Channel Manager was carried alongside as the negative, because it still had a
  // link. Bead enhancedchannelmanager-mer2o removed that link at the PO's request,
  // so its row now collapses too and it is a second positive case.
  //
  // THAT LEAVES NO NEGATIVE CASE, and the gap is deliberate rather than
  // overlooked: `#settings/channel-pipeline` and `#settings/m3u-digest` are the
  // only surviving related links, and both belong to routes whose meta row also
  // carries other occupants, so neither exercises "a link alone keeps the row
  // standing". A route that reintroduces a lone related link should be added here
  // as the negative — until then this asserts only that the selector matches an
  // empty row, not that it declines to match a populated one.
  it.each([
    ['m3u-manager', true],
    ['channel-manager', true],
  ] as const)('collapses the empty %s meta row: %s', (tab, collapses) => {
    const header = readFileSync(resolve(process.cwd(), 'src/components/PageHeader.css'), 'utf8');
    const selector = /^(\.page-header-meta:has\([^{]*?)\s*\{/m.exec(header)?.[1];
    expect(selector).toBeDefined();

    const { container } = render(
      <PageHeader
        title={tab}
        status={<div className="route-page-status-outlet" />}
        relatedLinks={ROUTE_HIERARCHY[tab].settingsLinks}
      />,
    );

    expect(container.querySelector('.page-header-meta')).not.toBeNull();
    expect(container.querySelector(selector as string) !== null).toBe(collapses);
  });

  // Bead enhancedchannelmanager-7dxx0. A PageHeader carrying only a heading
  // is the label for the list underneath it, so it sits closer to that list
  // than to whatever is above — the default 1.5rem is sized for a header with
  // copy and a toolbar in it. Pinned here, in the shared rule, because a
  // per-tab margin is exactly the drift this sweep removes.
  it('tightens a heading-only header onto the list it labels', () => {
    const header = readFileSync(resolve(process.cwd(), 'src/components/PageHeader.css'), 'utf8');
    const bare = /^\.page-header\.page-header-heading-only \{$[^}]*\}/m.exec(header)?.[0];
    expect(bare).toBeDefined();
    expect(bare).toContain('margin-bottom: 0.5rem;');
  });

  // Bead enhancedchannelmanager-sl7dx. The route header is a chrome band: it
  // is the only `.page-header` that sets its own padding, and at `1rem` it
  // spent 32px of block padding around 45.5px of text. The app's other band —
  // the 45px top bar — is built from `--header-band-padding-block: 0.5rem`
  // around a 28px control, so the route header now sits exactly one step of
  // the spacing scale above the chrome idiom (0.75rem) and drops to the chrome
  // value itself at the breakpoint written to recover working height.
  it('pads the route header band one step above the chrome band, not a bare 1rem', () => {
    const app = readFileSync(resolve(process.cwd(), 'src/App.css'), 'utf8');
    const band = /^\.main > \.route-page-header \{$[^}]*\}/m.exec(app)?.[0];
    expect(band).toBeDefined();
    expect(band).toContain('padding: 0.75rem 1.5rem;');

    const compact = /^@media \(max-width: 1280px\), \(max-height: 800px\) \{[\s\S]*?^\}/m.exec(app)?.[0];
    expect(compact).toBeDefined();
    expect(compact).toMatch(/\.main > \.route-page-header \{\s*padding: 0\.5rem 1rem;/);
  });

  // Bead enhancedchannelmanager-sl7dx. A wrapped header line (the actions
  // cluster, a controls toolbar, the meta row) is separated by the header's
  // own `row-gap` and by nothing else. The meta row used to add a 0.25rem
  // `margin-top` on top of that gap, so its separation was 16px where every
  // other wrapped line got 12px — and the compact breakpoint then had to
  // restate both values to undo the compounding. One gap, one place.
  it('spaces wrapped header lines with one row-gap and no compounding meta margin', () => {
    const header = readFileSync(resolve(process.cwd(), 'src/components/PageHeader.css'), 'utf8');

    const row = /^\.page-header \{$[^}]*\}/m.exec(header)?.[0];
    expect(row).toBeDefined();
    expect(row).toContain('row-gap: 0.5rem;');

    const meta = /^\.page-header-meta \{$[^}]*\}/m.exec(header)?.[0];
    expect(meta).toBeDefined();
    expect(meta).not.toMatch(/margin-top/);

    const compact = /^@media \(max-width: 1280px\), \(max-height: 800px\) \{[\s\S]*?^\}/m.exec(header)?.[0];
    expect(compact).toBeDefined();
    expect(compact).not.toMatch(/\.route-page-header\.page-header \{/);
    expect(compact).not.toMatch(/margin-top/);
  });

  it('renders a heading-only header with no description, actions or meta row', () => {
    const { container } = render(<PageHeader className="page-header-heading-only" title="EPG Sources" />);

    expect(screen.getByRole('heading', { level: 2, name: 'EPG Sources' })).toBeInTheDocument();
    expect(container.querySelector('.header-description')).toBeNull();
    expect(container.querySelector('.header-actions')).toBeNull();
    expect(container.querySelector('.page-header-meta')).toBeNull();
  });
});
