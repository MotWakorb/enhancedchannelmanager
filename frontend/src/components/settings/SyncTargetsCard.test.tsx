/**
 * Tests for the Cross-Instance Sync card (epic i39wu / bead nnl9s).
 *
 * Contracts under test:
 *   - Lists configured sync targets with a tri-state status badge derived from
 *     `last_outcome` plus the "last synced" timestamp.
 *   - The add-target form calls createSyncTarget with the entered fields.
 *   - The enable/disable toggle (the KILL SWITCH) calls updateSyncTarget with
 *     the flipped `enabled` value.
 *   - Delete confirms first, then calls deleteSyncTarget.
 *   - "Sync now" runs a DRY-RUN preview — runTask(`dbas_sync_${id}`, undefined,
 *     { sync_target_id, confirm_apply: false }) — and only an explicit Apply
 *     runs with confirm_apply: true. The task id is PER TARGET (7ipq2.3 /
 *     ADR-013 S6): distinct targets sync concurrently; the backend refuses a
 *     second run against the same target.
 *   - The load-bearing operator copy (one-way overwrite + credentials-not-synced)
 *     is present in the card (ADR-013).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

vi.mock('../../services/api', () => ({
  listSyncTargets: vi.fn(),
  createSyncTarget: vi.fn(),
  updateSyncTarget: vi.fn(),
  deleteSyncTarget: vi.fn(),
  runTask: vi.fn(),
}));

const notify = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

import * as api from '../../services/api';
import { SyncTargetsCard } from './SyncTargetsCard';

type Mock = ReturnType<typeof vi.fn>;

const TARGET: api.SyncTarget = {
  id: 7,
  name: 'Living Room B',
  base_url: 'https://b.example.com',
  credentials: { username: '***user' },
  enabled: true,
  insecure: false,
  fuzzy_stream_matching: false,
  credential_version: 1,
  last_full_sync_at: '2026-06-18T12:00:00Z',
  last_outcome: 'success',
};

function mockTargets(targets: api.SyncTarget[]) {
  (api.listSyncTargets as Mock).mockResolvedValue(targets);
}

async function renderCard(targets: api.SyncTarget[] = [TARGET]) {
  mockTargets(targets);
  render(<SyncTargetsCard />);
  // Wait for the initial list load to settle.
  await waitFor(() => expect(api.listSyncTargets).toHaveBeenCalled());
}

describe('SyncTargetsCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default confirm to true so delete proceeds unless a test overrides it.
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('renders the one-way + credentials-not-synced operator copy', async () => {
    await renderCard([]);
    expect(screen.getByText(/managed replica/i)).toBeInTheDocument();
    expect(screen.getByText(/overwritten by/i)).toBeInTheDocument();
    expect(screen.getByText(/credentials are not synced/i)).toBeInTheDocument();
  });

  it('lists configured targets with a tri-state status badge', async () => {
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());
    const badge = screen.getByTestId('sync-target-status-7');
    expect(badge).toHaveTextContent(/success/i);
  });

  it('add-target form calls createSyncTarget with entered fields', async () => {
    (api.createSyncTarget as Mock).mockResolvedValue({ ...TARGET, id: 8, name: 'New B' });
    await renderCard([]);

    // Wait for the async list-load to settle before interacting (the add button
    // + form only render once the loading state clears).
    fireEvent.click(await screen.findByRole('button', { name: /add sync target/i }));

    fireEvent.change(await screen.findByLabelText(/^name/i), { target: { value: 'New B' } });
    fireEvent.change(screen.getByLabelText(/base url/i), {
      target: { value: 'https://new-b.example.com' },
    });
    fireEvent.change(screen.getByLabelText(/username/i), { target: { value: 'admin' } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'secret' } });

    // The button's accessible name includes the leading material-icon ligature
    // ("add Create target"), so match on the label substring, not an anchor.
    fireEvent.click(screen.getByRole('button', { name: /create target/i }));

    await waitFor(() => expect(api.createSyncTarget).toHaveBeenCalledTimes(1));
    expect(api.createSyncTarget).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'New B',
        base_url: 'https://new-b.example.com',
        credentials: { username: 'admin', password: 'secret' },
      }),
    );
  });

  it('enable/disable toggle (kill switch) calls updateSyncTarget with flipped enabled', async () => {
    (api.updateSyncTarget as Mock).mockResolvedValue({ ...TARGET, enabled: false });
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-toggle-7'));

    await waitFor(() => expect(api.updateSyncTarget).toHaveBeenCalledTimes(1));
    expect(api.updateSyncTarget).toHaveBeenCalledWith(7, { enabled: false });
  });

  it('delete confirms then calls deleteSyncTarget', async () => {
    (api.deleteSyncTarget as Mock).mockResolvedValue(undefined);
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-delete-7'));

    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(api.deleteSyncTarget).toHaveBeenCalledWith(7));
  });

  it('does NOT delete when the confirm is declined', async () => {
    (window.confirm as Mock).mockReturnValue(false);
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-delete-7'));

    expect(window.confirm).toHaveBeenCalled();
    expect(api.deleteSyncTarget).not.toHaveBeenCalled();
  });

  it('"Sync now" runs a DRY-RUN preview (confirm_apply: false)', async () => {
    (api.runTask as Mock).mockResolvedValue({ success: true, message: 'preview ok' });
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-preview-7'));

    await waitFor(() => expect(api.runTask).toHaveBeenCalledTimes(1));
    expect(api.runTask).toHaveBeenCalledWith('dbas_sync_7', undefined, {
      sync_target_id: 7,
      confirm_apply: false,
    });
  });

  it('Apply (after preview) runs with confirm_apply: true', async () => {
    (api.runTask as Mock).mockResolvedValue({ success: true, message: 'preview ok' });
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    // Run the preview first to surface the Apply affordance.
    fireEvent.click(screen.getByTestId('sync-target-preview-7'));
    await waitFor(() => expect(api.runTask).toHaveBeenCalledTimes(1));

    const applyBtn = await screen.findByTestId('sync-target-apply-7');
    fireEvent.click(applyBtn);

    // Apply confirm dialog (source-wins overwrite) — confirm spy returns true.
    await waitFor(() => expect(api.runTask).toHaveBeenCalledTimes(2));
    expect(api.runTask).toHaveBeenLastCalledWith('dbas_sync_7', undefined, {
      sync_target_id: 7,
      confirm_apply: true,
    });
  });

  it('shows the insecure-TLS warning badge when a target has insecure=true (nngkg)', async () => {
    await renderCard([{ ...TARGET, insecure: true }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());
    const badge = screen.getByTestId('sync-target-insecure-7');
    expect(badge).toBeInTheDocument();
    // Plain-language copy — no "TLS"/"SSRF" jargon required to convey the risk.
    expect(badge).toHaveTextContent(/certificate check off/i);
  });

  it('does NOT show the insecure badge for a secure target', async () => {
    await renderCard([{ ...TARGET, insecure: false }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());
    expect(screen.queryByTestId('sync-target-insecure-7')).not.toBeInTheDocument();
  });

  it('a disabled target shows the kill-switch state', async () => {
    await renderCard([{ ...TARGET, enabled: false }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());
    const row = screen.getByTestId('sync-target-row-7');
    expect(within(row).getByTestId('sync-target-toggle-7')).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });
});
