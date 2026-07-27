/**
 * Tests for the Encrypted Backup (Migration) card (ADR-012 D12 / bead u81kh).
 *
 * Contracts under test:
 *   - The lost-passphrase HARD GATE: "Create" stays disabled until the
 *     acknowledge-unrecoverable checkbox is ticked AND the passphrase is valid
 *     (>= 12 chars, both fields match). Not a tooltip — a real disabled gate.
 *   - A < 12-char passphrase surfaces an inline error and keeps the gate shut.
 *   - A successful create calls runTask('dbas_backup', ...) with the exact
 *     encryption run-parameters, and clears the passphrase fields afterwards.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('../../services/api', () => ({
  runTask: vi.fn(),
}));

const notify = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

import * as api from '../../services/api';
import { EncryptedBackupCard } from './EncryptedBackupCard';

const VALID_PASS = 'a-strong-passphrase';

function setPass(value: string, confirm: string = value) {
  fireEvent.change(screen.getByLabelText('Passphrase'), { target: { value } });
  fireEvent.change(screen.getByLabelText('Confirm passphrase'), { target: { value: confirm } });
}

function createBtn() {
  return screen.getByRole('button', { name: /Create Encrypted Backup/i });
}

function ackCheckbox() {
  return screen.getByLabelText(/lost passphrase means this backup cannot be recovered/i);
}

describe('EncryptedBackupCard', () => {
  beforeEach(() => vi.clearAllMocks());

  it('keeps Create disabled until passphrase valid AND ack ticked', () => {
    render(<EncryptedBackupCard />);
    expect(createBtn()).toBeDisabled();

    setPass(VALID_PASS);            // valid passphrase, but no ack yet
    expect(createBtn()).toBeDisabled();

    fireEvent.click(ackCheckbox()); // hard gate satisfied
    expect(createBtn()).toBeEnabled();
  });

  it('rejects a short passphrase with an inline error and a shut gate', () => {
    render(<EncryptedBackupCard />);
    setPass('short-11-ch');         // 11 chars
    fireEvent.click(ackCheckbox());
    expect(screen.getByText(/at least 12 characters/i)).toBeInTheDocument();
    expect(createBtn()).toBeDisabled();
  });

  it('keeps the gate shut when the confirm field does not match', () => {
    render(<EncryptedBackupCard />);
    setPass(VALID_PASS, 'different-passphrase');
    fireEvent.click(ackCheckbox());
    expect(screen.getByText(/do not match/i)).toBeInTheDocument();
    expect(createBtn()).toBeDisabled();
  });

  it('triggers dbas_backup with the encryption params and clears the passphrase', async () => {
    (api.runTask as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true, message: 'ok' });
    render(<EncryptedBackupCard />);

    setPass(VALID_PASS);
    fireEvent.click(screen.getByLabelText(/Include credentials for migration/i));
    fireEvent.click(ackCheckbox());
    fireEvent.click(createBtn());

    await waitFor(() => expect(api.runTask).toHaveBeenCalledTimes(1));
    expect(api.runTask).toHaveBeenCalledWith('dbas_backup', undefined, {
      passphrase: VALID_PASS,
      include_credentials: true,
      acknowledge_unrecoverable: true,
    });
    // Passphrase cleared from the field after the attempt.
    await waitFor(() =>
      expect((screen.getByLabelText('Passphrase') as HTMLInputElement).value).toBe(''),
    );
    expect(notify.success).toHaveBeenCalled();
  });
});
