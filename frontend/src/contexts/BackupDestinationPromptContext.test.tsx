/**
 * Tests for BackupDestinationPromptContext (bead s5a3o).
 *
 * Contract: the backup-destination first-run choice must NOT appear on login /
 * app load. It appears only when a consumer calls promptBackupDestination()
 * (the cloud-target-add and schedule-enable triggers), and only if the operator
 * has not already answered (localStorage flag unset). Once answered it is never
 * re-prompted.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

vi.mock('../services/api', () => ({
  saveSecurityMode: vi.fn(),
}));

const notify = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

import * as api from '../services/api';
import { SECURITY_FIRST_RUN_KEY } from '../components/SecurityFirstRunModal';
import {
  BackupDestinationPromptProvider,
  useBackupDestinationPrompt,
} from './BackupDestinationPromptContext';

type Mock = ReturnType<typeof vi.fn>;

/** A tiny consumer that fires the trigger when its button is clicked. */
function TriggerConsumer() {
  const { promptBackupDestination } = useBackupDestinationPrompt();
  return (
    <button data-testid="fire-trigger" onClick={() => promptBackupDestination()}>
      configure backups
    </button>
  );
}

function renderWithProvider() {
  return render(
    <BackupDestinationPromptProvider>
      <TriggerConsumer />
    </BackupDestinationPromptProvider>,
  );
}

describe('BackupDestinationPromptContext', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    (api.saveSecurityMode as Mock).mockResolvedValue({ ssrf_outbound_mode: 'lan_friendly' });
  });

  it('does NOT render the modal on mount / app load', () => {
    renderWithProvider();
    expect(screen.queryByTestId('security-first-run-modal')).not.toBeInTheDocument();
  });

  it('opens the modal when triggered and the operator has not answered', () => {
    renderWithProvider();
    fireEvent.click(screen.getByTestId('fire-trigger'));
    expect(screen.getByTestId('security-first-run-modal')).toBeInTheDocument();
  });

  it('is a no-op when the operator has already answered (flag set)', () => {
    localStorage.setItem(SECURITY_FIRST_RUN_KEY, '1');
    renderWithProvider();
    fireEvent.click(screen.getByTestId('fire-trigger'));
    expect(screen.queryByTestId('security-first-run-modal')).not.toBeInTheDocument();
  });

  it('closes after the operator answers and never re-prompts', async () => {
    renderWithProvider();
    fireEvent.click(screen.getByTestId('fire-trigger'));
    // Answer by confirming the default.
    fireEvent.click(screen.getByTestId('security-first-run-confirm'));
    await waitFor(() =>
      expect(screen.queryByTestId('security-first-run-modal')).not.toBeInTheDocument(),
    );
    expect(localStorage.getItem(SECURITY_FIRST_RUN_KEY)).toBe('1');

    // A subsequent trigger must not re-open it.
    fireEvent.click(screen.getByTestId('fire-trigger'));
    expect(screen.queryByTestId('security-first-run-modal')).not.toBeInTheDocument();
  });

  it('throws when used outside the provider', () => {
    // Silence the expected React error boundary console noise.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => render(<TriggerConsumer />)).toThrow(
      /must be used within a BackupDestinationPromptProvider/,
    );
    spy.mockRestore();
  });
});
