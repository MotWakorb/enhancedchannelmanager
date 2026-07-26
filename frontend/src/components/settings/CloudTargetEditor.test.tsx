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
      insecure: false,
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

describe('CloudTargetEditor — WebDAV TLS verification opt-out (PR #743 item 2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function selectWebdav() {
    openProviderDropdown();
    fireEvent.click(screen.getByRole('option', { name: /WebDAV/ }));
  }

  it('shows the advanced insecure checkbox with a warning for WebDAV only', () => {
    renderEditor();
    // Default provider is S3 — no TLS opt-out affordance.
    expect(screen.queryByTestId('cloud-target-insecure')).not.toBeInTheDocument();

    selectWebdav();
    const checkbox = screen.getByTestId('cloud-target-insecure');
    expect(checkbox).not.toBeChecked();
    // A warning names the risk in plain language — the opt-out is never silent.
    expect(screen.getByTestId('cloud-target-insecure-warning')).toHaveTextContent(
      /intercept/i,
    );
  });

  it('sends the top-level insecure flag on create (never inside credentials)', async () => {
    const cloudApi = await import('../../services/cloudTargetsApi');
    renderEditor();
    selectWebdav();

    fireEvent.change(screen.getByPlaceholderText('My S3 Bucket'), { target: { value: 'NAS' } });
    const urlGroup = screen.getByText('WebDAV URL').closest('.modal-form-group');
    fireEvent.change(urlGroup!.querySelector('input')!, {
      target: { value: 'https://nas.local/dav' },
    });
    fireEvent.click(screen.getByTestId('cloud-target-insecure'));
    fireEvent.click(screen.getByRole('button', { name: 'Create Target' }));

    await vi.waitFor(() => expect(cloudApi.createCloudTarget).toHaveBeenCalled());
    const payload = vi.mocked(cloudApi.createCloudTarget).mock.calls[0][0];
    expect(payload.insecure).toBe(true);
    expect((payload.credentials as Record<string, unknown>).insecure).toBeUndefined();
  });

  it('threads the flag into the inline connection test (test == upload policy)', async () => {
    const cloudApi = await import('../../services/cloudTargetsApi');
    renderEditor();
    selectWebdav();

    const urlGroup = screen.getByText('WebDAV URL').closest('.modal-form-group');
    fireEvent.change(urlGroup!.querySelector('input')!, {
      target: { value: 'https://nas.local/dav' },
    });
    fireEvent.click(screen.getByTestId('cloud-target-insecure'));
    fireEvent.click(screen.getByRole('button', { name: /Test Connection/ }));

    await vi.waitFor(() =>
      expect(cloudApi.testCloudConnectionInline).toHaveBeenCalled(),
    );
    const payload = vi.mocked(cloudApi.testCloudConnectionInline).mock.calls[0][0];
    expect(payload.insecure).toBe(true);
  });

  it('pre-checks the checkbox when editing a target saved with insecure=true', () => {
    const target: CloudTarget = {
      id: 9,
      name: 'Self-signed NAS',
      provider_type: 'webdav',
      credentials: {},
      upload_path: '/backups',
      enabled: true,
      insecure: true,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };
    renderEditor(target);
    expect(screen.getByTestId('cloud-target-insecure')).toBeChecked();
  });
});
