/**
 * Unit tests for TabNavigation (bd-3thyn: overflow scroll affordance).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TabNavigation } from './TabNavigation';

// jsdom performs no real layout, so scrollWidth/clientWidth/scrollLeft
// default to 0 on every element. Stub them on the rendered <nav> so the
// overflow-detection logic in TabNavigation has something to measure.
function mockOverflow(nav: HTMLElement, { scrollWidth, clientWidth, scrollLeft }: {
  scrollWidth: number;
  clientWidth: number;
  scrollLeft: number;
}) {
  Object.defineProperty(nav, 'scrollWidth', { value: scrollWidth, configurable: true });
  Object.defineProperty(nav, 'clientWidth', { value: clientWidth, configurable: true });
  Object.defineProperty(nav, 'scrollLeft', { value: scrollLeft, configurable: true, writable: true });
}

describe('TabNavigation', () => {
  it('renders all tabs and marks the active one', () => {
    render(<TabNavigation activeTab="stats" onTabChange={vi.fn()} />);

    expect(screen.getByText('Settings')).toBeInTheDocument();
    expect(screen.getByText('M3U Manager')).toBeInTheDocument();

    const statsButton = screen.getByText('Stats').closest('button');
    expect(statsButton).toHaveClass('active');

    const settingsButton = screen.getByText('Settings').closest('button');
    expect(settingsButton).not.toHaveClass('active');
  });

  it('calls onTabChange with the clicked tab id', () => {
    const onTabChange = vi.fn();
    render(<TabNavigation activeTab="m3u-manager" onTabChange={onTabChange} />);

    fireEvent.click(screen.getByText('Settings').closest('button')!);

    expect(onTabChange).toHaveBeenCalledWith('settings');
  });

  it('disables every tab button when disabled is set', () => {
    render(<TabNavigation activeTab="m3u-manager" onTabChange={vi.fn()} disabled />);

    const settingsButton = screen.getByText('Settings').closest('button');
    expect(settingsButton).toBeDisabled();
  });

  it('does not render scroll chevrons when the strip fits (no overflow)', () => {
    const { container } = render(<TabNavigation activeTab="m3u-manager" onTabChange={vi.fn()} />);
    const nav = container.querySelector('.tab-navigation') as HTMLElement;
    mockOverflow(nav, { scrollWidth: 1000, clientWidth: 1000, scrollLeft: 0 });
    fireEvent.scroll(nav);

    expect(screen.queryByLabelText('Scroll tabs left')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Scroll tabs right')).not.toBeInTheDocument();
  });

  it('shows the right chevron (not left) when overflowing from the start', () => {
    const { container } = render(<TabNavigation activeTab="m3u-manager" onTabChange={vi.fn()} />);
    const nav = container.querySelector('.tab-navigation') as HTMLElement;
    mockOverflow(nav, { scrollWidth: 1207, clientWidth: 1024, scrollLeft: 0 });
    fireEvent.scroll(nav);

    expect(screen.queryByLabelText('Scroll tabs left')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Scroll tabs right')).toBeInTheDocument();
  });

  it('shows the left chevron once scrolled away from the start, and is keyboard-reachable', () => {
    const { container } = render(<TabNavigation activeTab="settings" onTabChange={vi.fn()} />);
    const nav = container.querySelector('.tab-navigation') as HTMLElement;
    mockOverflow(nav, { scrollWidth: 1207, clientWidth: 1024, scrollLeft: 150 });
    fireEvent.scroll(nav);

    const leftBtn = screen.getByLabelText('Scroll tabs left');
    expect(leftBtn.tagName).toBe('BUTTON');
    expect(leftBtn).not.toHaveAttribute('disabled');
  });

  it('scrolls the strip when a chevron is clicked', () => {
    const { container } = render(<TabNavigation activeTab="m3u-manager" onTabChange={vi.fn()} />);
    const nav = container.querySelector('.tab-navigation') as HTMLElement;
    mockOverflow(nav, { scrollWidth: 1207, clientWidth: 1024, scrollLeft: 0 });
    fireEvent.scroll(nav);

    const scrollBySpy = vi.fn();
    nav.scrollBy = scrollBySpy as unknown as typeof nav.scrollBy;

    fireEvent.click(screen.getByLabelText('Scroll tabs right'));

    expect(scrollBySpy).toHaveBeenCalledWith(expect.objectContaining({ left: expect.any(Number), behavior: 'smooth' }));
  });
});
