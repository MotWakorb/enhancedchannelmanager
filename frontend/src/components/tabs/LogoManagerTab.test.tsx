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
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { LogoManagerTab } from './LogoManagerTab';
import { NotificationProvider } from '../../contexts/NotificationContext';
import * as api from '../../services/api';
import type { Logo, PaginatedResponse } from '../../types';
import { HttpError } from '../../services/httpClient';

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
    vi.resetAllMocks();
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

  it('names delete confirmation and blocks Close, Cancel, and Escape while delete is pending', async () => {
    vi.mocked(api.deleteLogo).mockReturnValue(new Promise(() => {}));
    await renderAndSettle();
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete logo' })[0]);
    const dialog = screen.getByRole('dialog', { name: 'Delete Logo' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled());
    expect(within(dialog).getByRole('button', { name: 'Close' })).toBeDisabled();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(dialog).toBeInTheDocument();
  });

  it('loads the first page with the default sort (name, ascending)', async () => {
    await renderAndSettle();

    expect(api.getLogos).toHaveBeenCalledWith(
      expect.objectContaining({ page: 1, sortBy: 'name', sortOrder: 'asc', unusedOnly: false }),
    );
    expect(screen.getByText('ESPN')).toBeInTheDocument();
    expect(screen.getByText('Fox News')).toBeInTheDocument();
  });

  it('recovers from a transient failure through the scoped Retry action', async () => {
    vi.mocked(api.getLogos)
      .mockRejectedValueOnce(new Error('Network down'))
      .mockResolvedValueOnce(pageResponse([makeLogo({ name: 'Recovered Logo' })]));

    renderWithProviders(<LogoManagerTab />);

    expect(await screen.findByRole('status', { name: 'Logos unavailable' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Retry loading logos' }));

    expect(await screen.findByText('Recovered Logo')).toBeVisible();
    expect(screen.getByText('1 total logos')).toBeVisible();
    expect(api.getLogos).toHaveBeenCalledTimes(2);
  });

  it('removes cached protected content when a later request is forbidden', async () => {
    vi.mocked(api.getLogos)
      .mockResolvedValueOnce(pageResponse([makeLogo({ name: 'Private Logo' })]))
      .mockRejectedValueOnce(new HttpError('Forbidden', 403));

    await renderAndSettle();
    expect(screen.getByText('Private Logo')).toBeVisible();

    fireEvent.change(screen.getByPlaceholderText('Search logos...'), {
      target: { value: 'private' },
    });

    expect(await screen.findByRole('status', { name: 'Logos access denied' })).toBeVisible();
    expect(screen.queryByText('Private Logo')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add Logo/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Retry loading/i })).not.toBeInTheDocument();
  });

  it('keeps cached content visible but marks it stale after a transient refresh failure', async () => {
    vi.mocked(api.getLogos)
      .mockResolvedValueOnce(pageResponse([makeLogo({ name: 'Cached Logo' })]))
      .mockRejectedValueOnce(new Error('Network down'));

    await renderAndSettle();
    fireEvent.change(screen.getByPlaceholderText('Search logos...'), {
      target: { value: 'cached' },
    });

    expect(await screen.findByText('Logos unavailable — showing previously loaded data')).toBeVisible();
    expect(screen.getByText('Cached Logo')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Retry loading logos' })).toBeVisible();
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

  it('uses native sort buttons and exposes the active sort direction', async () => {
    await renderAndSettle();
    const nameButton = screen.getByRole('button', { name: /Sort by Name, currently ascending/ });

    fireEvent.click(nameButton);

    await waitFor(() => {
      expect(api.getLogos).toHaveBeenLastCalledWith(
        expect.objectContaining({ sortBy: 'name', sortOrder: 'desc' }),
      );
    });
    expect(screen.getByRole('button', { name: /Sort by Name, currently descending/ })).toBeInTheDocument();
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
