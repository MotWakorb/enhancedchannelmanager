/**
 * Tests for DbasRestoreModal (bead 7euap + u81kh passphrase path).
 *
 * Contracts under test:
 *   - Upload a plain .zip -> configure step, no passphrase field, preview enabled.
 *   - Upload an ENCRYPTED artifact (ECMBKENC magic) -> passphrase field shown,
 *     and the run button stays disabled until a passphrase is entered.
 *   - "Run preview" calls startDbasRestore(file, /*apply*\/ false, passphrase?).
 *   - A completed dry-run reads the RestoreReport from task history and renders
 *     the summary with an "Apply these changes" follow-through.
 *   - A failed run (e.g. wrong passphrase) surfaces the sanitized error.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../services/api', () => ({
  getSettings: vi.fn(),
  startDbasRestore: vi.fn(),
  getTaskHistory: vi.fn(),
  getTask: vi.fn(),
}));

// Controllable progress view returned by the polling hook.
let mockView: Record<string, unknown>;
vi.mock('../hooks/useRestoreProgress', () => ({
  useRestoreProgress: () => mockView,
}));
vi.mock('../hooks/useNavigateAwayGuard', () => ({ useNavigateAwayGuard: () => {} }));

import * as api from '../services/api';
import { DbasRestoreModal } from './DbasRestoreModal';

function view(over: Record<string, unknown> = {}) {
  return {
    progress: null, stageNumber: 1, totalStages: 13, stageLabel: 'Pre-flight',
    itemCurrent: 0, itemTotal: 0, percentage: 0, status: 'idle',
    isRunning: false, isError: false, isComplete: false, error: null,
    stopPolling: vi.fn(), ...over,
  };
}

function zip(name: string, head: string) {
  return new File([new TextEncoder().encode(head + 'payloadpayload')], name, { type: 'application/zip' });
}

function dropFile(file: File) {
  const dz = screen.getByText(/drag & drop/i).closest('div')!;
  fireEvent.drop(dz, { dataTransfer: { files: [file] } });
}

const dryRunReport = {
  contract_version: 1, is_dry_run: true, outcome: null,
  categories: [], logo_misses: 0, notes: [],
};

// A dry-run report that WOULD replace guide data on channels the operator
// already has — the state whose remedy copy has to name a reachable control.
const touchedDryRunReport = {
  contract_version: 1, is_dry_run: true, outcome: null,
  categories: [], logo_misses: 0, notes: [],
  epg_link_reattach: {
    mode: 'overwrite', created_channels: 0, existing_channels: 2,
    preserved_channels: 0, existing_channels_named: ['FOX News', 'CNN'],
    preserved_channels_named: [],
  },
};

/**
 * A view for a task the BACKEND reported as failed.
 *
 * It carries a `progress` payload, because the real hook only ever produces a
 * terminal error through `viewFromProgress`, which always has one. A view with
 * `isError: true` and `progress: null` is not a state the hook can reach from a
 * backend status — that shape is reserved for, and now means, "the hook gave up
 * waiting for the run to start", which the modals must NOT treat as a run result
 * (bead dfkbn, review round 5). Fixtures have to respect that distinction or
 * they assert against an impossible state.
 */
function backendFailedView() {
  return view({
    isError: true,
    status: 'failed',
    progress: {
      total: 13, current: 3, percentage: 23, status: 'failed',
      current_item: 'channel', success_count: 0, failed_count: 1,
      skipped_count: 0, started_at: '2026-08-05T10:00:00Z',
    },
  });
}

