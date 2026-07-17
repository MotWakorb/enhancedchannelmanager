/**
 * Unit tests for BackupRestoreSection component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BackupRestoreSection } from './BackupRestoreSection';

// Mock the API module
vi.mock('../../services/api', () => ({
  getBackupDownloadUrl: vi.fn(() => '/api/backup/create'),
  restoreBackup: vi.fn(),
  exportBackup: vi.fn(),
  validateBackup: vi.fn(),
  restoreBackupYaml: vi.fn(),
  getExportSections: vi.fn(() => Promise.resolve([
    { key: 'settings', label: 'Settings' },
    { key: 'tag_groups', label: 'Tag Groups' },
    { key: 'ffmpeg_profiles', label: 'FFmpeg Profiles' },
  ])),
  listSavedBackups: vi.fn(() => Promise.resolve([])),
  getSavedBackupDownloadUrl: vi.fn((f: string) => `/api/backup/saved/${f}`),
  deleteSavedBackup: vi.fn(),
  restoreSavedBackup: vi.fn(),
  restoreDbasBackupSaved: vi.fn(),
  getSettings: vi.fn(() => Promise.resolve({ url: '', ssrf_outbound_mode: 'lan_friendly' })),
  getTaskHistory: vi.fn(() => Promise.resolve({ history: [] })),
  saveSecurityMode: vi.fn(),
}));

// Mock the one-time backup-schedule setup banner (bead ikv8z) — it has its own
// test suite and makes its own getTaskSchedules call, which this suite's api
// mock doesn't stub.
vi.mock('./BackupScheduleBanner', () => ({
  BackupScheduleBanner: () => null,
}));

// Mock BackupRestoreModal to avoid complex rendering
vi.mock('../BackupRestoreModal', () => ({
  BackupRestoreModal: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="backup-restore-modal">
      <button onClick={onClose}>Close Modal</button>
    </div>
  ),
}));

// Mock DbasRestoreSavedModal (bead rzhid) — it has its own test suite;
// stub it here so this suite only asserts it opens with the right filename.
vi.mock('../DbasRestoreSavedModal', () => ({
  DbasRestoreSavedModal: ({ filename, onClose }: { filename: string; onClose: () => void }) => (
    <div data-testid="dbas-restore-saved-modal">
      {filename}
      <button onClick={onClose}>Close DBAS Modal</button>
    </div>
  ),
}));

// Mock notification context
const mockSuccess = vi.fn();
const mockError = vi.fn();
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    success: mockSuccess,
    error: mockError,
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

// CloudTargetsCard (a child) uses the backup-destination prompt context (bead
// s5a3o); stub it so this section test renders without the provider.
vi.mock('../../contexts/BackupDestinationPromptContext', () => ({
  useBackupDestinationPrompt: () => ({ promptBackupDestination: vi.fn() }),
}));

import * as api from '../../services/api';

describe('BackupRestoreSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    globalThis.fetch = vi.fn();
  });

  describe('when not admin', () => {
    it('shows no-access message', () => {
      render(<BackupRestoreSection isAdmin={false} />);
      expect(screen.getByText(/only administrators/i)).toBeInTheDocument();
    });

    it('does not show backup or restore buttons', () => {
      render(<BackupRestoreSection isAdmin={false} />);
      expect(screen.queryByText('Export YAML')).not.toBeInTheDocument();
      expect(screen.queryByText('Restore')).not.toBeInTheDocument();
    });
  });

  describe('when admin', () => {
    it('renders all sections', async () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText('Export Configuration (YAML)')).toBeInTheDocument();
      expect(screen.getByText('Restore from YAML Export')).toBeInTheDocument();
      expect(screen.getByText('Create Full Backup')).toBeInTheDocument();
      expect(screen.getByText('Restore Full Backup')).toBeInTheDocument();

      // Let the mount-time fetch settle (getExportSections + listSavedBackups) so
      // the resulting state updates happen inside act() — otherwise React logs an
      // act() warning for the un-awaited effects that run after the test body.
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });

    it('renders the relocated backup destination policy card (bead 09x38.12)', async () => {
      render(<BackupRestoreSection isAdmin={true} />);

      await waitFor(() => {
        expect(screen.getByText('Where backups can be sent')).toBeInTheDocument();
      });
      expect(screen.getByTestId('outbound-mode-lan_friendly')).toBeInTheDocument();
      expect(screen.getByTestId('outbound-mode-public_only')).toBeInTheDocument();
    });

    it('renders page header', async () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText('Backup & Restore')).toBeInTheDocument();

      // Let mount-time fetches settle.
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });

    it('renders YAML export button', async () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText('Export YAML')).toBeInTheDocument();

      // Let mount-time fetches settle.
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });

    it('renders full backup download button', async () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText('Download Full Backup')).toBeInTheDocument();

      // Let mount-time fetches settle.
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });

    it('shows sensitive data warning on YAML export', async () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText(/redacted in the export/i)).toBeInTheDocument();

      // Let mount-time fetches settle.
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });

    it('shows sensitive data warning on full backup', async () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText(/contains sensitive data/i)).toBeInTheDocument();

      // Let mount-time fetches settle.
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });

    it('renders file input for zip files', async () => {
      render(<BackupRestoreSection isAdmin={true} />);
      const fileInput = document.querySelector('input[type="file"][accept=".zip"]');
      expect(fileInput).toBeInTheDocument();

      // Let mount-time fetches settle.
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });

    it('shows warning about full restore replacing data', async () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText(/replace all current settings/i)).toBeInTheDocument();

      // Let mount-time fetches settle.
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });
  });

  describe('YAML export', () => {
    it('triggers YAML download on button click', async () => {
      const mockBlob = new Blob(['yaml-data'], { type: 'text/yaml' });
      vi.mocked(api.exportBackup).mockResolvedValue(mockBlob);

      const mockUrl = 'blob:http://test/mock-url';
      globalThis.URL.createObjectURL = vi.fn(() => mockUrl);
      globalThis.URL.revokeObjectURL = vi.fn();

      render(<BackupRestoreSection isAdmin={true} />);

      // Wait for sections to load before clicking
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Export YAML'));

      await waitFor(() => {
        expect(api.exportBackup).toHaveBeenCalled();
      });

      await waitFor(() => {
        expect(mockSuccess).toHaveBeenCalledWith('YAML export downloaded successfully');
      });
    });

    it('shows error on export failure', async () => {
      vi.mocked(api.exportBackup).mockRejectedValue(new Error('Export failed'));

      render(<BackupRestoreSection isAdmin={true} />);

      // Wait for sections to load
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Export YAML'));

      await waitFor(() => {
        expect(mockError).toHaveBeenCalledWith('Export failed', 'Export Failed');
      });
    });

    it('renders section checkboxes', async () => {
      render(<BackupRestoreSection isAdmin={true} />);

      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
        expect(screen.getByText('Tag Groups')).toBeInTheDocument();
        expect(screen.getByText('FFmpeg Profiles')).toBeInTheDocument();
      });

      // All should be pre-selected
      const checkboxes = screen.getAllByRole('checkbox');
      expect(checkboxes.filter(cb => (cb as HTMLInputElement).checked).length).toBe(3);
    });
  });

  describe('YAML restore modal', () => {
    it('opens restore modal on button click', async () => {
      render(<BackupRestoreSection isAdmin={true} />);
      fireEvent.click(screen.getByText('Restore from YAML...'));

      expect(screen.getByTestId('backup-restore-modal')).toBeInTheDocument();

      // Let mount-time fetches settle (getExportSections + listSavedBackups).
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });

    it('closes restore modal', async () => {
      render(<BackupRestoreSection isAdmin={true} />);
      fireEvent.click(screen.getByText('Restore from YAML...'));
      fireEvent.click(screen.getByText('Close Modal'));

      expect(screen.queryByTestId('backup-restore-modal')).not.toBeInTheDocument();

      // Let mount-time fetches settle.
      await waitFor(() => {
        expect(screen.getByText('Settings')).toBeInTheDocument();
      });
    });
  });

  describe('full backup download', () => {
    it('triggers download on button click', async () => {
      const mockBlob = new Blob(['zip-data'], { type: 'application/zip' });
      const mockResponse = {
        ok: true,
        blob: vi.fn().mockResolvedValue(mockBlob),
        headers: new Headers({
          'Content-Disposition': 'attachment; filename="ecm-backup-2026-01-01.zip"',
        }),
      };
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(mockResponse);

      const mockUrl = 'blob:http://test/mock-url';
      globalThis.URL.createObjectURL = vi.fn(() => mockUrl);
      globalThis.URL.revokeObjectURL = vi.fn();

      render(<BackupRestoreSection isAdmin={true} />);
      fireEvent.click(screen.getByText('Download Full Backup'));

      await waitFor(() => {
        expect(globalThis.fetch).toHaveBeenCalledWith('/api/backup/create');
      });

      await waitFor(() => {
        expect(mockSuccess).toHaveBeenCalledWith('Backup downloaded successfully');
      });
    });

    it('shows error on download failure', async () => {
      (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
        ok: false,
      });

      render(<BackupRestoreSection isAdmin={true} />);
      fireEvent.click(screen.getByText('Download Full Backup'));

      await waitFor(() => {
        expect(mockError).toHaveBeenCalled();
      });
    });
  });

  describe('full zip restore', () => {
    it('shows error when no file selected', async () => {
      render(<BackupRestoreSection isAdmin={true} />);
      fireEvent.click(screen.getByText('Restore'));

      await waitFor(() => {
        expect(mockError).toHaveBeenCalledWith('Please select a backup file', 'No File Selected');
      });
    });

    it('calls restoreBackup on valid file upload', async () => {
      const mockResult = {
        status: 'ok',
        backup_version: '0.15.0',
        backup_date: '2026-01-01T00:00:00Z',
        restored_files: ['settings.json', 'journal.db'],
      };
      vi.mocked(api.restoreBackup).mockResolvedValue(mockResult);

      const reloadMock = vi.fn();
      Object.defineProperty(window, 'location', {
        value: { ...window.location, reload: reloadMock },
        writable: true,
      });

      render(<BackupRestoreSection isAdmin={true} />);

      const file = new File(['zip-content'], 'backup.zip', { type: 'application/zip' });
      const input = document.querySelector('input[type="file"]') as HTMLInputElement;
      Object.defineProperty(input, 'files', { value: [file] });

      fireEvent.click(screen.getByText('Restore'));

      await waitFor(() => {
        expect(api.restoreBackup).toHaveBeenCalledWith(file);
      });

      await waitFor(() => {
        expect(mockSuccess).toHaveBeenCalledWith('Restored 2 files from backup');
      });
    });

    it('shows error on restore failure', async () => {
      vi.mocked(api.restoreBackup).mockRejectedValue(new Error('Server error'));

      render(<BackupRestoreSection isAdmin={true} />);

      const file = new File(['zip-content'], 'backup.zip', { type: 'application/zip' });
      const input = document.querySelector('input[type="file"]') as HTMLInputElement;
      Object.defineProperty(input, 'files', { value: [file] });

      fireEvent.click(screen.getByText('Restore'));

      await waitFor(() => {
        expect(mockError).toHaveBeenCalledWith('Server error', 'Restore Failed');
      });
    });
  });

  describe('restore from saved backup (bead rzhid)', () => {
    const savedZip = {
      filename: 'ecm-backup-2026-01-01_000000.zip',
      size_bytes: 1024,
      created_at: '2026-01-01T00:00:00Z',
      type: 'zip' as const,
    };
    const savedYaml = {
      filename: 'ecm-backup-2026-01-02_000000.yaml',
      size_bytes: 512,
      created_at: '2026-01-02T00:00:00Z',
      type: 'yaml' as const,
    };

    it('shows legacy and DBAS restore buttons only for zip saved backups', async () => {
      vi.mocked(api.listSavedBackups).mockResolvedValue([savedZip, savedYaml]);

      render(<BackupRestoreSection isAdmin={true} />);

      await waitFor(() => {
        expect(screen.getByText(savedZip.filename)).toBeInTheDocument();
      });

      expect(screen.getByLabelText('Restore as legacy full backup')).toBeInTheDocument();
      expect(screen.getByLabelText('Restore as DBAS backup')).toBeInTheDocument();
    });

    it('opens a type-to-confirm dialog for legacy restore and requires exact filename', async () => {
      vi.mocked(api.listSavedBackups).mockResolvedValue([savedZip]);
      const mockResult = {
        status: 'ok',
        filename: savedZip.filename,
        backup_version: '0.15.0',
        backup_date: '2026-01-01T00:00:00Z',
        restored_files: ['settings.json'],
      };
      vi.mocked(api.restoreSavedBackup).mockResolvedValue(mockResult);

      render(<BackupRestoreSection isAdmin={true} />);
      await waitFor(() => screen.getByLabelText('Restore as legacy full backup'));

      fireEvent.click(screen.getByLabelText('Restore as legacy full backup'));

      const confirmBtn = screen.getByRole('button', { name: 'Restore this backup' });
      expect(confirmBtn).toBeDisabled();
      expect(api.restoreSavedBackup).not.toHaveBeenCalled();

      const input = screen.getByLabelText(/type/i);
      fireEvent.change(input, { target: { value: savedZip.filename } });
      fireEvent.click(confirmBtn);

      await waitFor(() => {
        expect(api.restoreSavedBackup).toHaveBeenCalledWith(savedZip.filename);
      });
    });

    it('cancelling the legacy restore dialog does not call the API', async () => {
      vi.mocked(api.listSavedBackups).mockResolvedValue([savedZip]);

      render(<BackupRestoreSection isAdmin={true} />);
      await waitFor(() => screen.getByLabelText('Restore as legacy full backup'));

      fireEvent.click(screen.getByLabelText('Restore as legacy full backup'));
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

      expect(api.restoreSavedBackup).not.toHaveBeenCalled();
      expect(screen.queryByLabelText(/type/i)).not.toBeInTheDocument();
    });

    it('opens the DBAS-saved restore modal with the clicked filename', async () => {
      vi.mocked(api.listSavedBackups).mockResolvedValue([savedZip]);

      render(<BackupRestoreSection isAdmin={true} />);
      await waitFor(() => screen.getByLabelText('Restore as DBAS backup'));

      fireEvent.click(screen.getByLabelText('Restore as DBAS backup'));

      expect(screen.getByTestId('dbas-restore-saved-modal')).toHaveTextContent(savedZip.filename);

      fireEvent.click(screen.getByText('Close DBAS Modal'));
      expect(screen.queryByTestId('dbas-restore-saved-modal')).not.toBeInTheDocument();
    });
  });

  describe('backup destination policy (relocated from Security page, bead 09x38.12)', () => {
    it('does not persist on radio click — only on explicit Save', async () => {
      render(<BackupRestoreSection isAdmin={true} />);

      await waitFor(() => {
        expect(screen.getByTestId('outbound-mode-public_only')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId('outbound-mode-public_only'));
      expect(api.saveSecurityMode).not.toHaveBeenCalled();

      fireEvent.click(screen.getByTestId('outbound-policy-save'));
      await waitFor(() => {
        expect(api.saveSecurityMode).toHaveBeenCalledWith('public_only');
      });
    });
  });
});
