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
      expect(api.restoreDbasBackupSaved).toHaveBeenCalledWith(FILENAME, false, undefined, 'preserve'),
    );
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /apply these changes/i })).toBeInTheDocument(),
    );
  });

  it('reaches the results step when the run finished with warnings', async () => {
    // Bead fexq1 — same terminal-status widening as DbasRestoreModal. This
    // modal polls the same history row, so it needs the same fix or a degraded
    // restore never renders its report here either.
    (api.restoreDbasBackupSaved as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed_with_warnings', details: { restore_report: dryRunReport } }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

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
      expect(api.restoreDbasBackupSaved).toHaveBeenCalledWith(FILENAME, true, undefined, 'preserve'),
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
      expect(api.restoreDbasBackupSaved).toHaveBeenCalledWith(FILENAME, false, 'sekret', 'preserve'),
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
    mockView = backendFailedView();

    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(screen.getByText(/wrong passphrase or corrupted artifact/i)).toBeInTheDocument(),
    );
  });
  // --- Channel-reattach mode (bead dfkbn, PR review W1) -------------------
  //
  // The assertions above pin the fourth argument as 'preserve', but that is the
  // component's own useState default and would still pass if the picker were
  // deleted. These pin the CONTROL: it renders, and it changes what is sent.

  it('renders the reattach picker, defaulted to keeping existing channels', async () => {
    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    const keep = screen.getByLabelText(/keep their current guide data, logos, and grouping/i);
    const replace = screen.getByLabelText(/replace their guide data, logos, and grouping/i);
    expect(keep).toBeInTheDocument();
    expect(replace).toBeInTheDocument();
    expect(keep).toBeChecked();
    expect(replace).not.toBeChecked();
  });

  it('sends overwrite only when the operator explicitly picks it', async () => {
    (api.restoreDbasBackupSaved as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed', details: { restore_report: dryRunReport } }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText(/replace their guide data, logos, and grouping/i));
    fireEvent.click(screen.getByRole('button', { name: /run preview/i }));

    await waitFor(() =>
      expect(api.restoreDbasBackupSaved).toHaveBeenCalledWith(
        FILENAME, false, undefined, 'overwrite',
      ),
    );
  });

  it('the mode picker names the consequence, never the enum value', async () => {
    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

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
    (api.restoreDbasBackupSaved as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: true,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{ status: 'completed', details: { restore_report: touchedDryRunReport } }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
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
        screen.getByLabelText(/keep their current guide data, logos, and grouping/i),
      ).toBeInTheDocument(),
    );
  });

  it('offers no way back from an APPLIED result, and says so', async () => {
    (api.restoreDbasBackupSaved as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: 'started', task_id: 'dbas_restore', is_dry_run: false,
    });
    (api.getTaskHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      history: [{
        status: 'completed',
        details: { restore_report: { ...touchedDryRunReport, is_dry_run: false, outcome: 'success' } },
      }],
    });
    mockView = view({ isComplete: true, status: 'completed' });

    render(<DbasRestoreSavedModal filename={FILENAME} onClose={vi.fn()} />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());


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