describe('DbasRestoreModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockView = view();
    (api.getSettings as ReturnType<typeof vi.fn>).mockResolvedValue({ url: 'http://disp:9191' });
  });

  it('renders the upload dropzone first', async () => {
    render(<DbasRestoreModal onClose={vi.fn()} />);
    expect(screen.getByText(/drag & drop a backup artifact/i)).toBeInTheDocument();

    // Let the mount-time getSettings() fetch settle so the resulting state
    // update (setDispatcharrUrl) happens inside act() — otherwise React logs
    // an act() warning for the un-awaited promise resolution.
    await waitFor(() => {
      expect(api.getSettings).toHaveBeenCalled();
    });
  });

  it('a plain .zip goes to configure with no passphrase field', async () => {
    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('backup.zip', 'PK'));
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());
    expect(screen.queryByLabelText('Passphrase')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /run preview/i })).toBeEnabled();
  });

  it('an encrypted artifact requires a passphrase before the run is enabled', async () => {
    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('enc.zip', 'ECMBKENC'));
    await waitFor(() => expect(screen.getByLabelText('Passphrase')).toBeInTheDocument());
    const run = screen.getByRole('button', { name: /run preview/i });
    expect(run).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value: 'a-passphrase' } });
    expect(run).toBeEnabled();
  });

  it('Run preview triggers a dry-run and renders the report', async () => {
    (api.startDbasRestore as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed', details: { restore_report: dryRunReport } }],
    });
    // The poll hook reports terminal-complete as soon as the run starts.
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('backup.zip', 'PK'));
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(api.startDbasRestore).toHaveBeenCalledWith(expect.any(File), false, undefined, 'preserve'),
    );
    // Dry-run summary + the apply follow-through.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /apply these changes/i })).toBeInTheDocument(),
    );
  });

  it('applying from a completed dry-run requires typing the exact file name in the confirm dialog', async () => {
    (api.startDbasRestore as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed', details: { restore_report: dryRunReport } }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('backup.zip', 'PK'));
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /apply these changes/i })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /apply these changes/i }));

    const applyConfirm = screen.getByRole('button', { name: 'Apply restore' });
    expect(applyConfirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/type/i), { target: { value: 'wrong-name.zip' } });
    expect(applyConfirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/type/i), { target: { value: 'backup.zip' } });
    expect(applyConfirm).toBeEnabled();
    fireEvent.click(applyConfirm);

    await waitFor(() =>
      expect(api.startDbasRestore).toHaveBeenCalledWith(expect.any(File), true, undefined, 'preserve'),
    );
  });

  it('selecting Apply at the configure step also gates the mutation behind the confirm dialog', async () => {
    (api.startDbasRestore as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: false,
    });

    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('backup.zip', 'PK'));
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('radio', { name: /^apply/i }));
    fireEvent.click(screen.getByRole('button', { name: /^apply restore/i }));

    // No mutation yet — the confirm dialog must appear first.
    expect(api.startDbasRestore).not.toHaveBeenCalled();
    const applyConfirm = screen.getByRole('button', { name: 'Apply restore' });
    expect(applyConfirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/type/i), { target: { value: 'wrong-name.zip' } });
    expect(applyConfirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/type/i), { target: { value: 'backup.zip' } });
    expect(applyConfirm).toBeEnabled();
    fireEvent.click(applyConfirm);

    await waitFor(() =>
      expect(api.startDbasRestore).toHaveBeenCalledWith(expect.any(File), true, undefined, 'preserve'),
    );
  });

  it('Preview (dry run) remains one click even with the confirm gate in place', async () => {
    (api.startDbasRestore as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });

    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('backup.zip', 'PK'));
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(api.startDbasRestore).toHaveBeenCalledWith(expect.any(File), false, undefined, 'preserve'),
    );
    expect(screen.queryByRole('button', { name: 'Apply restore' })).not.toBeInTheDocument();
  });

  it('passes the passphrase through for an encrypted artifact', async () => {
    (api.startDbasRestore as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed', details: { restore_report: dryRunReport } }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('enc.zip', 'ECMBKENC'));
    await waitFor(() => expect(screen.getByLabelText('Passphrase')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value: 'sekret-passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(api.startDbasRestore).toHaveBeenCalledWith(expect.any(File), false, 'sekret-passphrase', 'preserve'),
    );
  });

  it('surfaces a sanitized failure (wrong passphrase) and returns to configure', async () => {
    (api.startDbasRestore as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{
        status: 'failed', details: null,
        error: 'Could not decrypt backup: wrong passphrase or corrupted artifact',
      }],
    });
    mockView = backendFailedView();

    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('enc.zip', 'ECMBKENC'));
    await waitFor(() => expect(screen.getByLabelText('Passphrase')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value: 'wrong-passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(screen.getByText(/wrong passphrase or corrupted artifact/i)).toBeInTheDocument(),
    );
  });
  it('reaches the results step when the run finished with warnings', async () => {
    // Bead fexq1: a degraded restore's history row now reads
    // `completed_with_warnings`. The terminal check used to accept only
    // `completed` and `failed`, so the drill's degraded restore would poll
    // until the retries ran out and land back on configure with
    // "Restore failed" — for a restore that completed and rolled nothing back.
    (api.startDbasRestore as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed_with_warnings', details: { restore_report: dryRunReport } }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('backup.zip', 'PK'));
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /apply these changes/i })).toBeInTheDocument(),
    );
  });

  // --- Channel-reattach mode (bead dfkbn, PR review W1) -------------------

  it('defaults the reattach mode to keeping existing channels as they are', async () => {
    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('backup.zip', 'PK'));
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());

    const keep = screen.getByLabelText(/keep their current guide data and logos/i);
    const replace = screen.getByLabelText(/replace their guide data and logos/i);
    expect(keep).toBeChecked();
    expect(replace).not.toBeChecked();
  });

  it('sends overwrite only when the operator explicitly picks it', async () => {
    (api.startDbasRestore as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed', details: { restore_report: dryRunReport } }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('backup.zip', 'PK'));
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());

    fireEvent.click(screen.getByLabelText(/replace their guide data and logos/i));
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(api.startDbasRestore).toHaveBeenCalledWith(
        expect.any(File), false, undefined, 'overwrite',
      ),
    );
  });

  it('the mode picker names the consequence, never the enum value', async () => {
    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('backup.zip', 'PK'));
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());

    // The wire values are an implementation detail; an operator deciding
    // whether to keep their own guide links must not have to map an enum.
    const picker = screen.getByRole('group', { name: /channels that already exist/i });
    expect(picker.textContent).not.toMatch(/\bpreserve\b/i);
    expect(picker.textContent).not.toMatch(/\boverwrite\b/i);
  });
  // --- The preview's advice must name a control that EXISTS (bead dfkbn) -----
  //
  // The component-level notice test hand-supplies `mode`, so it structurally
  // cannot see whether the control its copy names is reachable from the step it
  // renders on. These drive the real modal to the dry-run results step.

  it('offers a way back to the options from a dry-run result', async () => {
    (api.startDbasRestore as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed', details: { restore_report: touchedDryRunReport } }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('backup.zip', 'PK'));
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    // The summary warns, and its remedy names "Back to options"...
    const notice = await screen.findByTestId('existing-channel-reattach-notice');
    expect(notice.textContent).toMatch(/back to options/i);

    // ...which is a real, enabled control on this step.
    const back = screen.getByRole('button', { name: /back to options/i });
    expect(back).toBeEnabled();

    // And it lands back on the picker, so the advice can be acted on.
    fireEvent.click(back);
    await waitFor(() =>
      expect(
        screen.getByLabelText(/keep their current guide data and logos/i),
      ).toBeInTheDocument(),
    );
  });

  it('offers no way back from an APPLIED result, and says so', async () => {
    (api.startDbasRestore as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: false,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{
        status: 'completed',
        details: { restore_report: { ...touchedDryRunReport, is_dry_run: false, outcome: 'success' } },
      }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('backup.zip', 'PK'));
    await waitFor(() => expect(screen.getByText('backup.zip')).toBeInTheDocument());


    // Reaching the results step. Both footer branches key off the REPORT's
    // is_dry_run, which is what an applied run returns, so this is the applied
    // results step without re-driving the type-to-confirm apply gate.
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    const notice = await screen.findByTestId('existing-channel-reattach-notice');
    // Nothing to go back to once it has run, so the copy must not pretend.
    expect(notice.textContent).toMatch(/run the restore again/i);
    expect(notice.textContent).not.toMatch(/back to options/i);
    expect(screen.queryByRole('button', { name: /back to options/i })).toBeNull();
  });
});
