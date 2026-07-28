import { createRef } from 'react';
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
});
