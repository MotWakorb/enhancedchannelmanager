import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TabNavigation } from './TabNavigation';

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
    expect(within(nav).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      'Dashboard', 'Channel Manager', 'Guide', 'M3U Manager', 'EPG Manager',
      'Logo Manager', 'Channel Pipeline', 'M3U Changes', 'Stats', 'Journal', 'Settings',
    ]);
    expect(within(nav).getByRole('button', { name: 'Stats' })).toHaveAttribute('aria-current', 'page');
  });

  it('activates destinations by pointer and keyboard', async () => {
    const onTabChange = vi.fn();
    render(<TabNavigation activeTab="channel-manager" onTabChange={onTabChange} />);
    const guide = screen.getByRole('button', { name: 'Guide' });

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
    expect(screen.getByRole('button', { name: 'Guide' })).toHaveAttribute('title', 'Guide');
    expect(screen.getByRole('button', { name: 'Expand navigation' })).toHaveAttribute('aria-expanded', 'false');

    unmount();
    const second = render(<TabNavigation activeTab="channel-manager" onTabChange={vi.fn()} />);
    expect(second.container.querySelector('.primary-sidebar')).toHaveClass('is-collapsed');
    await userEvent.click(screen.getByRole('button', { name: 'Expand navigation' }));
    expect(second.container.querySelector('.primary-sidebar')).not.toHaveClass('is-collapsed');
    expect(localStorage.getItem('ecm.navigation.collapsed')).toBe('false');
  });

  it('keeps all destinations disabled with explanatory names during guarded work', () => {
    render(<TabNavigation activeTab="channel-manager" onTabChange={vi.fn()} disabled />);
    for (const button of screen.getByRole('navigation', { name: 'Primary' }).querySelectorAll('button')) {
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute('title');
    }
  });

  it('does not move focus when toggled', () => {
    render(<TabNavigation activeTab="channel-manager" onTabChange={vi.fn()} />);
    const collapse = screen.getByRole('button', { name: 'Collapse navigation' });
    collapse.focus();
    fireEvent.click(collapse);
    expect(collapse).toHaveFocus();
  });
});
