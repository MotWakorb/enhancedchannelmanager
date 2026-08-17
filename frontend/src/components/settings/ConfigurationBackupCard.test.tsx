/**
 * Tests for ConfigurationBackupCard (bead enhancedchannelmanager-pui76).
 *
 * Contract:
 *   - One click on Settings → Backup & Restore produces the standard DBAS
 *     artifact. No passphrase, no acknowledgement, no trip to Scheduled Tasks.
 *   - The card is named for what it PRODUCES, not for the action (PO decision
 *     D3). After bead gi4zn the standard artifact carries no provider
 *     credentials and no ECM accounts, so a control called "Back Up Now" on a
 *     card called "Backup" would let an operator believe they hold a complete
 *     backup and find out otherwise at the worst possible moment.
 *   - The card says what is missing and where to get it (the Encrypted Backup
 *     card with "Include credentials").
 *   - The artifact appears in Saved Backups without a page reload — same seam,
 *     and same repeat-the-action failure mode, as bead 5z7c9 instance 3.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';

const notifications = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
};

vi.mock('../../services/api', () => ({ runTask: vi.fn() }));
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notifications,
}));
vi.mock('../../hooks/useServerDataInvalidation', () => ({
  invalidateServerData: vi.fn(),
}));

import * as api from '../../services/api';
import { invalidateServerData } from '../../hooks/useServerDataInvalidation';
import { ConfigurationBackupCard } from './ConfigurationBackupCard';

function taskResult(overrides: Record<string, unknown> = {}) {
  return {
    success: true,
    message: 'Backup artifact built. All 12 categories archived',
    started_at: '2026-08-17T10:00:00Z',
    completed_at: '2026-08-17T10:00:09Z',
    total_items: 12,
    success_count: 12,
    failed_count: 0,
    skipped_count: 0,
    ...overrides,
  };
}

function clickCreate() {
  return act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /Create Configuration Backup/i }));
  });
}

describe('ConfigurationBackupCard (bead pui76)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('is named for what it produces, not for the action', () => {
    render(<ConfigurationBackupCard />);
    expect(screen.getByRole('heading', { name: 'Configuration Backup' })).toBeInTheDocument();
    // The retracted framing. "Back Up Now" on a card called "Backup" is exactly
    // what PO decision D3 rules out.
    expect(screen.queryByRole('button', { name: /^Back Up Now$/i })).not.toBeInTheDocument();
  });

  it('says the artifact carries no credentials and no ECM accounts', () => {
    render(<ConfigurationBackupCard />);
    const card = screen.getByTestId('configuration-backup-card');
    expect(card).toHaveTextContent(/no credentials/i);
    expect(card).toHaveTextContent(/accounts/i);
    // …and does not leave the operator without a route to a complete one.
    expect(card).toHaveTextContent(/Encrypted Backup/i);
    expect(card).toHaveTextContent(/Include credentials/i);
  });

  it('produces the artifact in one click, with no encryption parameters', async () => {
    vi.mocked(api.runTask).mockResolvedValue(taskResult());
    render(<ConfigurationBackupCard />);

    await clickCreate();

    // Exactly the plain-artifact call: no passphrase, no include_credentials,
    // no acknowledge_unrecoverable. Anything in the third argument would make
    // this a different artifact than the card describes.
    expect(api.runTask).toHaveBeenCalledTimes(1);
    expect(api.runTask).toHaveBeenCalledWith('dbas_backup');
  });

  it('refreshes Saved Backups so the new artifact is visible without a reload', async () => {
    vi.mocked(api.runTask).mockResolvedValue(taskResult());
    render(<ConfigurationBackupCard />);

    await clickCreate();

    expect(invalidateServerData).toHaveBeenCalledWith('saved-backups');
    expect(notifications.success).toHaveBeenCalled();
  });

  it('reports a failed run as an error and never claims an artifact exists', async () => {
    vi.mocked(api.runTask).mockResolvedValue(
      taskResult({ success: false, error: 'journal.db could not be scrubbed', message: '' }),
    );
    render(<ConfigurationBackupCard />);

    await clickCreate();

    expect(notifications.error).toHaveBeenCalledWith(
      'journal.db could not be scrubbed',
      'Configuration Backup',
    );
    expect(notifications.success).not.toHaveBeenCalled();
    expect(invalidateServerData).not.toHaveBeenCalled();
  });

  it('reports a thrown request error', async () => {
    vi.mocked(api.runTask).mockRejectedValue(new Error('Network unreachable'));
    render(<ConfigurationBackupCard />);

    await clickCreate();

    expect(notifications.error).toHaveBeenCalledWith('Network unreachable', 'Configuration Backup');
    expect(notifications.success).not.toHaveBeenCalled();
  });

  it('cannot be fired twice while a backup is running', async () => {
    let release: (value: ReturnType<typeof taskResult>) => void = () => {};
    vi.mocked(api.runTask).mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }) as ReturnType<typeof api.runTask>,
    );
    render(<ConfigurationBackupCard />);

    await clickCreate();

    // The button is replaced by a running indicator, so there is nothing to
    // click a second time.
    expect(
      screen.queryByRole('button', { name: /Create Configuration Backup/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Building configuration backup/i)).toBeInTheDocument();

    await act(async () => {
      release(taskResult());
    });
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /Create Configuration Backup/i }),
      ).toBeInTheDocument(),
    );
    expect(api.runTask).toHaveBeenCalledTimes(1);
  });
});
