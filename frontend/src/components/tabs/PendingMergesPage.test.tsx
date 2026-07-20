/**
 * Unit tests for PendingMergesPage — the operator-facing queue view for
 * stream-to-channel dedup candidates queued by the bulk-M3U import hook
 * (BD-F) and the interactive trigger surfaces (BD-H / BD-I).
 *
 * Tests lock the BD-J / bd-gfxrz contract (per the parent epic bd-1v4ht and
 * ADR-008 §D1):
 *
 *   - Renders one row per PendingMergeRecord returned by
 *     GET /api/channel-merges?status=pending&page=1&page_size=50, with the
 *     stream name and confidence badge visible.
 *   - Empty state shows "No pending merges" plus the PO-ratified UX-recommended
 *     nudge text ("Pending Merges will appear here after an M3U refresh ...").
 *   - Per-row Merge button calls POST /api/channel-merges/{id}/accept and
 *     removes the row on success.
 *   - Per-row Create New button calls POST /api/channel-merges/{id}/dismiss
 *     and removes the row on success.
 *   - A backend error on accept/dismiss surfaces verbatim in an inline
 *     error banner; the row stays in place so the operator can retry.
 *   - Bulk actions support all/selected merge and clear operations while
 *     preserving per-row endpoint semantics (GH #642 / bead ixcf1).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { PendingMergesPage } from './PendingMergesPage';
import type { PendingMergeRecord } from '../../services/api';
import * as api from '../../services/api';

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>(
    '../../services/api',
  );
  return {
    ...actual,
    getPendingMerges: vi.fn(),
    acceptPendingMerge: vi.fn(),
    dismissPendingMerge: vi.fn(),
  };
});

function makeRecord(overrides: Partial<PendingMergeRecord> = {}): PendingMergeRecord {
  return {
    id: 1,
    stream_name: 'ESPN HD',
    group_id: 7,
    candidate_channel_id: 'channel-uuid-abc',
    candidate_channel_name: 'ESPN HD (Existing)',
    candidate_channel_number: 101,
    candidate_channel_group_name: 'Sports',
    confidence: 0.92,
    status: 'pending',
    created_at: 1_715_817_600_000,
    resolved_at: null,
    resolution_source: null,
    trigger_context: 'm3u_refresh',
    ...overrides,
  };
}

describe('PendingMergesPage — list rendering (BD-J / bd-gfxrz)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders one row per record with stream name and confidence badge', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [
        makeRecord({ id: 1, stream_name: 'ESPN HD', confidence: 0.92 }),
        makeRecord({ id: 2, stream_name: 'CNN HD', confidence: 1.0 }),
      ],
      total: 2,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });

    render(<PendingMergesPage />);

    expect(await screen.findByText('ESPN HD')).toBeInTheDocument();
    expect(screen.getByText('CNN HD')).toBeInTheDocument();

    // Fuzzy candidate renders the percent badge; exact match renders "Exact match".
    expect(screen.getByLabelText(/Confidence: 92 percent/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Exact match')).toBeInTheDocument();

    // The call matches the BD-J spec: pending status, page=1, page_size=50.
    expect(api.getPendingMerges).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'pending', page: 1, pageSize: 50 }),
    );
  });

  it('renders the empty state with the PO-ratified nudge copy when there are no rows', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [],
      total: 0,
      page: 1,
      page_size: 50,
      total_pages: 0,
    });

    render(<PendingMergesPage />);

    expect(await screen.findByText(/No pending merges/i)).toBeInTheDocument();
    // PO-ratified nudge text from epic bd-1v4ht UX section.
    expect(
      screen.getByText(/M3U refresh detects potential duplicates/i),
    ).toBeInTheDocument();
  });

  it('renders the candidate channel name, number, and group when resolved (bead 09x38.14)', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [
        makeRecord({
          id: 1,
          candidate_channel_id: '501',
          candidate_channel_name: 'ESPN HD',
          candidate_channel_number: 101,
          candidate_channel_group_name: 'Sports',
        }),
      ],
      total: 1,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });

    render(<PendingMergesPage />);

    const nameEl = await screen.findByTestId('pending-merges-candidate-name');
    expect(nameEl).toHaveTextContent('#101 ESPN HD');
    expect(screen.getByText('Sports')).toBeInTheDocument();
    // The raw id stays visible as secondary text.
    expect(screen.getByText('id 501')).toBeInTheDocument();
  });

  it('renders an explicit "no longer exists" state when the candidate channel is unresolved (bead 09x38.14)', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [
        makeRecord({
          id: 1,
          candidate_channel_id: '999',
          candidate_channel_name: null,
          candidate_channel_number: null,
          candidate_channel_group_name: null,
        }),
      ],
      total: 1,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });

    render(<PendingMergesPage />);

    expect(
      await screen.findByText(/Channel no longer exists \(id 999\)/i),
    ).toBeInTheDocument();
  });

  it('renders bulk actions and disables selection-dependent actions initially', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [makeRecord()],
      total: 1,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');

    expect(screen.getByRole('button', { name: /^Select all$/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /^Deselect all$/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^Merge all$/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /^Clear all$/i })).toBeEnabled();
    expect(screen.getByRole('button', { name: /^Merge selected$/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^Clear selected$/i })).toBeDisabled();
  });
});

describe('PendingMergesPage — per-row actions (BD-E accept/dismiss)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('calls acceptPendingMerge with the row id when Merge is clicked, then drops the row', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [makeRecord({ id: 42, stream_name: 'ESPN HD' })],
      total: 1,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });
    vi.mocked(api.acceptPendingMerge).mockResolvedValue({
      merged_into_channel_id: 'channel-uuid-abc',
      journal_entry_id: 100,
      source_stream_id: 'stream-uuid-xyz',
      confidence: 0.92,
      status: 'merged',
    });

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');

    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));

    await waitFor(() => {
      expect(api.acceptPendingMerge).toHaveBeenCalledWith(42);
    });
    // Row removed after successful accept.
    await waitFor(() => {
      expect(screen.queryByText('ESPN HD')).toBeNull();
    });
  });

  it('drops the row selection when a single-row action resolves it', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [
        makeRecord({ id: 42, stream_name: 'ESPN HD' }),
        makeRecord({ id: 43, stream_name: 'CNN HD' }),
      ],
      total: 2,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });
    vi.mocked(api.acceptPendingMerge).mockResolvedValue({
      merged_into_channel_id: 'channel-uuid-abc',
      journal_entry_id: 100,
      source_stream_id: 'stream-uuid-xyz',
      confidence: 0.92,
      status: 'merged',
    });

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('checkbox', { name: /Select ESPN HD/i }));
    expect(screen.getByText('1 selected')).toBeInTheDocument();
    fireEvent.click(
      screen
        .getByText('ESPN HD')
        .closest('.pending-merges-row')!
        .querySelector<HTMLButtonElement>('.pending-merges-merge-btn')!,
    );

    await waitFor(() => expect(screen.queryByText('ESPN HD')).toBeNull());
    expect(screen.getByText('CNN HD')).toBeInTheDocument();
    expect(screen.queryByText(/^\d+ selected$/i)).toBeNull();
  });

  it('calls dismissPendingMerge with the row id when Create New is clicked, then drops the row', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [makeRecord({ id: 99, stream_name: 'CNN HD' })],
      total: 1,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });
    vi.mocked(api.dismissPendingMerge).mockResolvedValue({
      journal_entry_id: 200,
      status: 'dismissed',
    });

    render(<PendingMergesPage />);
    await screen.findByText('CNN HD');

    fireEvent.click(screen.getByRole('button', { name: /Create New/i }));

    await waitFor(() => {
      expect(api.dismissPendingMerge).toHaveBeenCalledWith(99);
    });
    await waitFor(() => {
      expect(screen.queryByText('CNN HD')).toBeNull();
    });
  });

  it('surfaces backend error detail in an inline banner and leaves the row in place', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [makeRecord({ id: 7, stream_name: 'ESPN HD' })],
      total: 1,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });
    vi.mocked(api.acceptPendingMerge).mockRejectedValue(
      new Error(
        'Target channel no longer exists — dismiss this pending merge and refresh.',
      ),
    );

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');

    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));

    expect(
      await screen.findByText(/Target channel no longer exists/i),
    ).toBeInTheDocument();
    // Row is still present so the operator can dismiss or retry.
    expect(screen.getByText('ESPN HD')).toBeInTheDocument();
  });
});

describe('PendingMergesPage — bulk actions (GH #642 / bead ixcf1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  function mockRows() {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [
        makeRecord({ id: 1, stream_name: 'ESPN HD' }),
        makeRecord({ id: 2, stream_name: 'CNN HD' }),
        makeRecord({ id: 3, stream_name: 'BBC HD' }),
      ],
      total: 3,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });
  }

  it('selects and deselects every loaded row with accessible checkboxes', async () => {
    mockRows();
    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');

    fireEvent.click(screen.getByRole('button', { name: /^Select all$/i }));
    expect(screen.getByText('3 selected')).toBeInTheDocument();
    expect(screen.getAllByRole('checkbox')).toHaveLength(3);
    screen.getAllByRole('checkbox').forEach((checkbox) => {
      expect(checkbox).toBeChecked();
    });
    expect(screen.getByRole('button', { name: /Merge selected/i })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: /^Deselect all$/i }));
    expect(screen.queryByText(/^\d+ selected$/i)).toBeNull();
    screen.getAllByRole('checkbox').forEach((checkbox) => {
      expect(checkbox).not.toBeChecked();
    });
  });

  it('merges only selected rows sequentially and preserves unselected rows', async () => {
    mockRows();
    const callOrder: number[] = [];
    vi.mocked(api.acceptPendingMerge).mockImplementation(async (id) => {
      callOrder.push(id);
      return {
        merged_into_channel_id: 'channel-uuid-abc',
        journal_entry_id: id,
        source_stream_id: `stream-${id}`,
        confidence: 0.92,
        status: 'merged',
      };
    });

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('checkbox', { name: /Select ESPN HD/i }));
    fireEvent.click(screen.getByRole('checkbox', { name: /Select BBC HD/i }));
    fireEvent.click(screen.getByRole('button', { name: /Merge selected/i }));

    await waitFor(() => expect(callOrder).toEqual([1, 3]));
    expect(window.confirm).toHaveBeenCalledWith(
      'Merge 2 selected pending merges? This will attach each incoming stream to its candidate channel.',
    );
    await waitFor(() => expect(screen.queryByText('ESPN HD')).toBeNull());
    expect(screen.queryByText('BBC HD')).toBeNull();
    expect(screen.getByText('CNN HD')).toBeInTheDocument();
  });

  it('clear all dismisses every loaded row and is cancelled when confirmation is declined', async () => {
    mockRows();
    vi.mocked(window.confirm).mockReturnValue(false);
    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');

    fireEvent.click(screen.getByRole('button', { name: /Clear all/i }));

    expect(api.dismissPendingMerge).not.toHaveBeenCalled();
    expect(screen.getByText('ESPN HD')).toBeInTheDocument();
  });

  it('snapshots every page before Merge all so mutation cannot shift pagination', async () => {
    const allRows = Array.from({ length: 201 }, (_, index) =>
      makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
    );
    vi.mocked(api.getPendingMerges).mockImplementation(async (params) => {
      if (params?.pageSize === 200) {
        const page = params.page ?? 1;
        const start = (page - 1) * 200;
        return {
          merges: allRows.slice(start, start + 200),
          total: 201,
          page,
          page_size: 200,
          total_pages: 2,
        };
      }
      return {
        merges: allRows.slice(0, 50),
        total: 201,
        page: 1,
        page_size: 50,
        total_pages: 5,
      };
    });
    vi.mocked(api.acceptPendingMerge).mockImplementation(async (id) => ({
      merged_into_channel_id: 'channel-uuid-abc',
      journal_entry_id: id,
      source_stream_id: `stream-${id}`,
      confidence: 0.92,
      status: 'merged',
    }));

    render(<PendingMergesPage />);
    await screen.findByText('Stream 1');
    fireEvent.click(screen.getByRole('button', { name: /^Merge all$/i }));

    await waitFor(() => expect(api.acceptPendingMerge).toHaveBeenCalledTimes(201));
    expect(api.getPendingMerges).toHaveBeenCalledWith({
      status: 'pending',
      page: 1,
      pageSize: 200,
    });
    expect(api.getPendingMerges).toHaveBeenCalledWith({
      status: 'pending',
      page: 2,
      pageSize: 200,
    });
    expect(window.confirm).toHaveBeenCalledWith(
      'Merge 201 pending merges? This will attach each incoming stream to its candidate channel.',
    );
  });

  it('continues after a partial failure, removes successes, and leaves failures selected for retry', async () => {
    mockRows();
    vi.mocked(api.dismissPendingMerge)
      .mockResolvedValueOnce({ journal_entry_id: 10, status: 'dismissed' })
      .mockRejectedValueOnce(new Error('Candidate changed while clearing'))
      .mockResolvedValueOnce({ journal_entry_id: 12, status: 'dismissed' });

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Select all$/i }));
    fireEvent.click(screen.getByRole('button', { name: /Clear selected/i }));

    await waitFor(() => expect(api.dismissPendingMerge).toHaveBeenCalledTimes(3));
    expect(screen.queryByText('ESPN HD')).toBeNull();
    expect(screen.getByText('CNN HD')).toBeInTheDocument();
    expect(screen.queryByText('BBC HD')).toBeNull();
    expect(await screen.findByText('Candidate changed while clearing')).toBeInTheDocument();
    expect(screen.getByText('1 selected')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /Select CNN HD/i })).toBeChecked();
  });

  it('disables every mutating action during a bulk request to prevent duplicate submissions', async () => {
    mockRows();
    let resolveFirst: (() => void) | undefined;
    vi.mocked(api.acceptPendingMerge)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = () =>
              resolve({
                merged_into_channel_id: 'channel-uuid-abc',
                journal_entry_id: 1,
                source_stream_id: 'stream-1',
                confidence: 0.92,
                status: 'merged',
              });
          }),
      )
      .mockImplementation(async (id) => ({
              merged_into_channel_id: 'channel-uuid-abc',
              journal_entry_id: id,
              source_stream_id: `stream-${id}`,
              confidence: 0.92,
              status: 'merged',
      }));

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /Merge all/i }));

    expect(await screen.findByRole('button', { name: /Merging 1 of 3/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^Clear all$/i })).toBeDisabled();
    expect(screen.getAllByRole('button', { name: /^Working\.\.\.$/i })[0]).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /Merging 1 of 3/i }));
    expect(api.acceptPendingMerge).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst?.();
    });
    await waitFor(() => expect(api.acceptPendingMerge).toHaveBeenCalledTimes(3));
  });

  it('drops stale selections after a refresh replaces the loaded rows', async () => {
    mockRows();
    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('checkbox', { name: /Select ESPN HD/i }));
    expect(screen.getByText('1 selected')).toBeInTheDocument();

    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [makeRecord({ id: 4, stream_name: 'FOX HD' })],
      total: 1,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });
    fireEvent.click(screen.getByRole('button', { name: /Refresh/i }));

    await screen.findByText('FOX HD');
    await waitFor(() => expect(screen.queryByText(/^\d+ selected$/i)).toBeNull());
    expect(screen.getByRole('button', { name: /Merge selected/i })).toBeDisabled();
  });
});
