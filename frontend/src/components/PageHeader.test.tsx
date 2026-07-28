import { createRef } from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PageHeader } from './PageHeader';
import { isPlainPrimaryActivation } from './routeHierarchy';

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
  it('orders primary action, controls, then a meta row of status and contextual links', () => {
    render(
      <PageHeader
        title="M3U Manager"
        actions={<button type="button">Add M3U Account</button>}
        status={<span>Refreshing provider data…</span>}
        controls={<label>View <select><option>All accounts</option></select></label>}
        relatedLinks={[{ href: '#settings/linked-accounts', label: 'Linked account settings' }]}
      />,
    );
    const action = screen.getByRole('button', { name: 'Add M3U Account' });
    const status = screen.getByText('Refreshing provider data…');
    const controls = screen.getByRole('combobox', { name: 'View' });
    const link = screen.getByRole('link', { name: 'Linked account settings' });
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

  it('renders a heading-only header with no description, actions or meta row', () => {
    const { container } = render(<PageHeader className="page-header-heading-only" title="EPG Sources" />);

    expect(screen.getByRole('heading', { level: 2, name: 'EPG Sources' })).toBeInTheDocument();
    expect(container.querySelector('.header-description')).toBeNull();
    expect(container.querySelector('.header-actions')).toBeNull();
    expect(container.querySelector('.page-header-meta')).toBeNull();
  });
});
