/**
 * Unit tests for ProviderStreamUsagePanel (GH-482, bd-n5cwp).
 *
 * Covers:
 *   - Renders the table from GET /api/stats/providers/stream-usage
 *   - Empty state has aria-live announce
 *   - Error state surfaces the message
 *   - Column click toggles sort (ascending/descending) via aria-sort
 *   - Unknown-provider bucket (provider_id: null) renders with a distinct
 *     class, same convention as the Live Stats Unknown bucket
 *
 * Not admin-gated (unlike UserStatsPanel/ProvidersPanel) — no useAuth mock
 * needed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ProviderStreamUsagePanel } from './ProviderStreamUsagePanel';
import * as api from '../../services/api';
import type { ProviderStreamUsageResponse } from '../../types';

vi.mock('../../services/api');

function buildResponse(
  rows: ProviderStreamUsageResponse['data'],
): ProviderStreamUsageResponse {
  return { data: rows, meta: { total_rows: rows.length }, pagination: null };
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ProviderStreamUsagePanel', () => {
  it('renders a row per provider with all four metrics', async () => {
    vi.mocked(api.getProviderStreamUsage).mockResolvedValue(
      buildResponse([
        {
          provider_id: 1,
          provider_name: 'Provider A',
          total_streams: 100,
          assigned_streams: 40,
          total_assignments: 55,
          utilization_pct: 40.0,
        },
        {
          provider_id: 2,
          provider_name: 'Provider B',
          total_streams: 50,
          assigned_streams: 9,
          total_assignments: 12,
          utilization_pct: 18.0,
        },
      ]),
    );

    render(<ProviderStreamUsagePanel />);

    await waitFor(() => {
      expect(screen.getByText('Provider A')).toBeInTheDocument();
    });

    const rowA = screen.getByText('Provider A').closest('tr') as HTMLElement;
    expect(within(rowA).getByText('40')).toBeInTheDocument();
    expect(within(rowA).getByText('55')).toBeInTheDocument();
    expect(within(rowA).getByText('100')).toBeInTheDocument();
    expect(within(rowA).getByText('40.0%')).toBeInTheDocument();

    const rowB = screen.getByText('Provider B').closest('tr') as HTMLElement;
    expect(within(rowB).getByText('9')).toBeInTheDocument();
    expect(within(rowB).getByText('12')).toBeInTheDocument();
    expect(within(rowB).getByText('50')).toBeInTheDocument();
    expect(within(rowB).getByText('18.0%')).toBeInTheDocument();
  });

  it('shows an aria-live empty state when there are no providers', async () => {
    vi.mocked(api.getProviderStreamUsage).mockResolvedValue(buildResponse([]));

    render(<ProviderStreamUsagePanel />);

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/no provider stream data/i);
    });
  });

  it('surfaces a fetch error', async () => {
    vi.mocked(api.getProviderStreamUsage).mockRejectedValue(new Error('boom'));

    render(<ProviderStreamUsagePanel />);

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('boom');
    });
  });

  it('renders the Unknown provider bucket distinctly', async () => {
    vi.mocked(api.getProviderStreamUsage).mockResolvedValue(
      buildResponse([
        {
          provider_id: null,
          provider_name: 'Unknown',
          total_streams: 0,
          assigned_streams: 3,
          total_assignments: 3,
          utilization_pct: 0.0,
        },
      ]),
    );

    const { container } = render(<ProviderStreamUsagePanel />);

    await waitFor(() => {
      expect(screen.getByText('Unknown')).toBeInTheDocument();
    });
    expect(container.querySelector('tr.unknown-bucket')).toBeInTheDocument();
  });

  it('sorts by a clicked column and flips direction on a second click', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getProviderStreamUsage).mockResolvedValue(
      buildResponse([
        { provider_id: 1, provider_name: 'Zebra', total_streams: 10, assigned_streams: 5, total_assignments: 5, utilization_pct: 50 },
        { provider_id: 2, provider_name: 'Alpha', total_streams: 10, assigned_streams: 2, total_assignments: 2, utilization_pct: 20 },
      ]),
    );

    render(<ProviderStreamUsagePanel />);

    await waitFor(() => {
      expect(screen.getByText('Zebra')).toBeInTheDocument();
    });

    // Default sort: assigned_streams desc -> Zebra (5) before Alpha (2).
    let rows = screen.getAllByRole('row').slice(1); // drop header row
    expect(within(rows[0]).getByText('Zebra')).toBeInTheDocument();

    // Click the "Provider" column header to sort by name ascending.
    await user.click(screen.getByRole('button', { name: /sort by provider$/i }));

    rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('Alpha')).toBeInTheDocument();
    const providerHeader = screen.getByRole('columnheader', { name: /provider/i });
    expect(providerHeader).toHaveAttribute('aria-sort', 'ascending');

    // Click again to flip to descending.
    await user.click(screen.getByRole('button', { name: /sort by provider$/i }));
    rows = screen.getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('Zebra')).toBeInTheDocument();
    expect(providerHeader).toHaveAttribute('aria-sort', 'descending');
  });
});
