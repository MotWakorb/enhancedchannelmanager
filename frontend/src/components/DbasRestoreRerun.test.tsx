/**
 * Re-running a preview inside one modal session (bead dfkbn, review round 4).
 *
 * These use the REAL `useRestoreProgress`, mocking only the API. Every other
 * modal test replaces the hook with a static view object, which is precisely why
 * the defect below survived four review rounds: it lives in the hook's poll
 * effect and in how the modals key it.
 *
 * The defect: both restore endpoints return the CONSTANT task id
 * `"dbas_restore"`, so on a second run the hook's poll effect saw unchanged deps
 * (`[taskId, pollIntervalMs]`), never restarted, and kept serving the previous
 * run's terminal view. The modals' finalize effect fires on `progress.isComplete`
 * the instant `step` flips to `'restoring'`, so it read `/task-history?limit=1`
 * while that still held the PREVIOUS run's row. The operator clicked
 * "Back to options", switched to Replace, re-previewed — and got the old
 * `preserve` preview rendered verbatim, reassuring copy and all, above an enabled
 * Apply button that would then send `overwrite`. Under-reporting the destructive
 * count immediately before a one-way door.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

vi.mock('../services/api', () => ({
  getSettings: vi.fn(),
  startDbasRestore: vi.fn(),
  restoreDbasBackupSaved: vi.fn(),
  getTaskHistory: vi.fn(),
  getTask: vi.fn(),
}));
vi.mock('../hooks/useNavigateAwayGuard', () => ({ useNavigateAwayGuard: () => {} }));

import * as api from '../services/api';
import { DbasRestoreModal } from './DbasRestoreModal';
import { DbasRestoreSavedModal } from './DbasRestoreSavedModal';

const FILENAME = 'ecm-backup-2026-01-01_000000.zip';

/** A completed task payload. `started_at` is what distinguishes one run from the next. */
function completedTask(startedAt: string) {
  return {
    task_id: 'dbas_restore',
    task_name: 'DBAS Restore',
    progress: {
      total: 13, current: 13, percentage: 100, status: 'completed',
      current_item: 'finalize', success_count: 1, failed_count: 0,
      skipped_count: 0, started_at: startedAt,
    },
  };
}

/** A dry-run report whose reattach split is unmistakably one mode or the other. */
function reportFor(mode: 'preserve' | 'overwrite') {
  const preserving = mode === 'preserve';
  return {
    contract_version: 1, is_dry_run: true, outcome: null,
    categories: [], logo_misses: 0, notes: [],
    epg_link_reattach: {
      mode,
      created_channels: 0,
      existing_channels: preserving ? 0 : 2,
      preserved_channels: preserving ? 2 : 0,
      existing_channels_named: preserving ? [] : ['FOX News', 'CNN'],
      preserved_channels_named: preserving ? ['FOX News', 'CNN'] : [],
    },
  };
}

/**
 * Burn the hook's run-start budget on FAKE time.
 *
 * The budget is MAX_RUN_START_POLLS (20) polls at the default 1s cadence, and
 * the modals do not expose the cadence. Waiting it out for real would add 40
 * seconds to this suite for a branch whose arithmetic is already pinned in
 * `useRestoreProgress.test.ts`; what these tests are about is what the MODAL
 * does once it fires.
 */
async function burnRunStartBudget(startTheRun: () => void, assertions: () => void) {
  vi.useFakeTimers();
  try {
    // Installed BEFORE the run: the poll loop schedules its first sleep as soon
    // as the run starts, and a sleep queued on the real clock would never be
    // advanced by the fake one.
    startTheRun();
    // Advance one poll interval at a time. Each iteration has to let React
    // settle the setState the poll produced before the next timer fires, so a
    // single 30s jump does not drive the loop.
    for (let i = 0; i < 30; i += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    }
    assertions();
    // The give-up state must PERSIST, not flash while a stale report replaces
    // it a round trip later.
    for (let i = 0; i < 5; i += 1) {
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    }
    assertions();
  } finally {
    vi.useRealTimers();
  }
}

function zip(name: string) {
  return new File([new TextEncoder().encode('PKpayload')], name, {
    type: 'application/zip',
  });
}

const STARTED_AT = ['', '2026-08-05T10:00:00Z', '2026-08-05T11:00:00Z'];

/**
 * A server that behaves like the real one, which is the whole point.
 *
 * `requested` is what the operator has triggered; `executed` is what the backend
 * has actually run. They differ because the trigger endpoint is fire-and-forget:
 * it returns 200 having only scheduled the task. `executed` therefore advances
 * on the first PROGRESS POLL after a trigger, not on the trigger itself.
 *
 * That lag is the whole defect. `/task-history?limit=1` reports the last
 * EXECUTED run, so a modal that does not re-poll reads run 1's row for run 2 and
 * renders run 1's report. A fixture where history tracked the REQUESTED run
 * would hand back run 2's answer to a modal that never asked, and quietly pass
 * on the broken code — which is exactly what the first cut of this file did.
 */
