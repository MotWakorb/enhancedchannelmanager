/**
 * A merge the backend could not apply upstream is not shown as a success.
 *
 * Bead `enhancedchannelmanager-i5ic0`. `handleAction` treated ANY non-throwing
 * response as success: the row was optimistically removed, no error was shown,
 * and the operator was told the merge was confirmed — while Dispatcharr may
 * never have been updated, because the stream-name lookup matched zero streams,
 * several streams, or could not establish an answer at all.
 *
 * The backend now answers `dispatcharr_updated: false` with an actionable
 * `unapplied_reason` — and, since the PO decision of 2026-08-16, it also LEAVES
 * THE ROW IN THE QUEUE, flagged, so the merge stays in front of the operator
 * and stays retryable. An earlier round of this fix removed the row and kept
 * only a page-level notice; the reason outlived the row, but only where an
 * operator who went looking would find it.
 *
 * What this pins:
 *
 *  1. An unapplied merge raises a persistent, operator-readable notice naming
 *     the stream and the reason. It is not an error banner — nothing failed.
 *  2. An applied merge raises nothing, or the notice carries no information.
 *  3. A bulk run counts unapplied merges separately from failures and from
 *     successes, and every one of them is named.
 *  4. The row STAYS, badged "Not applied" and carrying its reason, in the
 *     single-row and the bulk path alike — and a retry that lands removes it.
 *     Removing it on the unapplied outcome would put the list out of step with
 *     a `status=pending` reload and take the retry away.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act, within } from '@testing-library/react';
import { PendingMergesPage } from './PendingMergesPage';
import type { AcceptMergeOutcome, PendingMergeRecord } from '../../services/api';
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
    unapplied_reason: null,
    ...overrides,
  };
}

function outcome(overrides: Partial<AcceptMergeOutcome> = {}): AcceptMergeOutcome {
  return {
    merged_into_channel_id: 'channel-uuid-abc',
    journal_entry_id: 100,
    source_stream_id: 'stream-uuid-xyz',
    confidence: 0.92,
    status: 'merged',
    dispatcharr_updated: true,
    unapplied_reason: null,
    journal_rows_unwritten: 0,
    ...overrides,
  };
}

const NO_MATCH =
  'No Dispatcharr stream is named "ESPN HD", so this merge was recorded but ' +
  'NOT applied upstream.';

function listOf(records: PendingMergeRecord[]) {
  return {
    merges: records,
    total: records.length,
    page: 1,
    page_size: 50,
    total_pages: 1,
  };
}

describe('PendingMergesPage — a merge that was not applied upstream', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getPendingMerges).mockResolvedValue(listOf([makeRecord()]));
  });

  it('surfaces the reason instead of reporting a silent success', async () => {
    vi.mocked(api.acceptPendingMerge).mockResolvedValue(
      outcome({ dispatcharr_updated: false, unapplied_reason: NO_MATCH }),
    );

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));

    const notice = await screen.findByTestId('pending-merges-unapplied');
    expect(notice).toHaveTextContent(NO_MATCH);
    // Names WHICH merge: after a bulk run over a long queue the flagged rows
    // are not necessarily on screen, so the summary has to identify them.
    expect(notice).toHaveTextContent('ESPN HD');
  });

  it('keeps the row in the queue, flagged and retryable', async () => {
    vi.mocked(api.acceptPendingMerge).mockResolvedValue(
      outcome({
        status: 'pending',
        dispatcharr_updated: false,
        unapplied_reason: NO_MATCH,
      }),
    );

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));

    await waitFor(() => {
      expect(screen.getByTestId('pending-merges-unapplied')).toBeInTheDocument();
    });
    // The backend left the row in `pending`, so the list has to as well — and
    // its Merge button is the retry the decision exists to preserve.
    expect(screen.getByRole('button', { name: /^Merge$/i })).toBeEnabled();
    expect(screen.getByTestId('pending-merges-row-unapplied')).toHaveTextContent(
      'Not applied',
    );
    expect(screen.getByTestId('pending-merges-unapplied')).toHaveTextContent(
      NO_MATCH,
    );
  });

  it('carries the reason on the row, not only in the page notice', async () => {
    vi.mocked(api.acceptPendingMerge).mockResolvedValue(
      outcome({
        status: 'pending',
        dispatcharr_updated: false,
        unapplied_reason: NO_MATCH,
      }),
    );

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));

    await screen.findByTestId('pending-merges-row-unapplied');
    // Dismissing the page notice must not take the explanation with it — the
    // notice is a summary of the last action, the row is the record.
    fireEvent.click(
      within(screen.getByTestId('pending-merges-unapplied')).getByRole(
        'button',
        { name: /^Dismiss$/i },
      ),
    );
    await waitFor(() => {
      expect(screen.queryByTestId('pending-merges-unapplied')).toBeNull();
    });
    expect(screen.getByText(new RegExp(NO_MATCH.slice(0, 40)))).toBeInTheDocument();
  });

  it('removes the row when a retry finally lands', async () => {
    vi.mocked(api.acceptPendingMerge)
      .mockResolvedValueOnce(
        outcome({
          status: 'pending',
          dispatcharr_updated: false,
          unapplied_reason: NO_MATCH,
        }),
      )
      .mockResolvedValueOnce(outcome());

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));
    await screen.findByTestId('pending-merges-row-unapplied');

    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));

    // A retry that applies is an ordinary accept: the row resolves and goes.
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /^Merge$/i })).toBeNull();
    });
    expect(api.acceptPendingMerge).toHaveBeenCalledTimes(2);
  });

  it('shows nothing when the merge really was applied', async () => {
    vi.mocked(api.acceptPendingMerge).mockResolvedValue(outcome());

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));

    await waitFor(() => {
      expect(api.acceptPendingMerge).toHaveBeenCalledWith(1);
    });
    expect(screen.queryByTestId('pending-merges-unapplied')).toBeNull();
  });

  it('does not treat an unapplied merge as a failed one', async () => {
    vi.mocked(api.acceptPendingMerge).mockResolvedValue(
      outcome({ dispatcharr_updated: false, unapplied_reason: NO_MATCH }),
    );

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));

    await screen.findByTestId('pending-merges-unapplied');
    // Nothing failed: the request succeeded and the decision was recorded. An
    // error banner would describe a request that did not do what it was asked,
    // which is not this one.
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('counts and names every unapplied merge in a bulk run', async () => {
    const records = [
      makeRecord({ id: 1, stream_name: 'ESPN HD' }),
      makeRecord({ id: 2, stream_name: 'CNN HD' }),
      makeRecord({ id: 3, stream_name: 'TNT HD' }),
    ];
    vi.mocked(api.getPendingMerges).mockResolvedValue(listOf(records));
    // "Merge all" materialises the whole queue through the snapshot endpoint
    // before it confirms.
    vi.mocked(api.getPendingMergesSnapshot).mockResolvedValue({
      merges: records,
      total: records.length,
    });
    vi.mocked(api.acceptPendingMerge).mockImplementation(async (id) =>
      id === 2
        ? outcome()
        : outcome({
            dispatcharr_updated: false,
            unapplied_reason: `Nothing matched for row ${id}.`,
          }),
    );

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Merge all$/i }));
    });
    const dialog = await screen.findByRole('dialog', {
      name: /Confirm bulk action/i,
    });
    await act(async () => {
      fireEvent.click(
        within(dialog).getByRole('button', { name: /^Confirm merge$/i }),
      );
    });

    const notice = await screen.findByTestId('pending-merges-unapplied');
    await waitFor(() => {
      expect(notice).toHaveTextContent('TNT HD');
    });
    expect(notice).toHaveTextContent('ESPN HD');
    // The one that DID apply is not listed — the notice must be able to be
    // shorter than the batch or it says nothing about which rows to chase.
    expect(notice).not.toHaveTextContent('CNN HD');

    // Reported apart from failures: nothing failed here.
    const progress = screen.getByLabelText('Bulk action progress');
    expect(progress).not.toHaveTextContent(/failure/i);
    expect(progress).toHaveTextContent('2 not applied');

    // The two unapplied rows STAY, flagged, and the applied one goes. A bulk
    // run that dropped them would leave the operator with a count and nothing
    // to retry — the same rule as the single-row path, deliberately.
    expect(screen.getAllByTestId('pending-merges-row-unapplied')).toHaveLength(2);
    // Scoped to the queue list: the page notice names the same streams, and an
    // unscoped query would pass on the notice alone — which is the state this
    // assertion exists to rule out.
    const queue = within(screen.getByRole('list', { name: 'Pending merges' }));
    expect(queue.getByText('ESPN HD')).toBeInTheDocument();
    expect(queue.getByText('TNT HD')).toBeInTheDocument();
    expect(queue.queryByText('CNN HD')).toBeNull();
  });
});

/**
 * `dispatcharr_updated` is THREE values, and the third had no consumer.
 *
 * Fix round on the same bead. The backend deliberately answers `null` for an
 * idempotent replay: that request made no Dispatcharr call, so it has no
 * evidence about what the original one did, and guessing `true` would be the
 * same false success claim one branch over. The page tested
 * `dispatcharr_updated !== false` and so consumed `null` through the ordinary
 * success path — row removed, no persistent explanation, and in a bulk run
 * neither `failures` nor `unapplied` incremented. The one outcome that says
 * "ECM does not know" was the one outcome shown as certainty.
 *
 * This need not count as a failure; it must not pass as a plain success. What
 * is pinned here is that `true`, `false` and `null` reach three distinct
 * consumer paths, in the single-row and the bulk consumer alike — and that
 * `undefined` (a dismiss outcome, which carries no such field at all) is not
 * dragged into the new one.
 */
