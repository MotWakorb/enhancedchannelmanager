/**
 * Saved Backups must show an artifact the operator just created, without a
 * page reload (bead enhancedchannelmanager-5z7c9, instance 3 — drill run
 * 2026-08-06-run9 finding P-4).
 *
 * WHY THIS ONE MATTERS MOST. The Encrypted Backup card and the Saved Backups
 * list are siblings on the same page. Creating an encrypted artifact
 * succeeded — the file was written and a success toast was raised — but the
 * list beside it kept showing only the previous artifact, through an in-app
 * navigation away and back, until the whole page was reloaded. The action an
 * operator repeats when they think a backup failed is "create another
 * credential-bearing encrypted backup", so the cosmetic bug produces real
 * duplicate secrets on disk.
 *
 * This test crosses the real seam: the section renders the real
 * EncryptedBackupCard child, the card issues the real mutation, and the
 * assertion is on the rendered list — not on the invalidation plumbing.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { BackupRestoreSection } from './BackupRestoreSection';

const EXISTING = {
  filename: 'ecm-dbas-20260806-0800.zip',
  size_bytes: 2048,
  created_at: '2026-08-06T08:00:00Z',
  type: 'zip' as const,
};
const CREATED = {
  filename: 'ecm-dbas-20260806-0930-encrypted.zip',
  size_bytes: 4096,
  created_at: '2026-08-06T09:30:00Z',
  type: 'zip' as const,
};

vi.mock('../../services/api', () => ({
  getBackupDownloadUrl: vi.fn(() => '/api/backup/create'),
  restoreBackup: vi.fn(),
  exportBackup: vi.fn(),
  getExportSections: vi.fn(() => Promise.resolve([{ key: 'settings', label: 'Settings' }])),
  listSavedBackups: vi.fn(),
  getSavedBackupDownloadUrl: vi.fn((f: string) => `/api/backup/saved/${f}`),
  deleteSavedBackup: vi.fn(),
  restoreSavedBackup: vi.fn(),
  runTask: vi.fn(),
}));

// Children with their own fetches and their own suites — stubbed so this file
// exercises exactly one seam: EncryptedBackupCard -> Saved Backups.
vi.mock('./BackupScheduleBanner', () => ({ BackupScheduleBanner: () => null }));
vi.mock('./OutboundPolicyCard', () => ({ OutboundPolicyCard: () => null }));
vi.mock('./SyncTargetsCard', () => ({ SyncTargetsCard: () => null }));
vi.mock('./CloudTargetsCard', () => ({ CloudTargetsCard: () => null }));
vi.mock('../BackupRestoreModal', () => ({ BackupRestoreModal: () => null }));
vi.mock('../DbasRestoreModal', () => ({ DbasRestoreModal: () => null }));
vi.mock('../DbasRestoreSavedModal', () => ({ DbasRestoreSavedModal: () => null }));

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

import * as api from '../../services/api';

const PASSPHRASE = 'correct horse battery staple';

async function createEncryptedBackup() {
  fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value: PASSPHRASE } });
  fireEvent.change(screen.getByLabelText('Confirm passphrase'), { target: { value: PASSPHRASE } });
  fireEvent.click(screen.getByLabelText(/I understand that a lost passphrase/i));
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /Create Encrypted Backup/i }));
  });
}

describe('Saved Backups freshness after an encrypted backup (bead 5z7c9 instance 3)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    globalThis.fetch = vi.fn();
  });

  it('lists the newly created artifact without a page reload', async () => {
    vi.mocked(api.listSavedBackups)
      .mockResolvedValueOnce([EXISTING])
      .mockResolvedValue([EXISTING, CREATED]);
    vi.mocked(api.runTask).mockResolvedValue({
      success: true,
      message: 'Encrypted backup created',
      started_at: '2026-08-06T09:30:00Z',
      completed_at: '2026-08-06T09:30:12Z',
      total_items: 16,
      success_count: 16,
      failed_count: 0,
      skipped_count: 0,
    });

    render(<BackupRestoreSection isAdmin />);
    await screen.findByText(EXISTING.filename);
    expect(screen.queryByText(CREATED.filename)).not.toBeInTheDocument();

    await createEncryptedBackup();

    await waitFor(() => expect(api.runTask).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(CREATED.filename)).toBeInTheDocument();
  });

  it('does not refetch the list when the backup task reports failure', async () => {
    vi.mocked(api.listSavedBackups).mockResolvedValue([EXISTING]);
    vi.mocked(api.runTask).mockResolvedValue({
      success: false,
      message: 'Encrypted backup failed',
      error: 'passphrase rejected',
      started_at: '2026-08-06T09:30:00Z',
      completed_at: '2026-08-06T09:30:01Z',
      total_items: 0,
      success_count: 0,
      failed_count: 1,
      skipped_count: 0,
    });

    render(<BackupRestoreSection isAdmin />);
    await screen.findByText(EXISTING.filename);
    expect(api.listSavedBackups).toHaveBeenCalledTimes(1);

    await createEncryptedBackup();

    await waitFor(() => expect(api.runTask).toHaveBeenCalledTimes(1));
    expect(api.listSavedBackups).toHaveBeenCalledTimes(1);
  });

  it('does not poll — the list is fetched once on mount and then only on a mutation', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(api.listSavedBackups).mockResolvedValue([EXISTING]);

      await act(async () => {
        render(<BackupRestoreSection isAdmin />);
      });
      expect(api.listSavedBackups).toHaveBeenCalledTimes(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(120_000);
      });

      expect(api.listSavedBackups).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
