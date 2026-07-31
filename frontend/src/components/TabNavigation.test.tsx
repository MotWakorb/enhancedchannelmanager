import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TabNavigation } from './TabNavigation';

function middleClick(element: Element): boolean {
  return element.dispatchEvent(new MouseEvent('auxclick', { bubbles: true, cancelable: true, button: 1 }));
}

describe('grouped primary navigation', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders the approved groups and destinations exactly once in order', () => {
    render(<TabNavigation activeTab="stats" onTabChange={vi.fn()} />);

    const nav = screen.getByRole('navigation', { name: 'Primary' });
    expect(within(nav).getAllByRole('heading').map((heading) => heading.textContent)).toEqual([
      'Overview', 'Operations', 'Automation', 'Insights', 'System',
    ]);
    expect(within(nav).getAllByRole('link').map((link) => link.getAttribute('aria-label'))).toEqual([
      'Dashboard', 'M3U Manager', 'EPG Manager', 'Logo Manager', 'Channel Manager',
      'Channel Pipeline', 'Guide', 'Stats', 'M3U Changes', 'Journal', 'Settings',
    ]);
    expect(within(nav).getByRole('link', { name: 'Stats' })).toHaveAttribute('aria-current', 'page');
    expect(within(nav).getByRole('link', { name: 'Guide' })).toHaveAttribute('href', '#guide');
  });

  it('activates destinations by pointer and keyboard', async () => {
    const onTabChange = vi.fn();
    render(<TabNavigation activeTab="channel-manager" onTabChange={onTabChange} />);
    const guide = screen.getByRole('link', { name: 'Guide' });

    await userEvent.click(guide);
    guide.focus();
    await userEvent.keyboard('{Enter}');

    expect(onTabChange).toHaveBeenNthCalledWith(1, 'guide');
    expect(onTabChange).toHaveBeenNthCalledWith(2, 'guide');
  });

  it('collapses to named icon controls, persists, and restores the full labels', async () => {
    const { container, unmount } = render(<TabNavigation activeTab="channel-manager" onTabChange={vi.fn()} />);
    const sidebar = container.querySelector('.primary-sidebar')!;

    expect(sidebar).not.toHaveClass('is-collapsed');
    await userEvent.click(screen.getByRole('button', { name: 'Collapse navigation' }));
    expect(sidebar).toHaveClass('is-collapsed');
    expect(localStorage.getItem('ecm.navigation.collapsed')).toBe('true');
    expect(screen.getByRole('link', { name: 'Guide' })).toHaveAttribute('title', 'Guide');
    expect(screen.getByRole('button', { name: 'Expand navigation' })).toHaveAttribute('aria-expanded', 'false');

    unmount();
    const second = render(<TabNavigation activeTab="channel-manager" onTabChange={vi.fn()} />);
    expect(second.container.querySelector('.primary-sidebar')).toHaveClass('is-collapsed');
    await userEvent.click(screen.getByRole('button', { name: 'Expand navigation' }));
    expect(second.container.querySelector('.primary-sidebar')).not.toHaveClass('is-collapsed');
    expect(localStorage.getItem('ecm.navigation.collapsed')).toBe('false');
  });

  it('makes the brand logo the collapse control and keeps no separate collapse button', async () => {
    const { container } = render(<TabNavigation activeTab="channel-manager" onTabChange={vi.fn()} />);
    const sidebar = container.querySelector('.primary-sidebar')!;

    const logo = screen.getByRole('button', { name: 'Collapse navigation' });
    expect(logo).toHaveClass('sidebar-brand-toggle');
    expect(logo).toHaveAttribute('aria-expanded', 'true');
    expect(container.querySelector('.navigation-collapse')).toBeNull();

    await userEvent.click(logo);
    expect(sidebar).toHaveClass('is-collapsed');

    const collapsedLogo = screen.getByRole('button', { name: 'Expand navigation' });
    expect(collapsedLogo).toHaveClass('sidebar-brand-toggle');
    expect(collapsedLogo).toHaveAttribute('aria-expanded', 'false');
    // The sidebar's only button is the logo toggle.
    expect(container.querySelectorAll('button')).toHaveLength(1);
  });

  // Playwright's getByRole name option matches substrings, so any other control
  // whose accessible name contains the Collapse/Expand phrase makes every e2e
  // query for the sidebar collapse toggle resolve to two elements.
  it('gives exactly one control an accessible name containing the collapse phrase', async () => {
    const { container } = render(<TabNavigation activeTab="channel-manager" onTabChange={vi.fn()} />);
    const namesContaining = (phrase: string) =>
      [...container.querySelectorAll('button')]
        .map((button) => button.getAttribute('aria-label') ?? button.textContent ?? '')
        .filter((name) => name.toLowerCase().includes(phrase));

    expect(namesContaining('collapse navigation')).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: 'Collapse navigation' }));
    expect(namesContaining('expand navigation')).toHaveLength(1);
  });

  describe('Settings drill-in', () => {
    it('replaces the groups with Settings sections when Settings is selected', async () => {
      const onTabChange = vi.fn();
      const { rerender } = render(
        <TabNavigation activeTab="channel-manager" onTabChange={onTabChange} />,
      );
      expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument();

      await userEvent.click(screen.getByRole('link', { name: 'Settings' }));
      expect(onTabChange).toHaveBeenCalledWith('settings');
      rerender(<TabNavigation activeTab="settings" onTabChange={onTabChange} />);

      const nav = screen.getByRole('navigation', { name: 'Settings sections' });
      expect(screen.queryByRole('navigation', { name: 'Primary' })).not.toBeInTheDocument();
      expect(within(nav).getByRole('link', { name: 'General' })).toHaveAttribute('href', '#settings/general');
      expect(within(nav).getByRole('button', { name: 'Back to main navigation' })).toBeInTheDocument();
    });

    it('restores the main groups on Back without leaving the Settings route', async () => {
      const onTabChange = vi.fn();
      render(<TabNavigation activeTab="settings" onTabChange={onTabChange} settingsPage="appearance" />);

      await userEvent.click(screen.getByRole('button', { name: 'Back to main navigation' }));

      expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument();
      expect(screen.queryByRole('navigation', { name: 'Settings sections' })).not.toBeInTheDocument();
      // Back is a sidebar-only affordance; it must not navigate.
      expect(onTabChange).not.toHaveBeenCalled();
    });

    it('reopens the sections when Settings is selected again after Back', async () => {
      render(<TabNavigation activeTab="settings" onTabChange={vi.fn()} settingsPage="general" />);

      await userEvent.click(screen.getByRole('button', { name: 'Back to main navigation' }));
      await userEvent.click(screen.getByRole('link', { name: 'Settings' }));

      expect(screen.getByRole('navigation', { name: 'Settings sections' })).toBeInTheDocument();
    });

    // The drill-in's counterpart to the primary nav's group assertion above.
    // The grouping is data (SETTINGS_SECTIONS.group) rendered through the same
    // `.navigation-group > h2` markup, so this pins what an operator actually
    // sees: six headings in the approved order, every destination under the
    // right one, and each link still a real `#settings/<page>` anchor.
    it('renders the approved Settings groups and destinations in order for an administrator', () => {
      render(<TabNavigation activeTab="settings" onTabChange={vi.fn()} settingsPage="general" isAdmin />);
      const nav = screen.getByRole('navigation', { name: 'Settings sections' });

      expect(within(nav).getAllByRole('heading').map((heading) => heading.textContent)).toEqual([
        'Connections', 'Channel Processing', 'Notifications & Reports', 'Upkeep', 'Workspace', 'Administration',
      ]);
      expect([...nav.querySelectorAll('.navigation-group')].map((group) => [
        group.querySelector('h2')!.textContent,
        [...group.querySelectorAll('a')].map((link) => link.getAttribute('aria-label')),
      ])).toEqual([
        ['Connections', ['General', 'Integrations']],
        ['Channel Processing', ['Channel Defaults', 'Channel Normalization', 'Tags', 'Channel Pipeline']],
        ['Notifications & Reports', ['Notification Settings', 'M3U Digest']],
        ['Upkeep', ['Scheduled Tasks', 'Maintenance', 'Backup & Restore']],
        ['Workspace', ['Appearance', 'Linked Accounts']],
        ['Administration', ['Authentication', 'User Management', 'TLS Certificates', 'MCP Integration']],
      ]);
      expect([...nav.querySelectorAll('a')].every((link) => link.getAttribute('href')?.startsWith('#settings/'))).toBe(true);
      expect(within(nav).getByRole('link', { name: 'General' })).toHaveAttribute('aria-current', 'page');
      // Back stays a sibling above the groups, keeping its explicit name.
      expect(within(nav).getByRole('button', { name: 'Back to main navigation' })).toBeInTheDocument();
    });

    // Administration is hidden by the empty-group filter, not by a branch on
    // `adminOnly` in the markup: a non-admin must see five headings and no
    // Administration heading over an empty list.
    it('drops the Administration group entirely for a non-administrator', () => {
      render(<TabNavigation activeTab="settings" onTabChange={vi.fn()} settingsPage="general" />);
      const nav = screen.getByRole('navigation', { name: 'Settings sections' });

      expect(within(nav).getAllByRole('heading').map((heading) => heading.textContent)).toEqual([
        'Connections', 'Channel Processing', 'Notifications & Reports', 'Upkeep', 'Workspace',
      ]);
      expect(within(nav).queryByRole('link', { name: 'User Management' })).not.toBeInTheDocument();
      expect([...nav.querySelectorAll('.navigation-group')].every((group) => group.querySelectorAll('a').length > 0)).toBe(true);
    });

    it('marks the active section and reports section changes', async () => {
      const onSettingsPageChange = vi.fn();
      render(
        <TabNavigation
          activeTab="settings"
          onTabChange={vi.fn()}
          settingsPage="appearance"
          onSettingsPageChange={onSettingsPageChange}
        />,
      );

      expect(screen.getByRole('link', { name: 'Appearance' })).toHaveAttribute('aria-current', 'page');
      expect(screen.getByRole('link', { name: 'General' })).not.toHaveAttribute('aria-current');

      await userEvent.click(screen.getByRole('link', { name: 'Maintenance' }));
      expect(onSettingsPageChange).toHaveBeenCalledWith('maintenance');
    });

    it('returns to the main groups when navigating away from Settings', () => {
      const { rerender } = render(<TabNavigation activeTab="settings" onTabChange={vi.fn()} />);
      expect(screen.getByRole('navigation', { name: 'Settings sections' })).toBeInTheDocument();

      rerender(<TabNavigation activeTab="stats" onTabChange={vi.fn()} />);
      expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument();
    });

    it('slides forward into the sections and back out, but not on first render', async () => {
      const onTabChange = vi.fn();
      const { container, rerender } = render(
        <TabNavigation activeTab="settings" onTabChange={onTabChange} />,
      );

      // A Settings deep link lands without animating.
      expect(container.querySelector('.primary-navigation')).not.toHaveClass('nav-slide-forward');
      expect(container.querySelector('.primary-navigation')).not.toHaveClass('nav-slide-back');

      await userEvent.click(screen.getByRole('button', { name: 'Back to main navigation' }));
      expect(container.querySelector('.primary-navigation')).toHaveClass('nav-slide-back');

      await userEvent.click(screen.getByRole('link', { name: 'Settings' }));
      expect(container.querySelector('.primary-navigation')).toHaveClass('nav-slide-forward');

      // Entering Settings from another route slides forward too.
      rerender(<TabNavigation activeTab="stats" onTabChange={onTabChange} />);
      rerender(<TabNavigation activeTab="settings" onTabChange={onTabChange} />);
      expect(container.querySelector('.primary-navigation')).toHaveClass('nav-slide-forward');
    });

    it('animates with transform only so mid-animation sampling cannot alter contrast', () => {
      // Guards the enhancedchannelmanager-eo7er lesson: an opacity-based
      // transition on nav text blends its computed colour while running, and
      // axe samples mid-animation. Read from disk — vitest stubs CSS imports,
      // so document.styleSheets would make this assertion vacuous.
      const css = readFileSync(resolve(process.cwd(), 'src/components/TabNavigation.css'), 'utf8');
      const keyframes = [...css.matchAll(/@keyframes\s+nav-slide-from-\w+\s*\{[^}]*\{[^}]*\}[^}]*\{[^}]*\}[^}]*\}/g)]
        .map((match) => match[0]);

      expect(keyframes).toHaveLength(2);
      for (const block of keyframes) {
        expect(block).toMatch(/transform/);
        expect(block).not.toMatch(/opacity/);
      }
    });

    it('keeps section links inert during guarded work', () => {
      const onSettingsPageChange = vi.fn();
      render(
        <TabNavigation
          activeTab="settings"
          onTabChange={vi.fn()}
          onSettingsPageChange={onSettingsPageChange}
          disabled
        />,
      );

      for (const link of screen.getByRole('navigation', { name: 'Settings sections' }).querySelectorAll('a')) {
        expect(link).toHaveAttribute('aria-disabled', 'true');
        expect(link).not.toHaveAttribute('href');
        expect(fireEvent.click(link)).toBe(false);
      }
      expect(onSettingsPageChange).not.toHaveBeenCalled();
    });
  });

  it('keeps all destinations disabled with explanatory names during guarded work', () => {
    const onTabChange = vi.fn();
    render(<TabNavigation activeTab="channel-manager" onTabChange={onTabChange} disabled />);
    for (const link of screen.getByRole('navigation', { name: 'Primary' }).querySelectorAll('a')) {
      expect(link).toHaveAttribute('aria-disabled', 'true');
      expect(link).not.toHaveAttribute('href');
      expect(link).toHaveAttribute('title');
      expect(fireEvent.click(link)).toBe(false);
      expect(fireEvent.click(link, { ctrlKey: true })).toBe(false);
      expect(middleClick(link)).toBe(false);
      expect(fireEvent.contextMenu(link)).toBe(false);
    }
    expect(onTabChange).not.toHaveBeenCalled();
  });

  it('intercepts only unmodified primary activation for enabled route links', () => {
    const onTabChange = vi.fn();
    render(<TabNavigation activeTab="channel-manager" onTabChange={onTabChange} />);
    const guide = screen.getByRole('link', { name: 'Guide' });

    expect(fireEvent.click(guide)).toBe(false);
    expect(fireEvent.click(guide, { ctrlKey: true })).toBe(true);
    expect(fireEvent.click(guide, { metaKey: true })).toBe(true);
    expect(fireEvent.click(guide, { shiftKey: true })).toBe(true);
    expect(middleClick(guide)).toBe(true);
    expect(onTabChange).toHaveBeenCalledTimes(1);
  });

  it('falls back to expanded navigation when localStorage is unavailable', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Storage unavailable');
    });
    const { container } = render(<TabNavigation activeTab="channel-manager" onTabChange={vi.fn()} />);
    expect(container.querySelector('.primary-sidebar')).not.toHaveClass('is-collapsed');
    getItem.mockRestore();
  });

  it('does not move focus when toggled', () => {
    render(<TabNavigation activeTab="channel-manager" onTabChange={vi.fn()} />);
    const collapse = screen.getByRole('button', { name: 'Collapse navigation' });
    collapse.focus();
    fireEvent.click(collapse);
    expect(collapse).toHaveFocus();
  });
});
