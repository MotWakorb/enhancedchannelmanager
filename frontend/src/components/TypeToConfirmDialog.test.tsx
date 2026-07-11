/**
 * Unit tests for TypeToConfirmDialog (enhancedchannelmanager-rzhid).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TypeToConfirmDialog } from './TypeToConfirmDialog';

describe('TypeToConfirmDialog', () => {
  it('disables confirm until the exact text is typed', () => {
    const onConfirm = vi.fn();
    render(
      <TypeToConfirmDialog
        title="Restore Backup"
        message="This will overwrite current data."
        confirmText="ecm-backup-2026.zip"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />
    );

    const confirmBtn = screen.getByRole('button', { name: 'Confirm' });
    expect(confirmBtn).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/type/i), { target: { value: 'wrong' } });
    expect(confirmBtn).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/type/i), { target: { value: 'ecm-backup-2026.zip' } });
    expect(confirmBtn).not.toBeDisabled();

    fireEvent.click(confirmBtn);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('calls onCancel when Cancel is clicked', () => {
    const onCancel = vi.fn();
    render(
      <TypeToConfirmDialog
        title="Restore Backup"
        message="Danger."
        confirmText="CONFIRM"
        onCancel={onCancel}
        onConfirm={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('shows a custom confirm label and disables inputs while busy', () => {
    render(
      <TypeToConfirmDialog
        title="Restore Backup"
        message="Danger."
        confirmText="CONFIRM"
        confirmLabel="Restore now"
        busy
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />
    );

    expect(screen.getByText('Working…')).toBeInTheDocument();
    expect(screen.getByLabelText(/type/i)).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  });
});
