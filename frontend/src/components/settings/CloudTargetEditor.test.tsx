/**
 * CloudTargetEditor provider affordances (bead 0i2vt.8).
 *
 * - WebDAV is offered as a first-class provider and selecting it renders its
 *   credential form (WebDAV URL / Username / Password).
 * - OneDrive and Dropbox are greyed out and labeled "not yet supported" in the
 *   create-target dropdown (PO decision 2026-07-25) — selecting them is a no-op.
 * - An existing OneDrive target still renders its editor (edit/delete keep
 *   working; only new-target creation gets the disabled affordance).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const notify = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

vi.mock('../../services/cloudTargetsApi', () => ({
  createCloudTarget: vi.fn().mockResolvedValue({ id: 1 }),
  updateCloudTarget: vi.fn().mockResolvedValue({ id: 1 }),
  testCloudTarget: vi.fn().mockResolvedValue({ success: true }),
  testCloudConnectionInline: vi.fn().mockResolvedValue({ success: true }),
}));

import type { CloudTarget } from '../../types/cloudTargets';
import { CloudTargetEditor } from './CloudTargetEditor';

function renderEditor(target: CloudTarget | null = null) {
  return render(
    <CloudTargetEditor target={target} onClose={vi.fn()} onSaved={vi.fn()} />,
  );
}

function openProviderDropdown() {
  // The provider CustomSelect is the only select in the modal; its trigger
  // shows the current provider label (default: the S3 option).
  fireEvent.click(screen.getByRole('button', { name: /Amazon S3/ }));
}

describe('CloudTargetEditor — provider affordances (0i2vt.8)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('offers WebDAV as a selectable provider with its credential fields', () => {
    renderEditor();
    openProviderDropdown();

    const webdavOption = screen.getByRole('option', { name: /WebDAV/ });
    expect(webdavOption).not.toHaveClass('disabled');
    fireEvent.click(webdavOption);

    expect(screen.getByText('WebDAV URL')).toBeInTheDocument();
    expect(screen.getByText('Username')).toBeInTheDocument();
    expect(screen.getByText('Password')).toBeInTheDocument();
    // base_url is required for a new target.
    const urlGroup = screen.getByText('WebDAV URL').closest('.modal-form-group');
    expect(urlGroup?.querySelector('.modal-required')).not.toBeNull();
  });

  it('greys out OneDrive and Dropbox as "not yet supported" and refuses selection', () => {
    renderEditor();
    openProviderDropdown();

    for (const label of [/OneDrive \(not yet supported\)/, /Dropbox \(not yet supported\)/]) {
      const option = screen.getByRole('option', { name: label });
      expect(option).toHaveClass('disabled');
      expect(option).toHaveAttribute('aria-disabled', 'true');
    }

    // Clicking a disabled option is a no-op — the S3 form stays rendered.
    fireEvent.click(screen.getByRole('option', { name: /OneDrive/ }));
    expect(screen.getByText('Bucket Name')).toBeInTheDocument();
    expect(screen.queryByText('Tenant ID')).not.toBeInTheDocument();
  });

  it('still renders the editor for an existing OneDrive target', () => {
    const target: CloudTarget = {
      id: 7,
      name: 'Legacy OneDrive',
      provider_type: 'onedrive',
      credentials: {},
      upload_path: '/legacy',
      enabled: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };
    renderEditor(target);

    expect(screen.getByText('Edit Cloud Target')).toBeInTheDocument();
    // The OneDrive credential form still renders so the row stays editable.
    expect(screen.getByText('Tenant ID')).toBeInTheDocument();
    // Provider cannot be changed while editing (pre-existing behavior).
    const trigger = screen.getByRole('button', { name: /OneDrive/ });
    expect(trigger).toBeDisabled();
  });
});
