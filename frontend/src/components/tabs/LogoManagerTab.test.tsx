/**
 * Unit tests for LogoManagerTab — sortable columns + unused-only filter
 * (bead enhancedchannelmanager-09x38.13).
 *
 * The tab was rewritten from a client-side-load-everything-then-slice model
 * to true per-page server-side sort/filter/pagination (see
 * services/api.ts getLogos() and backend/routers/channels.py get_logos()).
 * These tests lock the wiring: header clicks flip sortBy/sortOrder and
 * re-fetch, the unused-only toggle re-fetches with unusedOnly=true, and
 * both compose with search + pagination.
 */
import type * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { LogoManagerTab } from './LogoManagerTab';
import { NotificationProvider } from '../../contexts/NotificationContext';
import * as api from '../../services/api';
import type { Logo, PaginatedResponse } from '../../types';

vi.mock('../../services/api');

const renderWithProviders = (ui: React.JSX.Element) =>
  render(<NotificationProvider>{ui}</NotificationProvider>);

function makeLogo(overrides: Partial<Logo>): Logo {
  return {
    id: 1,
    name: 'ESPN',
    url: 'http://example/espn.png',
    cache_url: '',
    channel_count: 0,
    is_used: false,
    ...overrides,
  };
}

function pageResponse(results: Logo[], count = results.length): PaginatedResponse<Logo> {
  return { results, count, next: null, previous: null };
}

describe('LogoManagerTab', () => {
  const mockLogos: Logo[] = [
    makeLogo({ id: 1, name: 'ESPN', channel_count: 3 }),
    makeLogo({ id: 2, name: 'Fox News', channel_count: 0 }),
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getLogos).mockResolvedValue(pageResponse(mockLogos, 2));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function renderAndSettle() {
    renderWithProviders(<LogoManagerTab />);
    await waitFor(() => {
      expect(screen.queryByText('Loading logos...')).not.toBeInTheDocument();
    });
  }

  it('loads the first page with the default sort (name, ascending)', async () => {
    await renderAndSettle();

    expect(api.getLogos).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, sortBy: 'name', sortOrder: 'asc', unusedOnly: false }),
    );
    expect(screen.getByText('ESPN')).toBeInTheDocument();
    expect(screen.getByText('Fox News')).toBeInTheDocument();
  });

  it('clicking the Name header toggles sort order and re-fetches', async () => {
    await renderAndSettle();

    fireEvent.click(screen.getByText('Name'));

    await waitFor(() => {
      expect(api.getLogos).toHaveBeenLastCalledWith(
        expect.objectContaining({ sortBy: 'name', sortOrder: 'desc' }),
      );
    });

    // Clicking again flips back to ascending
    fireEvent.click(screen.getByText('Name'));
    await waitFor(() => {
      expect(api.getLogos).toHaveBeenLastCalledWith(
        expect.objectContaining({ sortBy: 'name', sortOrder: 'asc' }),
      );
    });
  });

  it('clicking the Used By header sorts by channel_count, defaulting to ascending', async () => {
    await renderAndSettle();

    fireEvent.click(screen.getByText('Used By'));

    await waitFor(() => {
      expect(api.getLogos).toHaveBeenLastCalledWith(
        expect.objectContaining({ sortBy: 'channel_count', sortOrder: 'asc' }),
      );
    });
  });

  it('switching sort column resets to page 1', async () => {
    vi.mocked(api.getLogos).mockResolvedValue(pageResponse(mockLogos, 200));
    await renderAndSettle();

    // Move to page 2
    fireEvent.click(screen.getByLabelText('Next page'));
    await waitFor(() => {
      expect(api.getLogos).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }));
    });

    // Now switch sort column — should reset back to page 1
    fireEvent.click(screen.getByText('Used By'));
    await waitFor(() => {
      expect(api.getLogos).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 1, sortBy: 'channel_count' }),
      );
    });
  });

  it('toggling "Unused only" re-fetches with unusedOnly=true, then back off', async () => {
    await renderAndSettle();

    const toggle = screen.getByLabelText('Show only unused logos');
    fireEvent.click(toggle);

    await waitFor(() => {
      expect(api.getLogos).toHaveBeenLastCalledWith(
        expect.objectContaining({ unusedOnly: true, page: 1 }),
      );
    });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(toggle);
    await waitFor(() => {
      expect(api.getLogos).toHaveBeenLastCalledWith(
        expect.objectContaining({ unusedOnly: false }),
      );
    });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
  });

  it('composes the unused-only filter with an active search term', async () => {
    await renderAndSettle();

    fireEvent.change(screen.getByPlaceholderText('Search logos...'), {
      target: { value: 'fox' },
    });
    await waitFor(() => {
      expect(api.getLogos).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: 'fox' }),
      );
    });

    fireEvent.click(screen.getByLabelText('Show only unused logos'));
    await waitFor(() => {
      expect(api.getLogos).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: 'fox', unusedOnly: true }),
      );
    });
  });

  it('keeps sort/filter state applied across the list/grid view toggle', async () => {
    await renderAndSettle();

    fireEvent.click(screen.getByLabelText('Show only unused logos'));
    await waitFor(() => {
      expect(api.getLogos).toHaveBeenLastCalledWith(expect.objectContaining({ unusedOnly: true }));
    });

    fireEvent.click(screen.getByLabelText('Grid view'));
    // Switching views is purely a render-mode change — it must not re-issue
    // a fetch that drops the active filter.
    expect(api.getLogos).toHaveBeenLastCalledWith(expect.objectContaining({ unusedOnly: true }));
    expect(screen.getByText('ESPN')).toBeInTheDocument();
  });

  it('renders the empty state distinctly when filters/search yield zero matches', async () => {
    vi.mocked(api.getLogos).mockResolvedValue(pageResponse([], 0));
    await renderAndSettle();

    fireEvent.click(screen.getByLabelText('Show only unused logos'));
    await waitFor(() => {
      expect(screen.getByText('No logos found')).toBeInTheDocument();
    });
  });
});
