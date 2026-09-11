/** Actual hook/modal seam: a remount must not trust another run's terminal payload. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import * as api from '../services/api';
import { DbasRestoreModal } from './DbasRestoreModal';
import { DbasRestoreSavedModal } from './DbasRestoreSavedModal';
import { invalidateServerData } from '../hooks/useServerDataInvalidation';

vi.mock('../services/api', () => ({
  getSettings: vi.fn(), startDbasRestore: vi.fn(), restoreDbasBackupSaved: vi.fn(),
  getTask: vi.fn(), getTaskHistory: vi.fn(),
}));
vi.mock('../hooks/useNavigateAwayGuard', () => ({ useNavigateAwayGuard: () => {} }));
vi.mock('../hooks/useServerDataInvalidation', () => ({ invalidateServerData: vi.fn() }));

function task(runId: string, status = 'completed') {
  return { task_id: 'dbas_restore', progress: {
    run_id: runId, started_at: '2026-01-01T00:00:00Z', status,
    total: 13, current: 13, percentage: 100, current_item: 'finalize',
    success_count: 0, failed_count: 0, skipped_count: 0,
  } };
}
function history(runId: string) {
  return { history: [{ status: 'completed', details: { run_id: runId, restore_report: {
    contract_version: 1, is_dry_run: true, categories: [], logo_misses: 0,
    notes: [], epg_link_reattach: {
      mode: 'overwrite', created_channels: 0, existing_channels: 1, preserved_channels: 0,
      existing_channels_named: [runId === 'current' ? 'CURRENT REPORT' : 'OLD REPORT'],
      preserved_channels_named: [],
    },
  } } }] };
}
async function tick(ms = 1000) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

describe.each(['uploaded', 'saved'] as const)('%s initiated restore correlation', (flow) => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.useFakeTimers();
    vi.mocked(api.getSettings).mockResolvedValue({ url: '' } as never);
    for (const trigger of [api.startDbasRestore, api.restoreDbasBackupSaved]) {
      vi.mocked(trigger).mockResolvedValue({ status: 'started', task_id: 'dbas_restore',
        is_dry_run: true, run_id: 'current' } as never);
    }
  });
  afterEach(() => { vi.useRealTimers(); });

  async function mount() {
    const view = render(flow === 'saved'
      ? <DbasRestoreSavedModal filename="backup.zip" onClose={vi.fn()} />
      : <DbasRestoreModal onClose={vi.fn()} />);
    if (flow === 'uploaded') {
      fireEvent.drop(screen.getByText(/drag & drop/i).closest('div')!, {
        dataTransfer: { files: [new File(['PK'], 'backup.zip')] },
      });
    }
    await tick(0);
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));
    await tick(0);
    return view;
  }

  it('ignores old terminal progress after remount, then waits for matching history', async () => {
    vi.mocked(api.getTask).mockResolvedValue(task('old') as never);
    vi.mocked(api.getTaskHistory).mockResolvedValue(history('old') as never);
    await mount();
    expect(api.getTask).toHaveBeenCalledTimes(1);
    expect(api.getTaskHistory).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /apply these changes/i })).not.toBeInTheDocument();
    vi.mocked(api.getTask).mockResolvedValue(task('current') as never);
    await tick();
    expect(api.getTaskHistory).toHaveBeenCalledTimes(1);
    expect(screen.queryByText('OLD REPORT')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /apply these changes/i })).not.toBeInTheDocument();
    vi.mocked(api.getTaskHistory).mockResolvedValue(history('current') as never);
    await tick(500);
    expect(screen.getByTestId('existing-channel-reattach-notice')).toHaveTextContent('CURRENT REPORT');
    expect(screen.getByRole('button', { name: /apply these changes/i })).toBeEnabled();
  });

  it('unknown progress identity exhausts start budget with zero history reads', async () => {
    vi.mocked(api.getTask).mockResolvedValue(task('') as never);
    await mount();
    for (let i = 0; i < 20; i++) await tick();
    expect(screen.getByText(/restore did not start/i)).toBeInTheDocument();
    expect(api.getTaskHistory).not.toHaveBeenCalled();
  });

  it('suppresses a late history response after unmount', async () => {
    vi.mocked(api.getTask).mockResolvedValue(task('current') as never);
    let resolve!: (value: never) => void;
    vi.mocked(api.getTaskHistory).mockImplementation(() => new Promise((r) => { resolve = r; }));
    const view = await mount();
    view.unmount();
    const applied = history('current');
    applied.history[0].details.restore_report.is_dry_run = false;
    await act(async () => { resolve(applied as never); });
    expect(screen.queryByText('CURRENT REPORT')).not.toBeInTheDocument();
    expect(invalidateServerData).not.toHaveBeenCalled();
  });

  it.each(['failed', 'cancelled'])('reads genuine %s history without saying no-start', async (status) => {
    vi.mocked(api.getTask).mockResolvedValue(task('current', status) as never);
    vi.mocked(api.getTaskHistory).mockResolvedValue({ history: [{ status,
      details: { run_id: 'current' }, error: 'CURRENT FAILURE' }] } as never);
    await mount();
    for (let i = 0; i < 6; i++) await tick(500);
    expect(screen.getByText('CURRENT FAILURE')).toBeInTheDocument();
    expect(screen.queryByText(/did not start/i)).not.toBeInTheDocument();
    expect(api.getTaskHistory).toHaveBeenCalledTimes(status === 'failed' ? 1 : 6);
    expect(invalidateServerData).not.toHaveBeenCalled();
  });

  it.each([false, true])('keeps warning reports and invalidates only apply=%s', async (apply) => {
    const response = history('current');
    response.history[0].status = 'completed_with_warnings';
    response.history[0].details.restore_report.is_dry_run = !apply;
    vi.mocked(api.getTask).mockResolvedValue(task('current', 'completed_with_warnings') as never);
    vi.mocked(api.getTaskHistory).mockResolvedValue(response as never);
    await mount();
    expect(screen.getByTestId('existing-channel-reattach-notice')).toHaveTextContent('CURRENT REPORT');
    expect(screen.queryByText(/restore failed/i)).not.toBeInTheDocument();
    expect(vi.mocked(invalidateServerData).mock.calls).toEqual(apply ? [['channel-groups'], ['channels']] : []);
  });

  it('history identity missing or different never yields a report, keeping six retries', async () => {
    vi.mocked(api.getTask).mockResolvedValue(task('current') as never);
    vi.mocked(api.getTaskHistory).mockResolvedValue(history('old') as never);
    await mount();
    for (let i = 0; i < 6; i++) await tick(500);
    expect(api.getTaskHistory).toHaveBeenCalledTimes(6);
    expect(screen.getByText('Restore failed')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /apply these changes/i })).not.toBeInTheDocument();
    expect(invalidateServerData).not.toHaveBeenCalled();
  });

  it('a rejected trigger never polls or reads another operation', async () => {
    vi.mocked(api.startDbasRestore).mockRejectedValue(new Error('Rejected overlap'));
    vi.mocked(api.restoreDbasBackupSaved).mockRejectedValue(new Error('Rejected overlap'));
    await mount();
    expect(screen.getByText('Rejected overlap')).toBeInTheDocument();
    expect(api.getTask).not.toHaveBeenCalled();
    expect(api.getTaskHistory).not.toHaveBeenCalled();
  });

  it('a trigger without authoritative identity fails closed even with fresh terminal progress', async () => {
    for (const trigger of [api.startDbasRestore, api.restoreDbasBackupSaved]) {
      vi.mocked(trigger).mockResolvedValue({ task_id: 'dbas_restore', status: 'started' } as never);
    }
    vi.mocked(api.getTask).mockResolvedValue(task('current') as never);
    await mount();
    for (let i = 0; i < 20; i++) await tick();
    expect(screen.getByText(/restore did not start/i)).toBeInTheDocument();
    expect(api.getTaskHistory).not.toHaveBeenCalled();
  });

  it('keeps transient history retry behavior', async () => {
    vi.mocked(api.getTask).mockResolvedValue(task('current') as never);
    vi.mocked(api.getTaskHistory).mockRejectedValueOnce(new Error('transient'))
      .mockResolvedValue(history('current') as never);
    await mount();
    expect(screen.queryByRole('button', { name: /apply these changes/i })).not.toBeInTheDocument();
    await tick(500);
    expect(screen.getByRole('button', { name: /apply these changes/i })).toBeEnabled();
    expect(api.getTaskHistory).toHaveBeenCalledTimes(2);
  });

  it('starts its full polling budget only after a slow successful trigger', async () => {
    let resolve!: (value: never) => void;
    const trigger = flow === 'uploaded' ? api.startDbasRestore : api.restoreDbasBackupSaved;
    vi.mocked(trigger).mockImplementation(() => new Promise((r) => { resolve = r; }));
    vi.mocked(api.getTask).mockResolvedValue(task('old') as never);
    vi.mocked(api.getTaskHistory).mockResolvedValue(history('current') as never);
    await mount();
    for (let i = 0; i < 30; i++) await tick();
    expect(api.getTask).not.toHaveBeenCalled();
    expect(api.getTaskHistory).not.toHaveBeenCalled();
    await act(async () => { resolve({ status: 'started', task_id: 'dbas_restore', run_id: 'current' } as never); });
    for (let i = 0; i < 18; i++) await tick();
    expect(api.getTask).toHaveBeenCalledTimes(19);
    expect(screen.queryByText(/did not start/i)).not.toBeInTheDocument();
    vi.mocked(api.getTask).mockResolvedValue(task('current') as never);
    await tick();
    expect(screen.getByRole('button', { name: /apply these changes/i })).toBeEnabled();
  });

  it('late progress from an unmounted consumer cannot finalize a new mount', async () => {
    let resolve!: (value: never) => void;
    vi.mocked(api.getTask).mockImplementationOnce(() => new Promise((r) => { resolve = r; }));
    const first = await mount();
    first.unmount();
    vi.mocked(api.getTask).mockResolvedValue(task('old') as never);
    await mount();
    await act(async () => { resolve(task('current') as never); });
    expect(api.getTaskHistory).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /apply these changes/i })).not.toBeInTheDocument();
  });
});
