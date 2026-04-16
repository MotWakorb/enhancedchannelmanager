/**
 * Unit tests for BackupRestoreModal component.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BackupRestoreModal } from './BackupRestoreModal';
import type { BackupValidation, BackupRestoreResult } from '../services/api';

// Mock the API module
vi.mock('../services/api', () => ({
  validateBackup: vi.fn(),
  restoreBackupYaml: vi.fn(),
}));

import * as api from '../services/api';

const mockValidation: BackupValidation = {
  valid: true,
  version: '0.16.0',
  exported_at: '2026-01-01T00:00:00+00:00',
  sections: [
    { key: 'settings', label: 'Settings', item_count: 10, available: true },
    { key: 'scheduled_tasks', label: 'Scheduled Tasks', item_count: 3, available: true },
    { key: 'tag_groups', label: 'Tag Groups', item_count: 2, available: true },
    { key: 'ffmpeg_profiles', label: 'FFmpeg Profiles', item_count: 0, available: false },
  ],
};

const mockRestoreResult: BackupRestoreResult = {
  success: true,
  sections_restored: ['settings', 'scheduled_tasks', 'tag_groups'],
  sections_failed: [],
  warnings: ['Skipped redacted field: password (kept existing value)'],
  errors: [],
};

describe('BackupRestoreModal', () => {
  const mockClose = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('upload step', () => {
    it('renders dropzone', () => {
      render(<BackupRestoreModal onClose={mockClose} />);
      expect(screen.getByText(/drag & drop/i)).toBeInTheDocument();
      expect(screen.getByText('.yaml or .yml files')).toBeInTheDocument();
    });

    it('renders cancel button', () => {
      render(<BackupRestoreModal onClose={mockClose} />);
      expect(screen.getByText('Cancel')).toBeInTheDocument();
    });

    it('calls onClose when cancel clicked', () => {
      render(<BackupRestoreModal onClose={mockClose} />);
      fireEvent.click(screen.getByText('Cancel'));
      expect(mockClose).toHaveBeenCalled();
    });

    it('shows error for non-yaml file', async () => {
      render(<BackupRestoreModal onClose={mockClose} />);

      const file = new File(['data'], 'backup.zip', { type: 'application/zip' });
      const dropzone = screen.getByText(/drag & drop/i).closest('div')!;

      fireEvent.drop(dropzone, {
        dataTransfer: { files: [file] },
      });

      await waitFor(() => {
        expect(screen.getByText(/please select a .yaml or .yml file/i)).toBeInTheDocument();
      });
    });
  });

  describe('section selection step', () => {
    async function renderWithFile() {
      vi.mocked(api.validateBackup).mockResolvedValue(mockValidation);
      render(<BackupRestoreModal onClose={mockClose} />);

      // Simulate file drop
      const file = new File(['yaml-data'], 'export.yaml', { type: 'text/yaml' });
      const dropzone = screen.getByText(/drag & drop/i).closest('div')!;

      fireEvent.drop(dropzone, {
        dataTransfer: { files: [file] },
      });

      await waitFor(() => {
        expect(screen.getByText('export.yaml')).toBeInTheDocument();
      });
    }

    it('shows file info after upload', async () => {
      await renderWithFile();
      expect(screen.getByText('export.yaml')).toBeInTheDocument();
      expect(screen.getByText(/ECM v0.16.0/)).toBeInTheDocument();
    });

    it('shows section checkboxes', async () => {
      await renderWithFile();
      expect(screen.getByText('Settings')).toBeInTheDocument();
      expect(screen.getByText('Scheduled Tasks')).toBeInTheDocument();
      expect(screen.getByText('Tag Groups')).toBeInTheDocument();
      expect(screen.getByText('FFmpeg Profiles')).toBeInTheDocument();
    });

    it('pre-selects available sections', async () => {
      await renderWithFile();
      const checkboxes = screen.getAllByRole('checkbox');
      // 3 available sections should be checked
      const checked = checkboxes.filter((cb) => (cb as HTMLInputElement).checked);
      expect(checked.length).toBe(3);
    });

    it('disables unavailable sections', async () => {
      await renderWithFile();
      const checkboxes = screen.getAllByRole('checkbox');
      const disabled = checkboxes.filter((cb) => (cb as HTMLInputElement).disabled);
      expect(disabled.length).toBe(1); // ffmpeg_profiles
    });

    it('shows item counts', async () => {
      await renderWithFile();
      expect(screen.getByText('10 items')).toBeInTheDocument();
      expect(screen.getByText('3 items')).toBeInTheDocument();
      expect(screen.getByText('empty')).toBeInTheDocument();
    });

    it('has select all/none buttons', async () => {
      await renderWithFile();
      expect(screen.getByText('Select all')).toBeInTheDocument();
      expect(screen.getByText('Select none')).toBeInTheDocument();
    });

    it('select none unchecks all', async () => {
      await renderWithFile();
      fireEvent.click(screen.getByText('Select none'));

      const checkboxes = screen.getAllByRole('checkbox');
      const checked = checkboxes.filter((cb) => (cb as HTMLInputElement).checked);
      expect(checked.length).toBe(0);
    });

    it('toggles individual sections', async () => {
      await renderWithFile();
      const settingsCheckbox = screen.getAllByRole('checkbox')[0];
      fireEvent.click(settingsCheckbox);

      expect((settingsCheckbox as HTMLInputElement).checked).toBe(false);
    });

    it('disables restore button when no sections selected', async () => {
      await renderWithFile();
      fireEvent.click(screen.getByText('Select none'));

      const restoreBtn = screen.getByText(/^Restore 0/);
      expect(restoreBtn).toBeDisabled();
    });
  });

  describe('restore execution', () => {
    it('shows results after successful restore', async () => {
      vi.mocked(api.validateBackup).mockResolvedValue(mockValidation);
      vi.mocked(api.restoreBackupYaml).mockResolvedValue(mockRestoreResult);

      render(<BackupRestoreModal onClose={mockClose} />);

      // Drop file
      const file = new File(['yaml-data'], 'export.yaml', { type: 'text/yaml' });
      const dropzone = screen.getByText(/drag & drop/i).closest('div')!;
      fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

      await waitFor(() => {
        expect(screen.getByText('export.yaml')).toBeInTheDocument();
      });

      // Click restore
      fireEvent.click(screen.getByText(/^Restore 3/));

      await waitFor(() => {
        expect(screen.getByText(/Successfully restored 3 section/)).toBeInTheDocument();
      });

      // Check restored sections shown
      expect(screen.getByText('Restored')).toBeInTheDocument();

      // Check warnings shown
      expect(screen.getByText('Warnings')).toBeInTheDocument();
      expect(screen.getByText(/Skipped redacted field/)).toBeInTheDocument();

      // Done button
      expect(screen.getByText('Done')).toBeInTheDocument();
    });

    it('shows partial failure results', async () => {
      vi.mocked(api.validateBackup).mockResolvedValue(mockValidation);
      vi.mocked(api.restoreBackupYaml).mockResolvedValue({
        success: false,
        sections_restored: ['settings'],
        sections_failed: ['scheduled_tasks'],
        warnings: [],
        errors: ['scheduled_tasks: DB connection error'],
      });

      render(<BackupRestoreModal onClose={mockClose} />);

      const file = new File(['yaml-data'], 'export.yaml', { type: 'text/yaml' });
      const dropzone = screen.getByText(/drag & drop/i).closest('div')!;
      fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

      await waitFor(() => {
        expect(screen.getByText('export.yaml')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText(/^Restore 3/));

      await waitFor(() => {
        expect(screen.getByText(/Restored 1 section.*1 failed/)).toBeInTheDocument();
      });

      expect(screen.getByText('Failed')).toBeInTheDocument();
      expect(screen.getByText('Errors')).toBeInTheDocument();
      expect(screen.getByText(/DB connection error/)).toBeInTheDocument();
    });

    it('handles restore API error', async () => {
      vi.mocked(api.validateBackup).mockResolvedValue(mockValidation);
      vi.mocked(api.restoreBackupYaml).mockRejectedValue(new Error('Network error'));

      render(<BackupRestoreModal onClose={mockClose} />);

      const file = new File(['yaml-data'], 'export.yaml', { type: 'text/yaml' });
      const dropzone = screen.getByText(/drag & drop/i).closest('div')!;
      fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

      await waitFor(() => {
        expect(screen.getByText('export.yaml')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText(/^Restore 3/));

      await waitFor(() => {
        expect(screen.getByText('Network error')).toBeInTheDocument();
      });

      // Should go back to select step (not stuck on restoring)
      expect(screen.getByText('Select all')).toBeInTheDocument();
    });
  });
});