function wireTwoRuns(trigger: ReturnType<typeof vi.fn>) {
  const server = { requested: 0, executed: 0 };
  trigger.mockImplementation(async () => {
    server.requested += 1;
    return { status: 'started', task_id: 'dbas_restore', is_dry_run: true };
  });
  (api.getTask as ReturnType<typeof vi.fn>).mockImplementation(async () => {
    server.executed = server.requested;
    return completedTask(STARTED_AT[server.executed]);
  });
  (api.getTaskHistory as ReturnType<typeof vi.fn>).mockImplementation(async () => ({
    history: [
      {
        status: 'completed',
        details: {
          restore_report: reportFor(server.executed === 1 ? 'preserve' : 'overwrite'),
        },
      },
    ],
  }));
  return server;
}

describe('Re-running a preview in one modal session', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.getSettings as ReturnType<typeof vi.fn>).mockResolvedValue({ url: 'http://d:1' });
  });

  it('DbasRestoreModal: the second preview reflects the NEW mode, not the first run', async () => {
    const trigger = api.startDbasRestore as ReturnType<typeof vi.fn>;
    wireTwoRuns(trigger);

    render(<DbasRestoreModal onClose={vi.fn()} />);
    const dz = screen.getByText(/drag & drop/i).closest('div')!;
    fireEvent.drop(dz, { dataTransfer: { files: [zip('backup.zip')] } });
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());

    // --- Run 1, under the default (preserve). ---
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));
    await waitFor(() =>
      expect(screen.getByTestId('existing-channel-reattach-notice').textContent)
        .toMatch(/would be left alone/i),
    );
    expect(trigger).toHaveBeenLastCalledWith(
      expect.any(File), false, undefined, 'preserve',
    );

    // --- Back, switch to the destructive mode, run again. ---
    fireEvent.click(screen.getByRole('button', { name: /back to options/i }));
    await waitFor(() =>
      expect(screen.getByLabelText(/replace their guide data and logos/i))
        .toBeInTheDocument(),
    );
    fireEvent.click(screen.getByLabelText(/replace their guide data and logos/i));
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(trigger).toHaveBeenLastCalledWith(
        expect.any(File), false, undefined, 'overwrite',
      ),
    );

    // THE bar: the preview on screen must describe the run that just happened.
    await waitFor(() => {
      const notice = screen.getByTestId('existing-channel-reattach-notice');
      expect(notice.textContent).toMatch(/would replace on/i);
      expect(notice.textContent).toMatch(/FOX News/);
      expect(notice).toHaveAttribute('role', 'alert');
    });
    const notice = screen.getByTestId('existing-channel-reattach-notice');
    expect(notice.textContent).not.toMatch(/would be left alone/i);
    expect(notice.textContent).not.toMatch(/would leave/i);

    // And it genuinely re-read the run's outcome rather than reusing run 1's.
    expect((api.getTaskHistory as ReturnType<typeof vi.fn>).mock.calls.length)
      .toBeGreaterThanOrEqual(2);
  });

  it('DbasRestoreSavedModal: the second preview reflects the NEW mode too', async () => {
    const trigger = api.restoreDbasBackupSaved as ReturnType<typeof vi.fn>;
    wireTwoRuns(trigger);

    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));
    await waitFor(() =>
      expect(screen.getByTestId('existing-channel-reattach-notice').textContent)
        .toMatch(/would be left alone/i),
    );
    expect(trigger).toHaveBeenLastCalledWith(FILENAME, false, undefined, 'preserve');

    fireEvent.click(screen.getByRole('button', { name: /back to options/i }));
    await waitFor(() =>
      expect(screen.getByLabelText(/replace their guide data and logos/i))
        .toBeInTheDocument(),
    );
    fireEvent.click(screen.getByLabelText(/replace their guide data and logos/i));
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(trigger).toHaveBeenLastCalledWith(FILENAME, false, undefined, 'overwrite'),
    );
    await waitFor(() =>
      expect(screen.getByTestId('existing-channel-reattach-notice').textContent)
        .toMatch(/would replace on/i),
    );
    expect(screen.getByTestId('existing-channel-reattach-notice').textContent)
      .not.toMatch(/would be left alone/i);
  });

  it('never publishes a terminal state that belongs to the PREVIOUS run', async () => {
    // The narrower race under the same defect. The trigger endpoint is
    // fire-and-forget, so for a moment after it returns 200 the task still
    // reports the previous run's finished progress. Here the first two polls of
    // run 2 return run 1's payload verbatim; only the third carries a new
    // started_at. A hook that published on the first would show run 1's report.
    const trigger = api.startDbasRestore as ReturnType<typeof vi.fn>;
    const server = { requested: 0, executed: 0 };
    let pollsInRun2 = 0;
    trigger.mockImplementation(async () => {
      server.requested += 1;
      return { status: 'started', task_id: 'dbas_restore', is_dry_run: true };
    });
    (api.getTask as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      if (server.requested === 1) {
        server.executed = 1;
        return completedTask(STARTED_AT[1]);
      }
      pollsInRun2 += 1;
      // A LONGER lag: the scheduled run has not begun, so the task still reports
      // run 1's finished progress for the first two polls.
      if (pollsInRun2 <= 2) return completedTask(STARTED_AT[1]);
      server.executed = 2;
      return completedTask(STARTED_AT[2]);
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockImplementation(async () => ({
      history: [
        {
          status: 'completed',
          details: {
            restore_report: reportFor(server.executed === 1 ? 'preserve' : 'overwrite'),
          },
        },
      ],
    }));

    render(<DbasRestoreModal onClose={vi.fn()} />);
    const dz = screen.getByText(/drag & drop/i).closest('div')!;
    fireEvent.drop(dz, { dataTransfer: { files: [zip('backup.zip')] } });
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));
    await waitFor(() =>
      expect(screen.getByTestId('existing-channel-reattach-notice')).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /back to options/i }));
    await waitFor(() =>
      expect(screen.getByLabelText(/replace their guide data and logos/i))
        .toBeInTheDocument(),
    );
    fireEvent.click(screen.getByLabelText(/replace their guide data and logos/i));
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    // It waited out the stale polls and landed on run 2's report. Three polls
    // at the default 1s cadence, so ~3s; the waitFor budget sits above that and
    // the per-test timeout above THAT, so a failure surfaces as this assertion
    // rather than as an opaque "Test timed out".
    await waitFor(
      () =>
        expect(screen.getByTestId('existing-channel-reattach-notice').textContent)
          .toMatch(/would replace on/i),
      { timeout: 8000 },
    );
    expect(pollsInRun2).toBeGreaterThan(2);
  }, 20000);

  // --- The run never starts at all (review round 5, finding F1) -------------
  //
  // The ALREADY_RUNNING path: the trigger returns 200, no new run is scheduled,
  // and the task keeps reporting run 1's terminal progress forever. The progress
  // hook eventually gives up — but the modals' finalize effect fires on isError
  // exactly as on isComplete, and then read the UNVERSIONED
  // /task-history?limit=1, which still holds run 1's completed row. So the guard
  // handed the stale report straight back: run 1's PRESERVE report rendered as
  // the result of an OVERWRITE preview, above an enabled Apply.

  function wireRunThatNeverStarts(trigger: ReturnType<typeof vi.fn>) {
    const server = { requested: 0, executed: 0 };
    trigger.mockImplementation(async () => {
      server.requested += 1;
      return { status: 'started', task_id: 'dbas_restore', is_dry_run: true };
    });
    (api.getTask as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      // Only the FIRST run ever executes. Run 2 is rejected upstream, so the
      // task's progress never moves off run 1's finished state.
      if (server.executed === 0) server.executed = 1;
      return completedTask(STARTED_AT[1]);
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockImplementation(async () => ({
      history: [
        { status: 'completed', details: { restore_report: reportFor('preserve') } },
      ],
    }));
    return server;
  }

  it('DbasRestoreModal: a run that never starts says so, and never shows the old report',
    async () => {
      const trigger = api.startDbasRestore as ReturnType<typeof vi.fn>;
      wireRunThatNeverStarts(trigger);

      render(<DbasRestoreModal onClose={vi.fn()} />);
      const dz = screen.getByText(/drag & drop/i).closest('div')!;
      fireEvent.drop(dz, { dataTransfer: { files: [zip('backup.zip')] } });
      await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());

      // Run 1 under the default, so a stale render is unmistakably reassuring copy.
      fireEvent.click(screen.getByRole('button', { name: /run preview/i }));
      await waitFor(() =>
        expect(screen.getByTestId('existing-channel-reattach-notice')).toBeInTheDocument(),
      );
      const historyCallsAfterRun1 =
        (api.getTaskHistory as ReturnType<typeof vi.fn>).mock.calls.length;

      // Back, switch to the destructive mode, run again — and it never starts.
      fireEvent.click(screen.getByRole('button', { name: /back to options/i }));
      await waitFor(() =>
        expect(screen.getByLabelText(/replace their guide data and logos/i))
          .toBeInTheDocument(),
      );
      fireEvent.click(screen.getByLabelText(/replace their guide data and logos/i));

      // The budget is 20 polls at a 1s cadence. Burn it on fake time rather
      // than 20 real seconds; the budget ITSELF is pinned in the hook's own
      // suite, and what this test is about is what the modal does when it fires.
      await burnRunStartBudget(
        () => fireEvent.click(screen.getByRole('button', { name: /run preview/i })),
        () => {
          expect(screen.getByText(/did not start/i)).toBeInTheDocument();
          expect(screen.queryByTestId('existing-channel-reattach-notice')).toBeNull();
          expect(screen.queryByRole('button', { name: /apply these changes/i })).toBeNull();
          // And it never went looking for a result that does not exist.
          expect((api.getTaskHistory as ReturnType<typeof vi.fn>).mock.calls.length)
            .toBe(historyCallsAfterRun1);
        },
      );
    },
  );

  it('DbasRestoreSavedModal: same, on the saved-file path',
    async () => {
      const trigger = api.restoreDbasBackupSaved as ReturnType<typeof vi.fn>;
      wireRunThatNeverStarts(trigger);

      render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
      await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

      fireEvent.click(screen.getByRole('button', { name: /run preview/i }));
      await waitFor(() =>
        expect(screen.getByTestId('existing-channel-reattach-notice')).toBeInTheDocument(),
      );
      const historyCallsAfterRun1 =
        (api.getTaskHistory as ReturnType<typeof vi.fn>).mock.calls.length;

      fireEvent.click(screen.getByRole('button', { name: /back to options/i }));
      await waitFor(() =>
        expect(screen.getByLabelText(/replace their guide data and logos/i))
          .toBeInTheDocument(),
      );
      fireEvent.click(screen.getByLabelText(/replace their guide data and logos/i));

      await burnRunStartBudget(
        () => fireEvent.click(screen.getByRole('button', { name: /run preview/i })),
        () => {
          expect(screen.getByText(/did not start/i)).toBeInTheDocument();
          expect(screen.queryByTestId('existing-channel-reattach-notice')).toBeNull();
          expect((api.getTaskHistory as ReturnType<typeof vi.fn>).mock.calls.length)
            .toBe(historyCallsAfterRun1);
        },
      );
    },
  );

  it('does not spend the run-start budget on the trigger request itself', async () => {
    // F2: the run token used to advance in the handler's synchronous prologue,
    // so on any second-or-later run the poll effect restarted while the trigger
    // POST was still in flight. The budget that exists to cover the scheduler's
    // fire-and-forget lag was spent on the upload instead.
    const trigger = api.startDbasRestore as ReturnType<typeof vi.fn>;
    const server = { requested: 0, executed: 0 };
    let releaseSecondTrigger: (() => void) | null = null;
    let pollsDuringTrigger = 0;
    let triggerInFlight = false;

    trigger.mockImplementation(async () => {
      server.requested += 1;
      if (server.requested === 2) {
        triggerInFlight = true;
        await new Promise<void>((resolve) => { releaseSecondTrigger = resolve; });
        triggerInFlight = false;
      }
      return { status: 'started', task_id: 'dbas_restore', is_dry_run: true };
    });
    (api.getTask as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      if (triggerInFlight) pollsDuringTrigger += 1;
      server.executed = server.requested;
      return completedTask(STARTED_AT[server.executed]);
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockImplementation(async () => ({
      history: [
        {
          status: 'completed',
          details: {
            restore_report: reportFor(server.executed === 1 ? 'preserve' : 'overwrite'),
          },
        },
      ],
    }));

    render(<DbasRestoreModal onClose={vi.fn()} />);
    const dz = screen.getByText(/drag & drop/i).closest('div')!;
    fireEvent.drop(dz, { dataTransfer: { files: [zip('backup.zip')] } });
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));
    await waitFor(() =>
      expect(screen.getByTestId('existing-channel-reattach-notice')).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /back to options/i }));
    await waitFor(() =>
      expect(screen.getByLabelText(/replace their guide data and logos/i))
        .toBeInTheDocument(),
    );
    fireEvent.click(screen.getByLabelText(/replace their guide data and logos/i));
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    // Hold the trigger open well past several poll intervals.
    await waitFor(() => expect(trigger).toHaveBeenCalledTimes(2));
    await new Promise((r) => setTimeout(r, 2500));
    expect(pollsDuringTrigger).toBe(0);

    releaseSecondTrigger!();
    await waitFor(() =>
      expect(screen.getByTestId('existing-channel-reattach-notice').textContent)
        .toMatch(/would replace on/i),
    );
  }, 20000);
});
