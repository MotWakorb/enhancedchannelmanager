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
      expect(api.startDbasRestore).toHaveBeenCalledWith(expect.any(File), false, undefined),
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
      expect(api.startDbasRestore).toHaveBeenCalledWith(expect.any(File), true, undefined),
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
      expect(api.startDbasRestore).toHaveBeenCalledWith(expect.any(File), true, undefined),
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
      expect(api.startDbasRestore).toHaveBeenCalledWith(expect.any(File), false, undefined),
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
      expect(api.startDbasRestore).toHaveBeenCalledWith(expect.any(File), false, 'sekret-passphrase'),
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
    mockView = view({ isError: true, status: 'failed' });

    render(<DbasRestoreModal onClose={vi.fn()} />);
    dropFile(zip('enc.zip', 'ECMBKENC'));
    await waitFor(() => expect(screen.getByLabelText('Passphrase')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value: 'wrong-passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(screen.getByText(/wrong passphrase or corrupted artifact/i)).toBeInTheDocument(),
    );
  });
});
