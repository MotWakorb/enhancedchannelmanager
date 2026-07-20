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
import { render, screen, waitFor, fireEvent, act, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    getPendingMergesSnapshot: vi.fn(),
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
    vi.resetAllMocks();
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
    vi.resetAllMocks();
    vi.mocked(api.getPendingMergesSnapshot).mockImplementation(async () => {
      const response = await api.getPendingMerges({
        status: 'pending',
        page: 1,
        pageSize: 200,
      });
      return { merges: response.merges, total: response.total };
    });
  });

  async function startBulk(actionName: RegExp) {
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: actionName }));
    });
    const dialog = await screen.findByRole('dialog', { name: /Confirm bulk action/i });
    await act(async () => {
      fireEvent.click(
        within(dialog).getByRole('button', { name: /^Confirm (merge|clear)$/i }),
      );
    });
  }

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

  it('selects and deselects the whole queue with accessible checkboxes', async () => {
    mockRows();
    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');

    fireEvent.click(screen.getByRole('button', { name: /^Select all$/i }));
    expect(await screen.findByText('3 selected')).toBeInTheDocument();
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

  it('Select all loads every page and Merge selected resolves off-page rows', async () => {
    const allRows = Array.from({ length: 51 }, (_, index) =>
      makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
    );
    vi.mocked(api.getPendingMerges).mockImplementation(async (params) => ({
      merges: params?.pageSize === 200 ? allRows : allRows.slice(0, 50),
      total: 51,
      page: params?.page ?? 1,
      page_size: params?.pageSize ?? 50,
      total_pages: params?.pageSize === 200 ? 1 : 2,
    }));
    vi.mocked(api.acceptPendingMerge).mockImplementation(async (id) => ({
      merged_into_channel_id: 'channel-uuid-abc',
      journal_entry_id: id,
      source_stream_id: `stream-${id}`,
      confidence: 0.92,
      status: 'merged',
    }));

    render(<PendingMergesPage />);
    await screen.findByText('Stream 1');
    fireEvent.click(screen.getByRole('button', { name: /^Select all$/i }));

    expect(await screen.findByText('51 selected')).toBeInTheDocument();
    expect(screen.getByText('Stream 51')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Select Stream 51' })).toBeChecked();

    await startBulk(/^Merge selected$/i);
    await waitFor(() => expect(api.acceptPendingMerge).toHaveBeenCalledTimes(51));
    expect(api.acceptPendingMerge).toHaveBeenCalledWith(51);
  });

  it('Deselect all clears a queue-wide selection loaded across pagination', async () => {
    const allRows = Array.from({ length: 51 }, (_, index) =>
      makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
    );
    vi.mocked(api.getPendingMerges).mockImplementation(async (params) => ({
      merges: params?.pageSize === 200 ? allRows : allRows.slice(0, 50),
      total: 51,
      page: params?.page ?? 1,
      page_size: params?.pageSize ?? 50,
      total_pages: params?.pageSize === 200 ? 1 : 2,
    }));

    render(<PendingMergesPage />);
    await screen.findByText('Stream 1');
    fireEvent.click(screen.getByRole('button', { name: /^Select all$/i }));
    expect(await screen.findByText('51 selected')).toBeInTheDocument();

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
    await startBulk(/^Merge selected$/i);

    await waitFor(() => expect(callOrder).toEqual([1, 3]));
    await waitFor(() => expect(screen.queryByText('ESPN HD')).toBeNull());
    expect(screen.queryByText('BBC HD')).toBeNull();
    expect(screen.getByText('CNN HD')).toBeInTheDocument();
  });

  it('clear all dismisses every loaded row and is cancelled when confirmation is declined', async () => {
    mockRows();
    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');

    fireEvent.click(screen.getByRole('button', { name: /Clear all/i }));
    const dialog = await screen.findByRole('dialog', { name: /Confirm bulk action/i });
    expect(within(dialog).getByText(/3 pending merges/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/cannot be undone/i)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: /^Cancel$/i }));

    expect(api.dismissPendingMerge).not.toHaveBeenCalled();
    expect(screen.getByText('ESPN HD')).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: /Confirm bulk action/i })).toBeNull();
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
    vi.mocked(api.getPendingMergesSnapshot).mockResolvedValue({
      merges: allRows,
      total: allRows.length,
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
    await startBulk(/^Merge all$/i);

    await waitFor(() => expect(api.acceptPendingMerge).toHaveBeenCalledTimes(201));
    expect(api.getPendingMergesSnapshot).toHaveBeenCalledTimes(1);
  });

  it.each([
    {
      label: 'Merge all',
      action: 'merge' as const,
      error: 'Off-page merge changed before resolution',
    },
    {
      label: 'Clear all',
      action: 'clear' as const,
      error: 'Off-page clear changed before resolution',
    },
  ])(
    '$label retains a failed 51st record with its error and retry controls',
    async ({ label, action, error }) => {
      const allRows = Array.from({ length: 51 }, (_, index) =>
        makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
      );
      vi.mocked(api.getPendingMerges).mockImplementation(async (params) => ({
        merges: params?.pageSize === 200 ? allRows : allRows.slice(0, 50),
        total: 51,
        page: params?.page ?? 1,
        page_size: params?.pageSize ?? 50,
        total_pages: params?.pageSize === 200 ? 1 : 2,
      }));
      vi.mocked(api.acceptPendingMerge).mockImplementation(async (id) => {
        if (id === 51) throw new Error(error);
        return {
          merged_into_channel_id: 'channel-uuid-abc',
          journal_entry_id: id,
          source_stream_id: `stream-${id}`,
          confidence: 0.92,
          status: 'merged',
        };
      });
      vi.mocked(api.dismissPendingMerge).mockImplementation(async (id) => {
        if (id === 51) throw new Error(error);
        return { journal_entry_id: id, status: 'dismissed' };
      });

      render(<PendingMergesPage />);
      await screen.findByText('Stream 1');
      await startBulk(new RegExp(`^${label}$`, 'i'));

      const mutation =
        action === 'merge' ? api.acceptPendingMerge : api.dismissPendingMerge;
      await waitFor(() => expect(mutation).toHaveBeenCalledTimes(51));
      expect(await screen.findByText('Stream 51')).toBeInTheDocument();
      expect(screen.getByText(error)).toBeInTheDocument();
      expect(screen.getByText('1 selected')).toBeInTheDocument();
      expect(screen.getByRole('checkbox', { name: 'Select Stream 51' })).toBeChecked();
      expect(screen.queryByText('No pending merges')).toBeNull();
      const failedRow = screen.getByText('Stream 51').closest('.pending-merges-row')!;
      expect(
        failedRow.querySelector<HTMLButtonElement>('.pending-merges-merge-btn'),
      ).toBeEnabled();
      expect(
        Array.from(failedRow.querySelectorAll('button')).find(
          (button) => button.textContent === 'Create New',
        ),
      ).toBeEnabled();
    },
  );

  it('continues after a partial failure, removes successes, and leaves failures selected for retry', async () => {
    mockRows();
    vi.mocked(api.dismissPendingMerge)
      .mockResolvedValueOnce({ journal_entry_id: 10, status: 'dismissed' })
      .mockRejectedValueOnce(new Error('Candidate changed while clearing'))
      .mockResolvedValueOnce({ journal_entry_id: 12, status: 'dismissed' });

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Select all$/i }));
    await screen.findByText('3 selected');
    await startBulk(/^Clear selected$/i);

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
    await startBulk(/^Merge all$/i);

    const progress = await screen.findByRole('status', {
      name: /Bulk action progress/i,
    });
    expect(progress).toHaveTextContent(/Merged 0 of 3/i);
    expect(screen.getByRole('button', { name: /^Clear all$/i })).toBeDisabled();
    expect(screen.getAllByRole('button', { name: /^Working\.\.\.$/i })[0]).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /^Merge all$/i }));
    expect(api.acceptPendingMerge).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst?.();
    });
    await waitFor(() => expect(api.acceptPendingMerge).toHaveBeenCalledTimes(3));
  });

  it('targets a 201-row snapshot, bounds its DOM window, and keeps live progress mounted', async () => {
    const allRows = Array.from({ length: 201 }, (_, index) =>
      makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
    );
    vi.mocked(api.getPendingMerges).mockImplementation(async (params) => {
      if (params?.pageSize === 200) {
        const page = params.page ?? 1;
        return {
          merges: allRows.slice((page - 1) * 200, page * 200),
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
    vi.mocked(api.getPendingMergesSnapshot).mockResolvedValue({
      merges: allRows,
      total: allRows.length,
    });
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
    await screen.findByText('Stream 1');
    fireEvent.click(screen.getByRole('button', { name: /^Merge all$/i }));

    const dialog = await screen.findByRole('dialog', { name: /Confirm bulk action/i });
    expect(screen.getByText('Stream 200')).toBeInTheDocument();
    expect(screen.queryByText('Stream 201')).toBeNull();
    expect(within(dialog).getByText(/201 pending merges/i)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: /^Confirm merge$/i }));

    const status = await screen.findByRole('status', { name: /Bulk action progress/i });
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(within(status).getByText(/Merged 0 of 201/i)).toBeInTheDocument();
    expect(within(status).getByRole('button', { name: /^Stop$/i })).toBeEnabled();
    expect(screen.getByText('Stream 200')).toBeInTheDocument();

    await act(async () => resolveFirst?.());
    await waitFor(() => expect(api.acceptPendingMerge).toHaveBeenCalledTimes(201));
    expect(screen.getByRole('status', { name: /Bulk action progress/i })).toHaveTextContent(
      'Completed 201 of 201',
    );
  });

  it('Stop waits for the in-flight row, skips every later row, and keeps the remainder selected', async () => {
    const allRows = Array.from({ length: 3 }, (_, index) =>
      makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
    );
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: allRows,
      total: 3,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });
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
    await screen.findByText('Stream 1');
    await startBulk(/^Merge all$/i);
    const status = await screen.findByRole('status', { name: /Bulk action progress/i });
    fireEvent.click(within(status).getByRole('button', { name: /^Stop$/i }));
    expect(status).toHaveTextContent(/Stopping after the current item/i);

    await act(async () => resolveFirst?.());
    await waitFor(() => expect(status).toHaveTextContent('Stopped after 1 of 3'));
    expect(api.acceptPendingMerge).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('Stream 1')).toBeNull();
    expect(screen.getByText('Stream 2')).toBeInTheDocument();
    expect(screen.getByText('Stream 3')).toBeInTheDocument();
    expect(screen.getByText('2 selected')).toBeInTheDocument();
  });

  it('guards modal confirmation against same-tick double submission', async () => {
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
    const trigger = screen.getByRole('button', { name: /^Merge all$/i });
    fireEvent.click(trigger);
    fireEvent.click(trigger);
    const dialog = await screen.findByRole('dialog', { name: /Confirm bulk action/i });
    const confirm = within(dialog).getByRole('button', { name: /^Confirm merge$/i });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    await waitFor(() => expect(api.acceptPendingMerge).toHaveBeenCalledTimes(1));
    await act(async () => resolveFirst?.());
    await waitFor(() => expect(api.acceptPendingMerge).toHaveBeenCalledTimes(3));
  });

  it('retains multiple failures, including an all-failing sweep, with exact errors', async () => {
    mockRows();
    vi.mocked(api.dismissPendingMerge).mockImplementation(async (id) => {
      throw new Error(`Clear failed for row ${id}`);
    });

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    await startBulk(/^Clear all$/i);

    await waitFor(() => expect(api.dismissPendingMerge).toHaveBeenCalledTimes(3));
    expect(screen.getByText('Clear failed for row 1')).toBeInTheDocument();
    expect(screen.getByText('Clear failed for row 2')).toBeInTheDocument();
    expect(screen.getByText('Clear failed for row 3')).toBeInTheDocument();
    expect(screen.getByText('3 selected')).toBeInTheDocument();
    expect(screen.queryByText('No pending merges')).toBeNull();
    expect(screen.getByRole('status', { name: /Bulk action progress/i })).toHaveTextContent(
      'Completed 3 of 3 with 3 failures',
    );
  });

  it('resolves each id from one coherent 401-row server snapshot exactly once', async () => {
    const records = Array.from({ length: 401 }, (_, index) =>
      makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
    );
    vi.mocked(api.getPendingMerges).mockImplementation(async (params) => {
      if (params?.pageSize !== 200) {
        return {
          merges: records.slice(0, 50),
          total: 201,
          page: 1,
          page_size: 50,
          total_pages: 5,
        };
      }
      const page = params.page ?? 1;
      const pageRows =
        page === 1
          ? records.slice(0, 200)
          : page === 2
            ? records.slice(149, 349)
            : records.slice(349);
      return {
        merges: pageRows,
        total: 401,
        page,
        page_size: 200,
        total_pages: 3,
      };
    });
    vi.mocked(api.getPendingMergesSnapshot).mockResolvedValue({
      merges: records,
      total: records.length,
    });
    const resolvedIds: number[] = [];
    vi.mocked(api.acceptPendingMerge).mockImplementation(async (id) => {
      resolvedIds.push(id);
      return {
        merged_into_channel_id: 'channel-uuid-abc',
        journal_entry_id: id,
        source_stream_id: `stream-${id}`,
        confidence: 0.92,
        status: 'merged',
      };
    });

    render(<PendingMergesPage />);
    await screen.findByText('Stream 1');
    await startBulk(/^Merge all$/i);

    await waitFor(() => expect(resolvedIds).toHaveLength(401));
    expect(new Set(resolvedIds).size).toBe(401);
    expect(resolvedIds).toEqual(expect.arrayContaining([1, 200, 201, 350, 401]));
  });

  it('uses the coherent server snapshot when the queue contracted before confirmation', async () => {
    const original = Array.from({ length: 401 }, (_, index) =>
      makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
    );
    const contracted = original.slice(100);
    let bulkPageCalls = 0;
    vi.mocked(api.getPendingMerges).mockImplementation(async (params) => {
      if (params?.pageSize !== 200) {
        return {
          merges: original.slice(0, 50),
          total: 401,
          page: 1,
          page_size: 50,
          total_pages: 9,
        };
      }
      bulkPageCalls += 1;
      const page = params.page ?? 1;
      const source = bulkPageCalls === 1 ? original : contracted;
      return {
        merges: source.slice((page - 1) * 200, page * 200),
        total: source.length,
        page,
        page_size: 200,
        total_pages: Math.ceil(source.length / 200),
      };
    });
    vi.mocked(api.getPendingMergesSnapshot).mockResolvedValue({
      merges: contracted,
      total: contracted.length,
    });

    render(<PendingMergesPage />);
    await screen.findByText('Stream 1');
    fireEvent.click(screen.getByRole('button', { name: /^Merge all$/i }));

    const dialog = await screen.findByRole('dialog', { name: /Confirm bulk action/i });
    expect(within(dialog).getByText(/301 pending merges/i)).toBeInTheDocument();
    expect(screen.getByText('Stream 250')).toBeInTheDocument();
    expect(api.acceptPendingMerge).not.toHaveBeenCalled();
  });

  it('fails closed when the snapshot endpoint rejects an unstable queue', async () => {
    let pass = 0;
    vi.mocked(api.getPendingMerges).mockImplementation(async (params) => {
      if (params?.pageSize !== 200) {
        return {
          merges: [makeRecord()],
          total: 2,
          page: 1,
          page_size: 50,
          total_pages: 1,
        };
      }
      pass += 1;
      return {
        merges: [
          makeRecord({
            id: pass % 2 === 0 ? 2 : 1,
            stream_name: pass % 2 === 0 ? 'Stream 2' : 'Stream 1',
          }),
        ],
        total: 1,
        page: 1,
        page_size: 200,
        total_pages: 1,
      };
    });
    vi.mocked(api.getPendingMergesSnapshot).mockRejectedValue(
      new Error(
        'The pending merges queue kept changing while ECM prepared this action. Nothing was changed.',
      ),
    );

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Merge all$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /queue kept changing.*Nothing was changed/i,
    );
    expect(screen.queryByRole('dialog', { name: /Confirm bulk action/i })).toBeNull();
    expect(api.acceptPendingMerge).not.toHaveBeenCalled();
    expect(api.dismissPendingMerge).not.toHaveBeenCalled();
    expect(api.getPendingMergesSnapshot).toHaveBeenCalledTimes(1);
  });

  it('fails closed when the snapshot endpoint enforces its row safety cap', async () => {
    vi.mocked(api.getPendingMerges).mockImplementation(async (params) => {
      if (params?.pageSize !== 200) {
        return {
          merges: [makeRecord()],
          total: 20_001,
          page: 1,
          page_size: 50,
          total_pages: 401,
        };
      }
      return {
        merges: [makeRecord()],
        total: 20_001,
        page: 1,
        page_size: 200,
        total_pages: 101,
      };
    });
    vi.mocked(api.getPendingMergesSnapshot).mockRejectedValue(
      new Error(
        'Pending merge snapshot exceeds the safety limit of 20000 records. Nothing was changed.',
      ),
    );

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Clear all$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /exceeds the safety limit.*Nothing was changed/i,
    );
    expect(api.getPendingMergesSnapshot).toHaveBeenCalledTimes(1);
    expect(api.dismissPendingMerge).not.toHaveBeenCalled();
  });

  it('focuses Cancel, traps Tab both ways, cancels on Escape, and restores the trigger', async () => {
    mockRows();
    const user = userEvent.setup();
    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    const trigger = screen.getByRole('button', { name: /^Clear all$/i });

    await user.click(trigger);
    const dialog = await screen.findByRole('dialog', { name: /Confirm bulk action/i });
    const close = within(dialog).getByRole('button', { name: /^Close$/i });
    const cancel = within(dialog).getByRole('button', { name: /^Cancel$/i });
    const confirm = within(dialog).getByRole('button', { name: /^Confirm clear$/i });
    expect(cancel).toHaveFocus();

    confirm.focus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(confirm).toHaveFocus();

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(dialog).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
    expect(api.dismissPendingMerge).not.toHaveBeenCalled();
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

  it('restores the original paginated view when a 51-row all action is cancelled', async () => {
    const allRows = Array.from({ length: 51 }, (_, index) =>
      makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
    );
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: allRows.slice(0, 50), total: 51, page: 1, page_size: 50, total_pages: 2,
    });
    vi.mocked(api.getPendingMergesSnapshot).mockResolvedValue({
      merges: allRows, total: 51,
    });
    render(<PendingMergesPage />);
    await screen.findByText('Stream 1');
    fireEvent.click(screen.getByRole('button', { name: /^Merge all$/i }));
    const dialog = await screen.findByRole('dialog', { name: /Confirm bulk action/i });
    expect(screen.getByText('Stream 51')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: /^Cancel$/i }));
    await waitFor(() => expect(screen.queryByText('Stream 51')).toBeNull());
    expect(screen.getAllByRole('checkbox')).toHaveLength(50);
    expect(api.acceptPendingMerge).not.toHaveBeenCalled();
  });

  it('shows loading on the initiating all-queue control while the snapshot loads', async () => {
    mockRows();
    let resolveSnapshot!: (value: { merges: PendingMergeRecord[]; total: number }) => void;
    vi.mocked(api.getPendingMergesSnapshot).mockImplementation(
      () => new Promise((resolve) => { resolveSnapshot = resolve; }),
    );
    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Merge all$/i }));
    expect(await screen.findByRole('button', { name: /Loading all/i })).toBeDisabled();
    await act(async () => resolveSnapshot({
      merges: [makeRecord()], total: 1,
    }));
    expect(await screen.findByRole('dialog', { name: /Confirm bulk action/i }))
      .toBeInTheDocument();
  });

  it('refreshes an active off-page selection with one coherent snapshot call', async () => {
    const allRows = Array.from({ length: 51 }, (_, index) =>
      makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
    );
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: allRows.slice(0, 50), total: 51, page: 1, page_size: 50, total_pages: 2,
    });
    vi.mocked(api.getPendingMergesSnapshot).mockResolvedValue({
      merges: allRows, total: 51,
    });
    render(<PendingMergesPage />);
    await screen.findByText('Stream 1');
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select Stream 1' }));
    vi.mocked(api.getPendingMerges).mockClear();
    vi.mocked(api.getPendingMergesSnapshot).mockClear();
    vi.mocked(api.getPendingMergesSnapshot).mockResolvedValue({
      merges: allRows, total: 777,
    });
    fireEvent.click(screen.getByRole('button', { name: /Refresh/i }));
    await waitFor(() => expect(api.getPendingMergesSnapshot).toHaveBeenCalledTimes(1));
    expect(api.getPendingMerges).not.toHaveBeenCalled();
    expect(screen.getByRole('checkbox', { name: 'Select Stream 1' })).toBeChecked();
    expect(screen.getByText('Stream 51')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Select all$/i })).toBeEnabled();
  });

  it('bounds snapshot DOM rendering to 200 rows while retaining the full target count', async () => {
    const allRows = Array.from({ length: 20_000 }, (_, index) =>
      makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
    );
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: allRows.slice(0, 50), total: 20_000, page: 1,
      page_size: 50, total_pages: 400,
    });
    vi.mocked(api.getPendingMergesSnapshot).mockResolvedValue({
      merges: allRows, total: 20_000,
    });
    render(<PendingMergesPage />);
    await screen.findByText('Stream 1');
    fireEvent.click(screen.getByRole('button', { name: /^Merge all$/i }));
    const dialog = await screen.findByRole('dialog', { name: /Confirm bulk action/i });
    expect(within(dialog).getByText(/20000 pending merges/i)).toBeInTheDocument();
    expect(screen.getAllByRole('checkbox')).toHaveLength(200);
    expect(screen.queryByText('Stream 201')).toBeNull();
  });

  it('keeps a failure at id 250 visible, selected, and retryable', async () => {
    const allRows = Array.from({ length: 260 }, (_, index) =>
      makeRecord({ id: index + 1, stream_name: `Stream ${index + 1}` }),
    );
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: allRows.slice(0, 50), total: 260, page: 1, page_size: 50, total_pages: 6,
    });
    vi.mocked(api.getPendingMergesSnapshot).mockResolvedValue({
      merges: allRows, total: 260,
    });
    vi.mocked(api.acceptPendingMerge).mockImplementation(async (id) => {
      if (id === 250) throw new Error('Row 250 changed');
      return {
        merged_into_channel_id: 'channel-uuid-abc', journal_entry_id: id,
        source_stream_id: `stream-${id}`, confidence: 0.92, status: 'merged',
      };
    });
    render(<PendingMergesPage />);
    await screen.findByText('Stream 1');
    await startBulk(/^Merge all$/i);
    await waitFor(() => expect(api.acceptPendingMerge).toHaveBeenCalledTimes(260));
    expect(screen.getByText('Stream 250')).toBeInTheDocument();
    expect(screen.getByText('Row 250 changed')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Select Stream 250' })).toBeChecked();
    const failedRow = screen.getByText('Stream 250').closest('.pending-merges-row')!;
    expect(within(failedRow as HTMLElement).getByRole('button', { name: 'Merge' }))
      .toBeEnabled();
  });
});
