/**
 * Tests for DbasRestoreSavedModal (bead rzhid) — the SAVED-file analogue of
 * DbasRestoreModal. Mirrors that suite's structure; the key difference under
 * test is that encryption is operator-declared (a checkbox) rather than
 * sniffed from file bytes, since a saved server-side file has no local File
 * object to peek at.
 *
 * Contracts under test:
 *   - Renders directly at the configure step (no upload dropzone) with the
 *     given filename.
 *   - "Run preview" calls restoreDbasBackupSaved(filename, false, undefined).
 *   - Checking "encrypted" reveals a passphrase field and gates the run
 *     button until a passphrase is entered; passphrase is passed through.
 *   - A completed dry-run renders the summary + "Apply these changes", which
 *     requires typing the filename in a TypeToConfirmDialog before calling
 *     restoreDbasBackupSaved(filename, true, ...).
 *   - A failed run surfaces the sanitized error and returns to configure.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../services/api', () => ({
  getSettings: vi.fn(),
  restoreDbasBackupSaved: vi.fn(),
  getTaskHistory: vi.fn(),
}));

let mockView: Record<string, unknown>;
vi.mock('../hooks/useRestoreProgress', () => ({
  useRestoreProgress: () => mockView,
}));
vi.mock('../hooks/useNavigateAwayGuard', () => ({ useNavigateAwayGuard: () => {} }));

import * as api from '../services/api';
import { DbasRestoreSavedModal } from './DbasRestoreSavedModal';

function view(over: Record<string, unknown> = {}) {
  return {
    progress: null, stageNumber: 1, totalStages: 13, stageLabel: 'Pre-flight',
    itemCurrent: 0, itemTotal: 0, percentage: 0, status: 'idle',
    isRunning: false, isError: false, isComplete: false, error: null,
    stopPolling: vi.fn(), ...over,
  };
}

const FILENAME = 'ecm-backup-2026-01-01_000000.zip';

const dryRunReport = {
  contract_version: 1, is_dry_run: true, outcome: null,
  categories: [], logo_misses: 0, notes: [],
};

describe('DbasRestoreSavedModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockView = view();
    (api.getSettings as ReturnType<typeof vi.fn>).mockResolvedValue({ url: 'http://disp:9191' });
  });

  it('renders the configure step directly with the given filename', async () => {
    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    expect(screen.getByText(FILENAME)).toBeInTheDocument();
    expect(screen.queryByLabelText('Passphrase')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /run preview/i })).toBeEnabled();

    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
  });

  it('checking "encrypted" requires a passphrase before the run is enabled', async () => {
    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    fireEvent.click(screen.getByLabelText('This backup is encrypted'));

    const run = screen.getByRole('button', { name: /run preview/i });
    expect(run).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value: 'a-passphrase' } });
    expect(run).toBeEnabled();

    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
  });

  it('Run preview triggers a dry-run and renders the report', async () => {
    (api.restoreDbasBackupSaved as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed', details: { restore_report: dryRunReport } }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(api.restoreDbasBackupSaved).toHaveBeenCalledWith(FILENAME, false, undefined),
    );
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /apply these changes/i })).toBeInTheDocument(),
    );
  });

  it('applying requires typing the exact filename in the confirm dialog', async () => {
    (api.restoreDbasBackupSaved as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed', details: { restore_report: dryRunReport } }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /apply these changes/i })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /apply these changes/i }));

    const applyConfirm = screen.getByRole('button', { name: 'Apply restore' });
    expect(applyConfirm).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/type/i), { target: { value: FILENAME } });
    fireEvent.click(applyConfirm);

    await waitFor(() =>
      expect(api.restoreDbasBackupSaved).toHaveBeenCalledWith(FILENAME, true, undefined),
    );
  });

  it('passes the passphrase through for an encrypted artifact', async () => {
    (api.restoreDbasBackupSaved as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed', details: { restore_report: dryRunReport } }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    fireEvent.click(screen.getByLabelText('This backup is encrypted'));
    fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value: 'sekret' } });
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(api.restoreDbasBackupSaved).toHaveBeenCalledWith(FILENAME, false, 'sekret'),
    );
  });

  it('surfaces a sanitized failure and returns to configure', async () => {
    (api.restoreDbasBackupSaved as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{
        status: 'failed', details: null,
        error: 'Could not decrypt backup: wrong passphrase or corrupted artifact',
      }],
    });
    mockView = view({ isError: true, status: 'failed' });

    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(screen.getByText(/wrong passphrase or corrupted artifact/i)).toBeInTheDocument(),
    );
  });
});
