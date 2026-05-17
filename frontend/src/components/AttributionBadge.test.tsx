/**
 * Tests for <AttributionBadge> (bd-r5f0c.5 / W5).
 *
 * Contracts under test:
 *   - Each source variant renders the correct icon name.
 *   - Each source variant renders the correct "via <Source>" text.
 *   - Icon span has an aria-label of "Attributed via <Source>".
 *   - The outer span carries the source-specific CSS modifier class.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AttributionBadge } from './AttributionBadge';

describe('AttributionBadge', () => {
  // --- Emby variant ---

  it('renders live_tv icon for emby source', () => {
    const { container } = render(<AttributionBadge source="emby" />);
    const icon = container.querySelector('.attribution-badge__icon');
    expect(icon?.textContent).toBe('live_tv');
  });

  it('renders "via Emby" text for emby source', () => {
    render(<AttributionBadge source="emby" />);
    expect(screen.getByText('via Emby')).toBeInTheDocument();
  });

  it('has aria-label "Attributed via Emby" on the icon span for emby source', () => {
    const { container } = render(<AttributionBadge source="emby" />);
    const icon = container.querySelector('.attribution-badge__icon');
    expect(icon?.getAttribute('aria-label')).toBe('Attributed via Emby');
  });

  it('applies emby CSS modifier class for emby source', () => {
    const { container } = render(<AttributionBadge source="emby" />);
    expect(container.querySelector('.attribution-badge--emby')).toBeInTheDocument();
  });

  // --- Plex variant ---

  it('renders smart_display icon for plex source', () => {
    const { container } = render(<AttributionBadge source="plex" />);
    const icon = container.querySelector('.attribution-badge__icon');
    expect(icon?.textContent).toBe('smart_display');
  });

  it('renders "via Plex" text for plex source', () => {
    render(<AttributionBadge source="plex" />);
    expect(screen.getByText('via Plex')).toBeInTheDocument();
  });

  it('has aria-label "Attributed via Plex" on the icon span for plex source', () => {
    const { container } = render(<AttributionBadge source="plex" />);
    const icon = container.querySelector('.attribution-badge__icon');
    expect(icon?.getAttribute('aria-label')).toBe('Attributed via Plex');
  });

  it('applies plex CSS modifier class for plex source', () => {
    const { container } = render(<AttributionBadge source="plex" />);
    expect(container.querySelector('.attribution-badge--plex')).toBeInTheDocument();
  });

  // --- Jellyfin variant ---

  it('renders play_circle icon for jellyfin source', () => {
    const { container } = render(<AttributionBadge source="jellyfin" />);
    const icon = container.querySelector('.attribution-badge__icon');
    expect(icon?.textContent).toBe('play_circle');
  });

  it('renders "via Jellyfin" text for jellyfin source', () => {
    render(<AttributionBadge source="jellyfin" />);
    expect(screen.getByText('via Jellyfin')).toBeInTheDocument();
  });

  it('has aria-label "Attributed via Jellyfin" on the icon span for jellyfin source', () => {
    const { container } = render(<AttributionBadge source="jellyfin" />);
    const icon = container.querySelector('.attribution-badge__icon');
    expect(icon?.getAttribute('aria-label')).toBe('Attributed via Jellyfin');
  });

  it('applies jellyfin CSS modifier class for jellyfin source', () => {
    const { container } = render(<AttributionBadge source="jellyfin" />);
    expect(container.querySelector('.attribution-badge--jellyfin')).toBeInTheDocument();
  });

  // --- Extra className pass-through ---

  it('appends additional className to the outer span', () => {
    const { container } = render(<AttributionBadge source="emby" className="my-extra-class" />);
    expect(container.querySelector('.my-extra-class')).toBeInTheDocument();
  });
});
