/**
 * Unit tests for TypeToConfirmDialog (enhancedchannelmanager-rzhid).
 */
import { describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { TypeToConfirmDialog } from './TypeToConfirmDialog';

describe('TypeToConfirmDialog', () => {
  it('owns distinct title and confirmation-input labels when two dialogs render', () => {
    render(
      <>
        <TypeToConfirmDialog
          title="First action"
          message="First warning."
          confirmText="FIRST"
          onCancel={vi.fn()}
          onConfirm={vi.fn()}
        />
        <TypeToConfirmDialog
          title="Second action"
          message="Second warning."
          confirmText="SECOND"
          onCancel={vi.fn()}
          onConfirm={vi.fn()}
        />
      </>
    );

    const dialogs = [
      screen.getByRole('dialog', { name: 'First action' }),
      screen.getByRole('dialog', { name: 'Second action' }),
    ];
    const inputs = [
      screen.getByRole('textbox', { name: /type first to confirm/i }),
      screen.getByRole('textbox', { name: /type second to confirm/i }),
    ];

    expect(dialogs[0].getAttribute('aria-labelledby')).not.toBe(
      dialogs[1].getAttribute('aria-labelledby')
    );
    expect(inputs[0].id).toBeTruthy();
    expect(inputs[1].id).toBeTruthy();
    expect(inputs[0].id).not.toBe(inputs[1].id);
    expect(document.querySelector(`label[for="${inputs[0].id}"]`)).toHaveTextContent(
      'Type FIRST to confirm'
    );
    expect(document.querySelector(`label[for="${inputs[1].id}"]`)).toHaveTextContent(
      'Type SECOND to confirm'
    );
  });

  it('is a named modal dialog, focuses its confirmation input, and handles Escape unless busy', async () => {
    const onCancel = vi.fn();
    const { rerender } = render(
      <TypeToConfirmDialog
        title="Restore Backup"
        message="Danger."
        confirmText="CONFIRM"
        onCancel={onCancel}
        onConfirm={vi.fn()}
      />
    );

    const dialog = screen.getByRole('dialog', { name: 'Restore Backup' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    await waitFor(() => expect(screen.getByLabelText(/type/i)).toHaveFocus());
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);

    rerender(
      <TypeToConfirmDialog
        title="Restore Backup"
        message="Danger."
        confirmText="CONFIRM"
        busy
        onCancel={onCancel}
        onConfirm={vi.fn()}
      />
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

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

  it('traps focus and restores it to the opener after close', async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return <><button onClick={() => setOpen(true)}>Open restore</button>{open && <TypeToConfirmDialog title="Restore Backup" message="Danger." confirmText="CONFIRM" onCancel={() => setOpen(false)} onConfirm={vi.fn()} />}</>;
    }
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Open restore' });
    opener.focus();
    fireEvent.click(opener);
    const input = await screen.findByRole('textbox');
    await waitFor(() => expect(input).toHaveFocus());
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    cancel.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(opener).toHaveFocus());
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

  it('focuses its container when busy leaves no eligible control', async () => {
    render(<TypeToConfirmDialog title="Restore Backup" message="Danger." confirmText="CONFIRM" busy onCancel={vi.fn()} onConfirm={vi.fn()} />);
    const dialog = screen.getByRole('dialog', { name: 'Restore Backup' });
    const container = dialog.querySelector('.type-to-confirm-dialog');
    await waitFor(() => expect(container).toHaveFocus());
    expect(container).toHaveAttribute('tabindex', '-1');
  });
});
