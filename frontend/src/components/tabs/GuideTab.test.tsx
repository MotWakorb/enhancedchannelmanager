/**
 * Regression tests for GuideTab data loading (bead ogm9v).
 *
 * Bug: landing directly on the Guide tab left it stuck on "Loading guide
 * data..." forever. GuideTab's load effect depended on propChannels/propLogos,
 * which App streams in progressively (paginated) — so every prop update re-ran
 * the whole effect, including the expensive getEPGGrid() call, resetting
 * `loading` to true on each pass and never settling. Visiting Channel Manager
 * first (so props were stable before mount) was the only workaround.
 *
 * These tests lock in that:
 *   - Guide-specific data (the EPG grid) is fetched exactly once on mount, even
 *     as channel/logo props change afterward.
 *   - Channels supplied late via props still render (no refetch, just a sync).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { GuideTab } from './GuideTab';
import { NotificationProvider } from '../../contexts/NotificationContext';
import type { Channel, Logo } from '../../types';

vi.mock('../../services/api');

// PrintGuideModal is always mounted (closed); stub it out — it has its own suite.
vi.mock('../PrintGuideModal', () => ({
  PrintGuideModal: () => null,
}));

import * as api from '../../services/api';

const renderWithProviders = (ui: React.JSX.Element) =>
  render(<NotificationProvider>{ui}</NotificationProvider>);

function makeChannel(overrides: Partial<Channel> = {}): Channel {
  return {
    id: 1,
    name: 'Channel One',
    channel_number: 1,
    channel_group_id: null,
    logo_id: null,
    tvg_id: null,
    epg_data_id: null,
    uuid: 'uuid-1',
    ...overrides,
  } as unknown as Channel;
}

beforeEach(() => {
  vi.mocked(api.getEPGGrid).mockResolvedValue([]);
  vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
  vi.mocked(api.getChannelGroups).mockResolvedValue([]);
  vi.mocked(api.getChannels).mockResolvedValue({ results: [], next: null } as never);
  vi.mocked(api.getLogos).mockResolvedValue({ results: [], next: null } as never);
});

describe('GuideTab data loading', () => {
  it('fetches the EPG grid exactly once even as channel/logo props update', async () => {
    const { rerender } = renderWithProviders(
      <GuideTab channels={[]} logos={[]} />
    );

    // Mount fetch settles: the loading spinner clears.
    await waitFor(() =>
      expect(screen.queryByText('Loading guide data...')).not.toBeInTheDocument()
    );

    // Simulate App streaming channels/logos in after mount (paginated loads).
    rerender(
      <NotificationProvider>
        <GuideTab channels={[makeChannel()]} logos={[]} />
      </NotificationProvider>
    );
    rerender(
      <NotificationProvider>
        <GuideTab channels={[makeChannel(), makeChannel({ id: 2, name: 'Channel Two', channel_number: 2 })]} logos={[] as Logo[]} />
      </NotificationProvider>
    );

    // The expensive grid fetch must NOT be repeated on prop changes.
    await waitFor(() =>
      expect(screen.getByText(/2 channels/)).toBeInTheDocument()
    );
    expect(api.getEPGGrid).toHaveBeenCalledTimes(1);
  });

  it('renders channels supplied late via props without stalling', async () => {
    const { rerender } = renderWithProviders(
      <GuideTab channels={[]} logos={[]} />
    );

    await waitFor(() =>
      expect(screen.queryByText('Loading guide data...')).not.toBeInTheDocument()
    );
    // Starts empty (App has not finished loading channels yet).
    expect(screen.getByText(/0 channels/)).toBeInTheDocument();

    // App finishes loading and passes channels down.
    rerender(
      <NotificationProvider>
        <GuideTab channels={[makeChannel()]} logos={[]} />
      </NotificationProvider>
    );

    await waitFor(() =>
      expect(screen.getByText(/1 channels/)).toBeInTheDocument()
    );
  });
});
