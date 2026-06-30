/**
 * Integration test for the cloud-target-add backup-destination trigger (bead s5a3o).
 *
 * Adding/saving a cloud upload target is trigger (B): the moment the operator
 * commits a target, the backup-destination first-run choice appears — but only
 * if they have not already answered it. Proves the CloudTargetsCard → provider
 * wiring end to end through the real BackupDestinationPromptProvider.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

const notify = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

vi.mock('../../services/cloudTargetsApi', () => ({
  getCloudTargets: vi.fn().mockResolvedValue([]),
  createCloudTarget: vi.fn().mockResolvedValue({ id: 1 }),
  updateCloudTarget: vi.fn().mockResolvedValue({ id: 1 }),
  deleteCloudTarget: vi.fn().mockResolvedValue(undefined),
  testCloudTarget: vi.fn().mockResolvedValue({ success: true }),
  testCloudConnectionInline: vi.fn().mockResolvedValue({ success: true }),
}));

// The SecurityFirstRunModal (rendered by the provider) PATCHes the safe default
// via api.saveSecurityMode on dismissal; stub it so no real network call fires.
vi.mock('../../services/api', () => ({
  saveSecurityMode: vi.fn().mockResolvedValue({ ssrf_outbound_mode: 'lan_friendly' }),
}));

import { SECURITY_FIRST_RUN_KEY } from '../SecurityFirstRunModal';
import { BackupDestinationPromptProvider } from '../../contexts/BackupDestinationPromptContext';
import { CloudTargetsCard } from './CloudTargetsCard';

/** Find the <input>/<textarea> inside the form group whose label matches. */
function inputForLabel(label: RegExp): HTMLElement {
  const labelEl = screen.getByText(label);
  const group = labelEl.closest('.modal-form-group');
  const field = group?.querySelector('input, textarea');
  if (!field) throw new Error(`no field for label ${label}`);
  return field as HTMLElement;
}

async function addAnS3Target() {
  // Empty state → "Add Cloud Target".
  fireEvent.click(await screen.findByText('Add Cloud Target'));
  // Fill the required S3 fields (default provider is s3).
  fireEvent.change(inputForLabel(/^Name$/), { target: { value: 'My Bucket' } });
  fireEvent.change(inputForLabel(/^Bucket Name/), { target: { value: 'my-bucket' } });
  fireEvent.change(inputForLabel(/^Access Key ID/), { target: { value: 'AKIA' } });
  fireEvent.change(inputForLabel(/^Secret Access Key/), { target: { value: 'secret' } });
  fireEvent.click(screen.getByText('Create Target'));
}

function renderCard() {
  return render(
    <BackupDestinationPromptProvider>
      <CloudTargetsCard />
    </BackupDestinationPromptProvider>,
  );
}

describe('CloudTargetsCard — backup-destination trigger (s5a3o)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('opens the backup-destination choice after saving a cloud target (flag unset)', async () => {
    renderCard();
    await addAnS3Target();
    expect(await screen.findByTestId('security-first-run-modal')).toBeInTheDocument();
  });

  it('does NOT open the choice when the operator has already answered (flag set)', async () => {
    localStorage.setItem(SECURITY_FIRST_RUN_KEY, '1');
    renderCard();
    await addAnS3Target();
    // Editor closes on save; give any async open a chance, then assert absence.
    await waitFor(() => expect(screen.queryByText('Create Target')).not.toBeInTheDocument());
    expect(screen.queryByTestId('security-first-run-modal')).not.toBeInTheDocument();
  });
});
