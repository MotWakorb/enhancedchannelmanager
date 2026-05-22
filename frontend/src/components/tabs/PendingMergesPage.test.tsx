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
 *   - Bulk-action buttons ("Resolve All ...") are NOT rendered — they were
 *     deferred to backlog bead enhancedchannelmanager-qpgsx per the PO.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

  it('does NOT render any bulk-action buttons (deferred to backlog bead qpgsx)', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [makeRecord()],
      total: 1,
      page: 1,
      page_size: 50,
      total_pages: 1,
    });

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');

    // "Resolve All" buttons were explicitly deferred per the parent epic.
    expect(screen.queryByRole('button', { name: /Resolve All/i })).toBeNull();
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
