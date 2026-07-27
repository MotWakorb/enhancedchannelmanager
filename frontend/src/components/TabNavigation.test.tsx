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
      'Dashboard', 'Channel Manager', 'Guide', 'M3U Manager', 'EPG Manager',
      'Logo Manager', 'Channel Pipeline', 'M3U Changes', 'Stats', 'Journal', 'Settings',
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
