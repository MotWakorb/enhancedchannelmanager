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

import * as api from '../../services/api';

describe('BackupRestoreSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Mock fetch for backup download
    global.fetch = vi.fn();
  });

  describe('when not admin', () => {
    it('shows no-access message', () => {
      render(<BackupRestoreSection isAdmin={false} />);
      expect(screen.getByText(/only administrators/i)).toBeInTheDocument();
    });

    it('does not show backup or restore buttons', () => {
      render(<BackupRestoreSection isAdmin={false} />);
      expect(screen.queryByText('Download Backup')).not.toBeInTheDocument();
      expect(screen.queryByText('Restore')).not.toBeInTheDocument();
    });
  });

  describe('when admin', () => {
    it('renders backup and restore sections', () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText('Create Backup')).toBeInTheDocument();
      expect(screen.getByText('Restore from Backup')).toBeInTheDocument();
    });

    it('renders download backup button', () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText('Download Backup')).toBeInTheDocument();
    });

    it('renders restore button', () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText('Restore')).toBeInTheDocument();
    });

    it('renders file input for zip files', () => {
      render(<BackupRestoreSection isAdmin={true} />);
      const fileInput = screen.getByAcceptingFiles('.zip');
      expect(fileInput).toBeInTheDocument();
    });

    it('shows warning about restore replacing data', () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText(/replace all current settings/i)).toBeInTheDocument();
    });

    it('renders page header', () => {
      render(<BackupRestoreSection isAdmin={true} />);
      expect(screen.getByText('Backup & Restore')).toBeInTheDocument();
    });
  });

  describe('backup download', () => {
    it('triggers download on button click', async () => {
      const mockBlob = new Blob(['zip-data'], { type: 'application/zip' });
      const mockResponse = {
        ok: true,
        blob: vi.fn().mockResolvedValue(mockBlob),
        headers: new Headers({
          'Content-Disposition': 'attachment; filename="ecm-backup-2026-01-01.zip"',
        }),
      };
      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(mockResponse);

      // Mock URL.createObjectURL and revokeObjectURL
      const mockUrl = 'blob:http://test/mock-url';
      global.URL.createObjectURL = vi.fn(() => mockUrl);
      global.URL.revokeObjectURL = vi.fn();

      render(<BackupRestoreSection isAdmin={true} />);
      fireEvent.click(screen.getByText('Download Backup'));

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith('/api/backup/create');
      });

      await waitFor(() => {
        expect(mockSuccess).toHaveBeenCalledWith('Backup downloaded successfully');
      });
    });

    it('shows error on download failure', async () => {
      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
        ok: false,
      });

      render(<BackupRestoreSection isAdmin={true} />);
      fireEvent.click(screen.getByText('Download Backup'));

      await waitFor(() => {
        expect(mockError).toHaveBeenCalled();
      });
    });
  });

  describe('restore', () => {
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

      // Prevent actual reload
      const reloadMock = vi.fn();
      Object.defineProperty(window, 'location', {
        value: { ...window.location, reload: reloadMock },
        writable: true,
      });

      render(<BackupRestoreSection isAdmin={true} />);

      // Simulate file selection
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
});

// Custom matcher helper for file input
function getByAcceptingFiles(accept: string) {
  return document.querySelector(`input[type="file"][accept="${accept}"]`);
}

// Extend screen with custom query
Object.defineProperty(screen, 'getByAcceptingFiles', {
  value: (accept: string) => {
    const el = getByAcceptingFiles(accept);
    if (!el) throw new Error(`No file input found with accept="${accept}"`);
    return el;
  },
});
