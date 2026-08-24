/**
 * Editing a cloud target's credentials without dropping their siblings
 * (bead …-ybr3u).
 *
 * `PATCH /api/cloud-targets/{id}` REPLACES `credentials` wholesale
 * (`routers/cloud_targets.py` — `target.credentials = encrypt_credentials(...)`),
 * exactly as the sync-target route does. The editor used to tell the operator
 * "Leave fields empty to keep existing values. Only changed fields will be
 * updated" and then submit only the box they touched, blanking `bucket_name`,
 * `region` and the rest. It also prefilled every box from the last-4 MASK,
 * inviting the operator to "keep" a value that is not the secret.
 *
 * Same two rules as bead …-a3lby, which shipped this for sync targets
 * (`SyncTargetsCard.tsx` — `handleSave`):
 *   1. all-or-nothing credential entry — a partial entry is REFUSED with the
 *      reason, never silently half-written;
 *   2. never prefill an input from a mask.
 *
 * THE INVARIANT (the specification; the S3 region field is one example of it):
 * a stored credential value changes only when the operator typed the whole
 * set, and what the form says will happen is what the route does.
 *
 * The replace-not-merge half of that contract is pinned on the backend by
 * `backend/tests/test_cloud_targets_integration.py::TestCredentialsReplaceNotMerge`
 * — if the route ever starts merging, that test fails and this hint text is
 * the thing to correct.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const notify = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

vi.mock('../../services/cloudTargetsApi', () => ({
  createCloudTarget: vi.fn().mockResolvedValue({ id: 3 }),
  updateCloudTarget: vi.fn().mockResolvedValue({ id: 3 }),
  testCloudTarget: vi.fn().mockResolvedValue({ success: true }),
  testCloudConnectionInline: vi.fn().mockResolvedValue({ success: true }),
}));

import type { CloudTarget } from '../../types/cloudTargets';
import { CloudTargetEditor } from './CloudTargetEditor';

const S3_TARGET: CloudTarget = {
  id: 3,
  name: 'Offsite S3',
  provider_type: 's3',
  // The read shape: real KEYS, values masked to last-4 by `_mask_credentials`.
  credentials: {
    bucket_name: '***ives',
    access_key_id: '***5678',
    secret_access_key: '***wxyz',
    region: '***st-2',
  },
  upload_path: '/backups',
  enabled: true,
  insecure: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function renderEditor(target: CloudTarget | null = null) {
  return render(
    <CloudTargetEditor target={target} onClose={vi.fn()} onSaved={vi.fn()} />,
  );
}

function credInput(label: RegExp | string) {
  const input = screen
    .getByText(label)
    .closest('.modal-form-group')
    ?.querySelector('input, textarea');
  if (!input) throw new Error(`missing credential field for ${label}`);
  return input as HTMLInputElement | HTMLTextAreaElement;
}

describe('CloudTargetEditor — credential edits are all-or-nothing (bead …-ybr3u)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('never prefills a credential input from the stored mask', () => {
    renderEditor(S3_TARGET);

    for (const label of [
      /^Endpoint URL/,
      /^Bucket Name/,
      /^Access Key ID/,
      /^Secret Access Key/,
      /^Region/,
    ]) {
      const input = credInput(label);
      expect(input).toHaveValue('');
      // Belt and braces: the mask must not reach the DOM through the
      // placeholder either.
      expect(input.placeholder).not.toContain('***');
    }
  });

  it('says the whole stored set is replaced, not that only changed fields are', () => {
    const { container } = renderEditor(S3_TARGET);
    const hint = container.querySelector('.cloud-target-credentials .form-hint');
    expect(hint).not.toBeNull();
    // The old text promised a merge the route does not perform.
    expect(hint!.textContent).not.toMatch(/only changed fields/i);
    expect(hint!.textContent).toMatch(/replac/i);
  });

  it('marks which credentials are currently stored without revealing them', () => {
    renderEditor(S3_TARGET);
    // Every S3 key is populated on this target, so every box says so — this is
    // what tells the operator which values they have to retype.
    expect(credInput(/^Bucket Name/).placeholder).toMatch(/currently set/i);
    expect(credInput(/^Region/).placeholder).toMatch(/currently set/i);
  });

  it('marks a credential the target does not have as not set', () => {
    renderEditor({ ...S3_TARGET, credentials: { bucket_name: '***ives' } });
    expect(credInput(/^Bucket Name/).placeholder).toMatch(/currently set/i);
    expect(credInput(/^Region/).placeholder).toMatch(/not set/i);
  });

  it('refuses a partial credential entry rather than blanking its siblings', async () => {
    const cloudApi = await import('../../services/cloudTargetsApi');
    renderEditor(S3_TARGET);

    // Rotating ONLY the secret — the exact edit that used to wipe bucket_name,
    // access_key_id and region.
    fireEvent.change(credInput(/^Secret Access Key/), {
      target: { value: 'rotated-secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

    expect(cloudApi.updateCloudTarget).not.toHaveBeenCalled();
    // The refusal NAMES the fields still missing and states WHY, so the
    // operator is not left guessing.
    expect(notify.error).toHaveBeenCalledWith(
      expect.stringMatching(/Bucket Name/),
    );
    expect(notify.error).toHaveBeenCalledWith(
      expect.stringMatching(/Access Key ID/),
    );
    expect(notify.error).toHaveBeenCalledWith(expect.stringMatching(/replac/i));
  });

  it('sends the complete credential dict once every required field is re-entered', async () => {
    const cloudApi = await import('../../services/cloudTargetsApi');
    renderEditor(S3_TARGET);

    fireEvent.change(credInput(/^Bucket Name/), { target: { value: 'offsite-archives' } });
    fireEvent.change(credInput(/^Access Key ID/), { target: { value: 'AKIA00005678' } });
    fireEvent.change(credInput(/^Secret Access Key/), { target: { value: 'rotated-secret' } });
    fireEvent.change(credInput(/^Region/), { target: { value: 'us-west-2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

    await vi.waitFor(() => expect(cloudApi.updateCloudTarget).toHaveBeenCalledTimes(1));
    const [id, payload] = vi.mocked(cloudApi.updateCloudTarget).mock.calls[0];
    expect(id).toBe(3);
    expect(payload.credentials).toEqual({
      bucket_name: 'offsite-archives',
      access_key_id: 'AKIA00005678',
      secret_access_key: 'rotated-secret',
      region: 'us-west-2',
    });
  });

  it('omits credentials entirely when no credential box is touched', async () => {
    const cloudApi = await import('../../services/cloudTargetsApi');
    renderEditor(S3_TARGET);

    fireEvent.change(screen.getByPlaceholderText('My S3 Bucket'), {
      target: { value: 'Offsite S3 (renamed)' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

    await vi.waitFor(() => expect(cloudApi.updateCloudTarget).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(cloudApi.updateCloudTarget).mock.calls[0][1];
    // Omission is the mechanism that preserves the stored set — naming the key
    // with a partial dict is what blanked its siblings.
    expect(payload).not.toHaveProperty('credentials');
    expect(payload.name).toBe('Offsite S3 (renamed)');
  });

  it('refuses to inline-test a partial credential set while editing', async () => {
    const cloudApi = await import('../../services/cloudTargetsApi');
    renderEditor(S3_TARGET);

    fireEvent.change(credInput(/^Secret Access Key/), {
      target: { value: 'rotated-secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Test Connection/ }));

    // Neither the inline test (which would fail against a bogus half-dict and
    // read as "your stored credentials are broken") nor the saved-target test
    // (which would pass and read as "your typed secret is good").
    expect(cloudApi.testCloudConnectionInline).not.toHaveBeenCalled();
    expect(cloudApi.testCloudTarget).not.toHaveBeenCalled();
    expect(notify.error).toHaveBeenCalled();
  });

  it('still tests the SAVED credentials when no box is touched', async () => {
    const cloudApi = await import('../../services/cloudTargetsApi');
    renderEditor(S3_TARGET);

    fireEvent.click(screen.getByRole('button', { name: /Test Connection/ }));

    await vi.waitFor(() => expect(cloudApi.testCloudTarget).toHaveBeenCalledWith(3));
    expect(cloudApi.testCloudConnectionInline).not.toHaveBeenCalled();
  });

  it('leaves the create path testable field-by-field', async () => {
    // The edit-path completeness rule must not remove the create form's
    // "test as you go" affordance — there is no stored set to misrepresent.
    const cloudApi = await import('../../services/cloudTargetsApi');
    renderEditor(null);

    fireEvent.change(credInput(/^Access Key ID/), { target: { value: 'AKIA00005678' } });
    fireEvent.click(screen.getByRole('button', { name: /Test Connection/ }));

    await vi.waitFor(() =>
      expect(cloudApi.testCloudConnectionInline).toHaveBeenCalledTimes(1),
    );
  });
});