describe('PendingMergesPage — a replay that obtained no evidence', () => {
  const REPLAY_REASON =
    'This merge was already resolved by an earlier request, which this one ' +
    'replayed without contacting Dispatcharr.';

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getPendingMerges).mockResolvedValue(listOf([makeRecord()]));
  });

  it('raises its own notice rather than the not-applied one', async () => {
    vi.mocked(api.acceptPendingMerge).mockResolvedValue(
      outcome({ dispatcharr_updated: null, unapplied_reason: REPLAY_REASON }),
    );

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));

    const notice = await screen.findByTestId('pending-merges-unknown');
    expect(notice).toHaveTextContent(REPLAY_REASON);
    expect(notice).toHaveTextContent('ESPN HD');
    // Not the "recorded but not applied" notice: that asserts the upstream
    // write did NOT happen, which this request also has no evidence for.
    expect(screen.queryByTestId('pending-merges-unapplied')).toBeNull();
  });

  it('keeps the notice after the row has left the queue', async () => {
    vi.mocked(api.acceptPendingMerge).mockResolvedValue(
      outcome({ dispatcharr_updated: null, unapplied_reason: REPLAY_REASON }),
    );

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /^Merge$/i })).toBeNull();
    });
    expect(screen.getByTestId('pending-merges-unknown')).toHaveTextContent(
      REPLAY_REASON,
    );
  });

  it('does not report it as a failure', async () => {
    vi.mocked(api.acceptPendingMerge).mockResolvedValue(
      outcome({ dispatcharr_updated: null, unapplied_reason: REPLAY_REASON }),
    );

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Merge$/i }));

    await screen.findByTestId('pending-merges-unknown');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('leaves a dismiss, which carries no such field, on the plain path', async () => {
    vi.mocked(api.dismissPendingMerge).mockResolvedValue({
      journal_entry_id: 7,
      status: 'dismissed',
    });

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    fireEvent.click(screen.getByRole('button', { name: /^Create New$/i }));

    await waitFor(() => {
      expect(api.dismissPendingMerge).toHaveBeenCalledWith(1);
    });
    // An absent field is not the same as `null`: the dismiss endpoint makes no
    // claim about Dispatcharr because none is in scope for it.
    expect(screen.queryByTestId('pending-merges-unknown')).toBeNull();
    expect(screen.queryByTestId('pending-merges-unapplied')).toBeNull();
  });

  it('counts replays apart from successes, failures and unapplied merges in a bulk run', async () => {
    const records = [
      makeRecord({ id: 1, stream_name: 'ESPN HD' }),
      makeRecord({ id: 2, stream_name: 'CNN HD' }),
      makeRecord({ id: 3, stream_name: 'TNT HD' }),
    ];
    vi.mocked(api.getPendingMerges).mockResolvedValue(listOf(records));
    vi.mocked(api.getPendingMergesSnapshot).mockResolvedValue({
      merges: records,
      total: records.length,
    });
    vi.mocked(api.acceptPendingMerge).mockImplementation(async (id) => {
      if (id === 1) return outcome();
      if (id === 2) {
        return outcome({
          dispatcharr_updated: false,
          unapplied_reason: 'Nothing matched for CNN HD.',
        });
      }
      return outcome({
        dispatcharr_updated: null,
        unapplied_reason: REPLAY_REASON,
      });
    });

    render(<PendingMergesPage />);
    await screen.findByText('ESPN HD');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Merge all$/i }));
    });
    const dialog = await screen.findByRole('dialog', {
      name: /Confirm bulk action/i,
    });
    await act(async () => {
      fireEvent.click(
        within(dialog).getByRole('button', { name: /^Confirm merge$/i }),
      );
    });

    const unknown = await screen.findByTestId('pending-merges-unknown');
    await waitFor(() => {
      expect(unknown).toHaveTextContent('TNT HD');
    });
    // Three outcomes, three destinations: the applied one is named nowhere,
    // the unapplied one only in its own notice, the replay only in this one.
    expect(unknown).not.toHaveTextContent('CNN HD');
    expect(unknown).not.toHaveTextContent('ESPN HD');
    expect(screen.getByTestId('pending-merges-unapplied')).toHaveTextContent(
      'CNN HD',
    );

    const progress = screen.getByLabelText('Bulk action progress');
    expect(progress).not.toHaveTextContent(/failure/i);
    expect(progress).toHaveTextContent('1 not applied');
    expect(progress).toHaveTextContent('1 already resolved');
  });
});
