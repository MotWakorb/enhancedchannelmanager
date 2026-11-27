/**
 * Unit tests for M3UChangesTab component and helper functions.
 */
import type * as React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { M3UChangesTab } from './M3UChangesTab';
import { NotificationProvider } from '../../contexts/NotificationContext';
import * as api from '../../services/api';
import { HttpError } from '../../services/httpClient';
import type { M3UChangeLog, M3UChangeSummary, M3UAccount } from '../../types';

// Mock the API module
vi.mock('../../services/api');
const viewer = vi.hoisted(() => ({ canEdit: true }));
vi.mock('../../hooks/useAuth', () => ({ useAdminNavVisible: () => viewer.canEdit }));

const renderWithProviders = (ui: React.JSX.Element) =>
  render(<NotificationProvider>{ui}</NotificationProvider>);

// Helper function tests - extracted from the component for testing
// We test these through the component's rendered output

describe('M3UChangesTab', () => {
  // Mock data
  const mockAccounts: M3UAccount[] = [
    { id: 1, name: 'Test M3U 1', server_url: 'http://test1.m3u', is_active: true } as unknown as M3UAccount,
    { id: 2, name: 'Test M3U 2', server_url: 'http://test2.m3u', is_active: true } as unknown as M3UAccount,
  ];

  const mockSummary: M3UChangeSummary = {
    total_changes: 10,
    groups_added: 2,
    groups_removed: 1,
    streams_added: 100,
    streams_removed: 50,
    accounts_affected: [1, 2],
    since: '2026-01-29T00:00:00Z',
  };

  const mockChanges: M3UChangeLog[] = [
    {
      id: 1,
      m3u_account_id: 1,
      change_time: new Date().toISOString(),
      change_type: 'group_added',
      group_name: 'Sports',
      stream_names: [],
      count: 50,
      enabled: true,
      snapshot_id: 1,
    },
    {
      id: 2,
      m3u_account_id: 1,
      change_time: new Date().toISOString(),
      change_type: 'streams_added',
      group_name: 'Movies',
      stream_names: ['Movie 1', 'Movie 2', 'Movie 3'],
      count: 3,
      enabled: false,
      snapshot_id: 1,
    },
    {
      id: 3,
      m3u_account_id: 2,
      change_time: new Date().toISOString(),
      change_type: 'group_removed',
      group_name: 'Old Group',
      stream_names: [],
      count: 25,
      enabled: true,
      snapshot_id: 2,
    },
  ];

  const mockChangesResponse = {
    results: mockChanges,
    total: 3,
    page: 1,
    page_size: 50,
    total_pages: 1,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    viewer.canEdit = true;

    // Setup default mocks
    vi.mocked(api.getM3UAccounts).mockResolvedValue(mockAccounts);
    vi.mocked(api.getM3UChanges).mockResolvedValue(mockChangesResponse);
    vi.mocked(api.getM3UChangesSummary).mockResolvedValue(mockSummary);
    vi.mocked(api.getM3UChangeExclusions).mockResolvedValue({ exclusions: [] });
    vi.mocked(api.getM3UChangeGroups).mockResolvedValue({
      results: [{ m3u_account_id: 1, group_name: 'Sports' }, { m3u_account_id: 2, group_name: 'Sports' }],
      total: 2, total_pages: 1,
    });
    vi.mocked(api.updateM3UChangeExclusions).mockResolvedValue({
      exclusions: [{ m3u_account_id: 1, group_name: 'Sports' }],
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('initial rendering', () => {
    it('does not offer exclusion mutations to non-admin viewers', async () => {
      viewer.canEdit = false;
      vi.mocked(api.getM3UChangeExclusions).mockResolvedValue({
        exclusions: [{ m3u_account_id: 1, group_name: 'Sports' }],
      });
      renderWithProviders(<M3UChangesTab />);
      expect(await screen.findByRole('button', { name: 'Excluded groups (1)' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Clear exclusions' })).toBeDisabled();
      expect(screen.queryByRole('button', { name: /^Actions for/ })).not.toBeInTheDocument();
      expect(api.updateM3UChangeExclusions).not.toHaveBeenCalled();
    });

    it('excludes a row by exact account/name and refreshes server-filtered results', async () => {
      renderWithProviders(<M3UChangesTab />);
      fireEvent.click(await screen.findByRole('button', { name: 'Actions for Sports (Test M3U 1)' }));
      fireEvent.click(screen.getByRole('menuitem', { name: 'Exclude this group' }));
      await waitFor(() => expect(api.updateM3UChangeExclusions).toHaveBeenCalledWith({
        add: [{ m3u_account_id: 1, group_name: 'Sports' }],
      }));
      expect(await screen.findByRole('button', { name: 'Excluded groups (1)' })).toBeInTheDocument();
      await waitFor(() => expect(api.getM3UChanges).toHaveBeenCalledTimes(2));
    });

    it('loads shared exclusions and removes only the unchecked provider identity', async () => {
      vi.mocked(api.getM3UChangeExclusions).mockResolvedValue({
        exclusions: [{ m3u_account_id: 2, group_name: 'Sports' }],
      });
      renderWithProviders(<M3UChangesTab />);
      fireEvent.click(await screen.findByRole('button', { name: 'Excluded groups (1)' }));
      const providerTwo = screen.getByRole('checkbox', { name: 'Sports — Test M3U 2' });
      expect(providerTwo).toBeChecked();
      expect(screen.getByRole('checkbox', { name: 'Sports — Test M3U 1' })).not.toBeChecked();
      fireEvent.click(providerTwo);
      await waitFor(() => expect(api.updateM3UChangeExclusions).toHaveBeenCalledWith({
        add: [], remove: [{ m3u_account_id: 2, group_name: 'Sports' }],
      }));
    });

    it('searches across paginated group options and bulk-excludes matching groups', async () => {
      vi.mocked(api.getM3UChangeGroups)
        .mockResolvedValueOnce({ results: [{ m3u_account_id: 1, group_name: 'Sports' }], total: 3, total_pages: 2 })
        .mockResolvedValueOnce({ results: [
          { m3u_account_id: 2, group_name: 'Sports' }, { m3u_account_id: 1, group_name: 'News' },
        ], total: 3, total_pages: 2 });
      renderWithProviders(<M3UChangesTab />);
      const trigger = await screen.findByRole('button', { name: 'Excluded groups (0)' });
      await waitFor(() => expect(trigger).toBeEnabled());
      fireEvent.click(trigger);
      fireEvent.change(screen.getByRole('textbox', { name: 'Search Excluded groups (0)' }), { target: { value: 'Sports' } });
      fireEvent.click(screen.getByRole('button', { name: 'Select All Visible' }));
      await waitFor(() => expect(api.updateM3UChangeExclusions).toHaveBeenCalledWith({
        add: [{ m3u_account_id: 1, group_name: 'Sports' }, { m3u_account_id: 2, group_name: 'Sports' }], remove: [],
      }));
      expect(api.getM3UChangeGroups).toHaveBeenCalledWith({ page: 2, pageSize: 200 });
    });

    it('surfaces exclusion-load failures and retries without inventing an empty saved list', async () => {
      vi.mocked(api.getM3UChangeExclusions).mockRejectedValueOnce(new Error('Exclusions unavailable'));
      renderWithProviders(<M3UChangesTab />);
      expect(await screen.findByText('Exclusions unavailable')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Retry exclusions' }));
      await waitFor(() => expect(api.getM3UChangeExclusions).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(screen.getByRole('button', { name: 'Excluded groups (0)' })).toBeEnabled());
    });

    it('keeps saved state on mutation failure and allows retry', async () => {
      vi.mocked(api.updateM3UChangeExclusions).mockRejectedValue(new Error('Save failed'));
      renderWithProviders(<M3UChangesTab />);
      fireEvent.click(await screen.findByRole('button', { name: 'Actions for Sports (Test M3U 1)' }));
      fireEvent.click(screen.getByRole('menuitem', { name: 'Exclude this group' }));
      expect(await screen.findByText('Save failed')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Excluded groups (0)' })).toBeInTheDocument();
    });

    it('clears saved exclusions from the filtered empty state without deleting history', async () => {
      vi.mocked(api.getM3UChangeExclusions).mockResolvedValue({
        exclusions: [{ m3u_account_id: 1, group_name: 'Sports' }],
      });
      vi.mocked(api.getM3UChanges).mockResolvedValue({ ...mockChangesResponse, results: [], total: 0, total_pages: 0 });
      renderWithProviders(<M3UChangesTab />);
      expect(await screen.findByText('No changes match the current filters. Recorded history is retained.')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Clear exclusions' }));
      await waitFor(() => expect(api.updateM3UChangeExclusions).toHaveBeenCalledWith({ clear: true }));
    });

    it('renders the M3U Changes header', async () => {
      renderWithProviders(<M3UChangesTab />);

      expect(screen.getByText('M3U Changes')).toBeInTheDocument();

      // Let the mount-time fetch settle so the resulting state updates happen
      // inside act() (otherwise React logs an act() warning for the
      // un-awaited fetchData() that flips loading off after this test body).
      await waitFor(() => {
        expect(screen.queryByText('Loading changes...')).not.toBeInTheDocument();
      });
    });

    it('shows loading state initially', async () => {
      renderWithProviders(<M3UChangesTab />);

      expect(screen.getByText('Loading changes...')).toBeInTheDocument();

      // Let the mount-time fetch settle so the resulting state updates happen
      // inside act() (otherwise React logs an act() warning for the
      // un-awaited fetchData() that flips loading off after this test body).
      await waitFor(() => {
        expect(screen.queryByText('Loading changes...')).not.toBeInTheDocument();
      });
    });

    it('fetches accounts, changes, and summary on mount', async () => {
      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(api.getM3UAccounts).toHaveBeenCalled();
        expect(api.getM3UChanges).toHaveBeenCalled();
        expect(api.getM3UChangesSummary).toHaveBeenCalled();
      });
    });
  });

  describe('data display', () => {
    it('displays summary statistics', async () => {
      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        // Check the header stats
        expect(screen.getByText(/10 changes/)).toBeInTheDocument();
      });

      // Check summary cards by finding the summary-label elements
      await waitFor(() => {
        const summaryLabels = document.querySelectorAll('.summary-label');
        const labelTexts = Array.from(summaryLabels).map(el => el.textContent);
        expect(labelTexts).toContain('Groups Added');
        expect(labelTexts).toContain('Streams Added');
      });
    });

    it('displays change rows after loading', async () => {
      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading changes...')).not.toBeInTheDocument();
      });

      // Check that changes are displayed
      expect(screen.getByText('Sports')).toBeInTheDocument();
      expect(screen.getByText('Movies')).toBeInTheDocument();
      expect(screen.getByText('Old Group')).toBeInTheDocument();
    });

    it('displays change type badges correctly', async () => {
      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading changes...')).not.toBeInTheDocument();
      });

      // These texts may appear multiple times (in change rows and summary cards)
      expect(screen.getAllByText('Group Added').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Streams Added').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Group Removed').length).toBeGreaterThan(0);
    });

    it('displays enabled/disabled badges', async () => {
      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading changes...')).not.toBeInTheDocument();
      });

      // Should have Yes and No badges
      const yesBadges = screen.getAllByText('Yes');
      const noBadges = screen.getAllByText('No');
      expect(yesBadges.length).toBeGreaterThan(0);
      expect(noBadges.length).toBeGreaterThan(0);
    });

    it('displays account names', async () => {
      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading changes...')).not.toBeInTheDocument();
      });

      // Account names may appear multiple times (in filter dropdown and change rows)
      expect(screen.getAllByText('Test M3U 1').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Test M3U 2').length).toBeGreaterThan(0);
    });
  });

  describe('empty state', () => {
    it('shows empty state when no changes exist', async () => {
      vi.mocked(api.getM3UChanges).mockResolvedValue({
        results: [],
        total: 0,
        page: 1,
        page_size: 50,
        total_pages: 0,
      });

      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.getByText('No Changes Detected')).toBeInTheDocument();
      });

      expect(screen.getByText(/No M3U playlist changes have been recorded/)).toBeInTheDocument();
    });
  });

  describe('error handling', () => {
    it('displays error message when API fails', async () => {
      vi.mocked(api.getM3UChanges).mockRejectedValue(new Error('Network error'));

      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.getByText('Network error')).toBeInTheDocument();
      });
    });

    it('clears protected rows and suppresses the retry action on 403', async () => {
      vi.mocked(api.getM3UChanges).mockRejectedValue(new HttpError('Forbidden', 403));

      renderWithProviders(<M3UChangesTab />);

      expect(await screen.findByText(/don't have permission/i)).toBeInTheDocument();
      expect(screen.queryByText('Sports')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    });

    it('retries a recoverable load failure', async () => {
      vi.mocked(api.getM3UChanges)
        .mockRejectedValueOnce(new Error('Network error'))
        .mockResolvedValueOnce(mockChangesResponse);

      renderWithProviders(<M3UChangesTab />);
      fireEvent.click(await screen.findByRole('button', { name: 'Retry' }));

      expect(await screen.findByText('Sports')).toBeInTheDocument();
    });

    it('retains populated rows as stale after refresh failure and recovers on retry', async () => {
      vi.mocked(api.getM3UChanges).mockReset()
        .mockResolvedValueOnce(mockChangesResponse)
        .mockRejectedValueOnce(new Error('Refresh failed'))
        .mockResolvedValueOnce(mockChangesResponse);
      vi.mocked(api.getM3UChangesSummary).mockReset()
        .mockResolvedValueOnce(mockSummary)
        .mockResolvedValueOnce(mockSummary)
        .mockResolvedValueOnce(mockSummary);
      renderWithProviders(<M3UChangesTab />);
      expect(await screen.findByText('Sports')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /refresh/i }));

      expect(await screen.findByText(/showing previously loaded changes/i)).toBeInTheDocument();
      expect(screen.getByText('Sports')).toBeInTheDocument();
      expect(screen.getByText('3 total changes')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
      await waitFor(() => expect(screen.queryByText(/showing previously loaded changes/i)).not.toBeInTheDocument());
      expect(screen.getByText('Sports')).toBeInTheDocument();
    });

    it('gives permission precedence for mixed summary 403 and changes 502 failures', async () => {
      vi.mocked(api.getM3UChanges).mockReset();
      vi.mocked(api.getM3UChangesSummary).mockReset();
      vi.mocked(api.getM3UChanges).mockRejectedValue(new HttpError('Unavailable', 502));
      vi.mocked(api.getM3UChangesSummary).mockRejectedValue(new HttpError('Forbidden', 403));

      renderWithProviders(<M3UChangesTab />);

      expect(await screen.findByText(/don't have permission/i)).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Refresh' })).not.toBeInTheDocument();
    });

    it('ignores an older refresh after a newer filter request commits', async () => {
      let resolveRefreshChanges!: (value: typeof mockChangesResponse) => void;
      let resolveRefreshSummary!: (value: M3UChangeSummary) => void;
      const filteredResponse = {
        ...mockChangesResponse,
        results: [{ ...mockChanges[0], id: 41, group_name: 'Newest filtered row' }],
        total: 1,
      };
      const lateResponse = {
        ...mockChangesResponse,
        results: [{ ...mockChanges[0], id: 42, group_name: 'Late stale row' }],
        total: 1,
      };
      vi.mocked(api.getM3UChanges).mockReset()
        .mockResolvedValueOnce(mockChangesResponse)
        .mockReturnValueOnce(new Promise(resolve => { resolveRefreshChanges = resolve; }))
        .mockResolvedValueOnce(filteredResponse);
      vi.mocked(api.getM3UChangesSummary).mockReset()
        .mockResolvedValueOnce(mockSummary)
        .mockReturnValueOnce(new Promise(resolve => { resolveRefreshSummary = resolve; }))
        .mockResolvedValueOnce({ ...mockSummary, total_changes: 1 });

      renderWithProviders(<M3UChangesTab />);
      expect(await screen.findByText('Sports')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /refresh/i }));
      fireEvent.click(screen.getByText('Last 7 days'));
      fireEvent.click(await screen.findByRole('option', { name: 'Last 24 hours' }));

      expect(await screen.findByText('Newest filtered row')).toBeInTheDocument();
      resolveRefreshChanges(lateResponse);
      resolveRefreshSummary({ ...mockSummary, total_changes: 99 });

      await waitFor(() => expect(screen.queryByText('Late stale row')).not.toBeInTheDocument());
      expect(screen.getByText('Newest filtered row')).toBeInTheDocument();
      expect(screen.getByText('1 total changes')).toBeInTheDocument();
    });

    it('keeps current-generation permission precedence when an older refresh resolves late', async () => {
      let resolveRefreshChanges!: (value: typeof mockChangesResponse) => void;
      let resolveRefreshSummary!: (value: M3UChangeSummary) => void;
      vi.mocked(api.getM3UChanges).mockReset()
        .mockResolvedValueOnce(mockChangesResponse)
        .mockReturnValueOnce(new Promise(resolve => { resolveRefreshChanges = resolve; }))
        .mockRejectedValueOnce(new HttpError('Unavailable', 502));
      vi.mocked(api.getM3UChangesSummary).mockReset()
        .mockResolvedValueOnce(mockSummary)
        .mockReturnValueOnce(new Promise(resolve => { resolveRefreshSummary = resolve; }))
        .mockRejectedValueOnce(new HttpError('Forbidden', 403));

      renderWithProviders(<M3UChangesTab />);
      expect(await screen.findByText('Sports')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /refresh/i }));
      fireEvent.click(screen.getByText('Last 7 days'));
      fireEvent.click(await screen.findByRole('option', { name: 'Last 24 hours' }));

      expect(await screen.findByText(/don't have permission/i)).toBeInTheDocument();
      resolveRefreshChanges(mockChangesResponse);
      resolveRefreshSummary(mockSummary);

      await waitFor(() => expect(screen.getByText(/don't have permission/i)).toBeInTheDocument());
      expect(screen.queryByText('Sports')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    });

  });

  it('uses native sort buttons and exposes the active sort direction', async () => {
    renderWithProviders(<M3UChangesTab />);
    const timeButton = await screen.findByRole('button', { name: /Sort by Time, currently descending/ });

    fireEvent.click(timeButton);

    await waitFor(() => {
      expect(api.getM3UChanges).toHaveBeenLastCalledWith(
        expect.objectContaining({ sortBy: 'change_time', sortOrder: 'asc' }),
      );
    });
    expect(screen.getByRole('button', { name: /Sort by Time, currently ascending/ })).toBeInTheDocument();
  });

  describe('row expansion', () => {
    it('expands row when clicked', async () => {
      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading changes...')).not.toBeInTheDocument();
      });

      // Find and click the first change row
      const sportsRow = screen.getByText('Sports').closest('.change-row');
      expect(sportsRow).toBeInTheDocument();

      fireEvent.click(sportsRow!);

      // Should show expanded details
      await waitFor(() => {
        expect(screen.getByText('Change Details')).toBeInTheDocument();
        expect(screen.getByText('Change ID:')).toBeInTheDocument();
      });
    });

    it('shows stream names in expanded view', async () => {
      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading changes...')).not.toBeInTheDocument();
      });

      // Find and click the Movies row (which has stream names)
      const moviesRow = screen.getByText('Movies').closest('.change-row');
      fireEvent.click(moviesRow!);

      await waitFor(() => {
        expect(screen.getByText('Stream Names (3)')).toBeInTheDocument();
        expect(screen.getByText('Movie 1')).toBeInTheDocument();
        expect(screen.getByText('Movie 2')).toBeInTheDocument();
        expect(screen.getByText('Movie 3')).toBeInTheDocument();
      });
    });
  });

  describe('refresh functionality', () => {
    it('refreshes data when refresh button is clicked', async () => {
      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading changes...')).not.toBeInTheDocument();
      });

      // Clear the call count
      vi.mocked(api.getM3UChanges).mockClear();
      vi.mocked(api.getM3UChangesSummary).mockClear();

      // Click refresh button
      const refreshButton = screen.getByRole('button', { name: /refresh/i });
      fireEvent.click(refreshButton);

      await waitFor(() => {
        expect(api.getM3UChanges).toHaveBeenCalled();
        expect(api.getM3UChangesSummary).toHaveBeenCalled();
      });
    });
  });

  describe('pagination', () => {
    it('returns to page one after excluding a row on a later page', async () => {
      vi.mocked(api.getM3UChanges).mockResolvedValue({ ...mockChangesResponse, total: 100, total_pages: 2 });
      renderWithProviders(<M3UChangesTab />);
      await screen.findByText('Page 1 of 2');
      fireEvent.click(screen.getByTitle('Next page'));
      await screen.findByText('Page 2 of 2');
      fireEvent.click(screen.getByRole('button', { name: 'Actions for Sports (Test M3U 1)' }));
      fireEvent.click(screen.getByRole('menuitem', { name: 'Exclude this group' }));
      await screen.findByText('Page 1 of 2');
      await waitFor(() => expect(api.getM3UChanges).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 })));
    });

    it('displays pagination controls', async () => {
      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading changes...')).not.toBeInTheDocument();
      });

      expect(screen.getByText('Page 1 of 1')).toBeInTheDocument();
      expect(screen.getByText('3 total changes')).toBeInTheDocument();
    });

    it('disables previous/first buttons on first page', async () => {
      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.queryByText('Loading changes...')).not.toBeInTheDocument();
      });

      const firstPageButton = screen.getByTitle('First page');
      const prevPageButton = screen.getByTitle('Previous page');

      expect(firstPageButton).toBeDisabled();
      expect(prevPageButton).toBeDisabled();
    });

    it('enables next/last buttons when more pages exist', async () => {
      vi.mocked(api.getM3UChanges).mockResolvedValue({
        results: mockChanges,
        total: 100,
        page: 1,
        page_size: 50,
        total_pages: 2,
      });

      renderWithProviders(<M3UChangesTab />);

      await waitFor(() => {
        expect(screen.getByText('Page 1 of 2')).toBeInTheDocument();
      });

      const nextPageButton = screen.getByTitle('Next page');
      const lastPageButton = screen.getByTitle('Last page');

      expect(nextPageButton).not.toBeDisabled();
      expect(lastPageButton).not.toBeDisabled();
    });
  });
});

// M3UChangesTab helper functions (formatChangeType, getChangeTypeClass, getChangeTypeIcon)
// are verified through component integration tests above. Time formatting now goes
// through the shared utils/formatting.formatRelativeTime (bd-juy2e), tested in
// utils/formatting.relativetime.test.ts.
// Removing the empty describe block to keep the suite clean.
