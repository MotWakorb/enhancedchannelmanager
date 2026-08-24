/**
 * Tests for the Cross-Instance Sync card (epic i39wu / bead nnl9s).
 *
 * Contracts under test:
 *   - Lists configured sync targets with a tri-state status badge derived from
 *     `last_outcome` plus the "last synced" timestamp.
 *   - The add-target form calls createSyncTarget with the entered fields.
 *   - The enable/disable toggle (the KILL SWITCH) calls updateSyncTarget with
 *     the flipped `enabled` value.
 *   - The logo-replication toggle (bead …-8gnik) calls updateSyncTarget with the
 *     flipped `sync_logos` value, reflects the target's stored value, and is
 *     never set implicitly by the create form — which is what lets the BACKEND
 *     own the default (ON since bead 2yq19).
 *   - Delete confirms first, then calls deleteSyncTarget.
 *   - "Sync now" runs a DRY-RUN preview — runTask(`dbas_sync_${id}`, undefined,
 *     { sync_target_id, confirm_apply: false }) — and only an explicit Apply
 *     runs with confirm_apply: true. The task id is PER TARGET (7ipq2.3 /
 *     ADR-013 S6): distinct targets sync concurrently; the backend refuses a
 *     second run against the same target.
 *   - The load-bearing operator copy (one-way overwrite + provider credentials
 *     ARE sent every cycle) is present in the card (ADR-013 amendment (b)).
 *   - The Schedules Direct password field appears ONLY when this instance has a
 *     Schedules Direct EPG source, and is carried on create.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

vi.mock('../../services/api', () => ({
  listSyncTargets: vi.fn(),
  createSyncTarget: vi.fn(),
  updateSyncTarget: vi.fn(),
  deleteSyncTarget: vi.fn(),
  getSyncSourceCredentialNeeds: vi.fn(),
  runTask: vi.fn(),
}));

const notify = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

import * as api from '../../services/api';
import { SyncTargetsCard } from './SyncTargetsCard';

type Mock = ReturnType<typeof vi.fn>;

const TARGET: api.SyncTarget = {
  id: 7,
  name: 'Living Room B',
  base_url: 'https://b.example.com',
  credentials: { username: '***user' },
  enabled: true,
  insecure: false,
  fuzzy_stream_matching: false,
  sync_logos: false,
  logo_sync_interval_hours: 24,
  core_settings_excluded: [],
  has_schedules_direct_password: false,
  credential_version: 1,
  last_full_sync_at: '2026-06-18T12:00:00Z',
  last_outcome: 'success',
};

function mockTargets(targets: api.SyncTarget[]) {
  (api.listSyncTargets as Mock).mockResolvedValue(targets);
}

async function renderCard(
  targets: api.SyncTarget[] = [TARGET],
  needsSd = false,
) {
  mockTargets(targets);
  (api.getSyncSourceCredentialNeeds as Mock).mockResolvedValue({
    needs_schedules_direct_password: needsSd,
    schedules_direct_sources: needsSd ? ['SD Lineup'] : [],
  });
  render(<SyncTargetsCard />);
  // Wait for the initial list load to settle.
  await waitFor(() => expect(api.listSyncTargets).toHaveBeenCalled());
}

describe('SyncTargetsCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Default confirm to true so delete proceeds unless a test overrides it.
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  it('renders the one-way + credentials-are-sent operator copy', async () => {
    // AMENDED 2026-08-22: this asserted "credentials are not synced". Bead
    // msqf7's defect was ECM claiming credentials were stripped while sending
    // them, so this copy has to change in the same commit as the behaviour —
    // the assertion is the gate on that, not decoration.
    await renderCard([]);
    expect(screen.getByText(/managed replica/i)).toBeInTheDocument();
    expect(screen.getByText(/overwritten by/i)).toBeInTheDocument();
    expect(
      screen.getByText(/provider credentials are sent on every sync/i),
    ).toBeInTheDocument();
    // And the copy states the cost, not only the convenience.
    expect(
      screen.getByTestId('stc-credentials-banner').textContent,
    ).toMatch(/in clear/i);
    // The claim it replaced must be gone, not merely outweighed.
    expect(screen.queryByText(/credentials are not synced/i)).toBeNull();
  });

  it('asks for a Schedules Direct password only when a source needs one', async () => {
    await renderCard([], false);
    fireEvent.click(await screen.findByRole('button', { name: /add sync target/i }));
    expect(screen.queryByTestId('stc-sd-password-field')).toBeNull();
  });

  it('shows the Schedules Direct field and carries it on create', async () => {
    (api.createSyncTarget as Mock).mockResolvedValue({ ...TARGET, id: 9 });
    await renderCard([], true);
    fireEvent.click(await screen.findByRole('button', { name: /add sync target/i }));

    const field = await screen.findByTestId('stc-sd-password-field');
    expect(field).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^name$/i), {
      target: { value: 'New B' },
    });
    fireEvent.change(screen.getByLabelText(/base url/i), {
      target: { value: 'https://b2.example.com' },
    });
    fireEvent.change(screen.getByLabelText(/schedules direct password/i), {
      target: { value: 'sd-secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create target/i }));

    await waitFor(() => expect(api.createSyncTarget).toHaveBeenCalled());
    expect((api.createSyncTarget as Mock).mock.calls[0][0]).toMatchObject({
      schedules_direct_password: 'sd-secret',
    });
  });

  it('badges a target that already has a stored Schedules Direct password', async () => {
    await renderCard([{ ...TARGET, has_schedules_direct_password: true }]);
    expect(
      await screen.findByTestId('sync-target-sd-password-7'),
    ).toBeInTheDocument();
  });

  it('lists configured targets with a tri-state status badge', async () => {
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());
    const badge = screen.getByTestId('sync-target-status-7');
    expect(badge).toHaveTextContent(/success/i);
  });

  it('add-target form calls createSyncTarget with entered fields', async () => {
    (api.createSyncTarget as Mock).mockResolvedValue({ ...TARGET, id: 8, name: 'New B' });
    await renderCard([]);

    // Wait for the async list-load to settle before interacting (the add button
    // + form only render once the loading state clears).
    fireEvent.click(await screen.findByRole('button', { name: /add sync target/i }));

    fireEvent.change(await screen.findByLabelText(/^name/i), { target: { value: 'New B' } });
    fireEvent.change(screen.getByLabelText(/base url/i), {
      target: { value: 'https://new-b.example.com' },
    });
    fireEvent.change(screen.getByLabelText(/username/i), { target: { value: 'admin' } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'secret' } });

    // The button's accessible name includes the leading material-icon ligature
    // ("add Create target"), so match on the label substring, not an anchor.
    fireEvent.click(screen.getByRole('button', { name: /create target/i }));

    await waitFor(() => expect(api.createSyncTarget).toHaveBeenCalledTimes(1));
    expect(api.createSyncTarget).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'New B',
        base_url: 'https://new-b.example.com',
        credentials: { username: 'admin', password: 'secret' },
      }),
    );
  });

  it('enable/disable toggle (kill switch) calls updateSyncTarget with flipped enabled', async () => {
    (api.updateSyncTarget as Mock).mockResolvedValue({ ...TARGET, enabled: false });
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-toggle-7'));

    await waitFor(() => expect(api.updateSyncTarget).toHaveBeenCalledTimes(1));
    expect(api.updateSyncTarget).toHaveBeenCalledWith(7, { enabled: false });
  });

  it('delete confirms then calls deleteSyncTarget', async () => {
    (api.deleteSyncTarget as Mock).mockResolvedValue(undefined);
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-delete-7'));

    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(api.deleteSyncTarget).toHaveBeenCalledWith(7));
  });

  it('does NOT delete when the confirm is declined', async () => {
    (window.confirm as Mock).mockReturnValue(false);
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-delete-7'));

    expect(window.confirm).toHaveBeenCalled();
    expect(api.deleteSyncTarget).not.toHaveBeenCalled();
  });

  it('"Sync now" runs a DRY-RUN preview (confirm_apply: false)', async () => {
    (api.runTask as Mock).mockResolvedValue({ success: true, message: 'preview ok' });
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-preview-7'));

    await waitFor(() => expect(api.runTask).toHaveBeenCalledTimes(1));
    expect(api.runTask).toHaveBeenCalledWith('dbas_sync_7', undefined, {
      sync_target_id: 7,
      confirm_apply: false,
    });
  });

  it('a preview that could not read the destination does NOT offer Apply', async () => {
    // Bead …-jqfxm: the backend fails a preview it could not read B for. The
    // card must keep the Apply affordance hidden — offering it is what turned a
    // false-green preview into an operator overwriting a destination nobody had
    // reached — and must show the sentence, not the machine code.
    (api.runTask as Mock).mockResolvedValue({
      success: false,
      error: 'SYNC_DESTINATION_UNREADABLE',
      message:
        'Cross-instance sync preview could not read the destination it ' +
        'describes — authentication to the destination was rejected (HTTP 401).',
    });
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-preview-7'));

    await waitFor(() => expect(api.runTask).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(screen.queryByTestId('sync-target-apply-7')).not.toBeInTheDocument();
    expect(notify.error).toHaveBeenCalledWith(
      expect.stringContaining('could not read the destination'),
      'Sync Preview (dry run)',
    );
  });

  it('Apply (after preview) runs with confirm_apply: true', async () => {
    (api.runTask as Mock).mockResolvedValue({ success: true, message: 'preview ok' });
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    // Run the preview first to surface the Apply affordance.
    fireEvent.click(screen.getByTestId('sync-target-preview-7'));
    await waitFor(() => expect(api.runTask).toHaveBeenCalledTimes(1));

    const applyBtn = await screen.findByTestId('sync-target-apply-7');
    fireEvent.click(applyBtn);

    // Apply confirm dialog (source-wins overwrite) — confirm spy returns true.
    await waitFor(() => expect(api.runTask).toHaveBeenCalledTimes(2));
    expect(api.runTask).toHaveBeenLastCalledWith('dbas_sync_7', undefined, {
      sync_target_id: 7,
      confirm_apply: true,
    });
  });

  it('shows the insecure-TLS warning badge when a target has insecure=true (nngkg)', async () => {
    await renderCard([{ ...TARGET, insecure: true }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());
    const badge = screen.getByTestId('sync-target-insecure-7');
    expect(badge).toBeInTheDocument();
    // Plain-language copy — no "TLS"/"SSRF" jargon required to convey the risk.
    expect(badge).toHaveTextContent(/certificate check off/i);
  });

  it('does NOT show the insecure badge for a secure target', async () => {
    await renderCard([{ ...TARGET, insecure: false }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());
    expect(screen.queryByTestId('sync-target-insecure-7')).not.toBeInTheDocument();
  });

  it('a disabled target shows the kill-switch state', async () => {
    await renderCard([{ ...TARGET, enabled: false }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());
    const row = screen.getByTestId('sync-target-row-7');
    expect(within(row).getByTestId('sync-target-toggle-7')).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });
  // -------------------------------------------------------------------------
  // Logo replication opt-in (bead …-8gnik).
  //
  // `sync_logos` had no UI at all: the only ways to turn logo replication on
  // were PUT /api/sync-targets/{id} or the MCP tool, and the operator guide
  // said so in as many words. The toggle below is that missing control.
  //
  // The DEFAULT moved to ON in bead …-2yq19, and it moved in the BACKEND. This
  // card's create form omits `sync_logos` entirely, which is exactly what makes
  // that possible — see the create-path test at the end of this block.
  // -------------------------------------------------------------------------

  it('logo toggle calls updateSyncTarget with the flipped sync_logos (off -> on)', async () => {
    (api.updateSyncTarget as Mock).mockResolvedValue({ ...TARGET, sync_logos: true });
    await renderCard([{ ...TARGET, sync_logos: false }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-logos-7'));

    await waitFor(() => expect(api.updateSyncTarget).toHaveBeenCalledTimes(1));
    expect(api.updateSyncTarget).toHaveBeenCalledWith(7, { sync_logos: true });
  });

  it('logo toggle turns replication back OFF (on -> off)', async () => {
    (api.updateSyncTarget as Mock).mockResolvedValue({ ...TARGET, sync_logos: false });
    await renderCard([{ ...TARGET, sync_logos: true }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-logos-7'));

    await waitFor(() => expect(api.updateSyncTarget).toHaveBeenCalledTimes(1));
    expect(api.updateSyncTarget).toHaveBeenCalledWith(7, { sync_logos: false });
  });

  it('logo toggle reflects the stored sync_logos value', async () => {
    await renderCard([{ ...TARGET, sync_logos: true }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());
    expect(screen.getByTestId('sync-target-logos-7')).toHaveAttribute('aria-pressed', 'true');
  });

  it('logo toggle reads OFF for a target with logo replication disabled', async () => {
    await renderCard([{ ...TARGET, sync_logos: false }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());
    const toggle = screen.getByTestId('sync-target-logos-7');
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(toggle).toHaveTextContent(/logos off/i);
  });

  it('creating a target lets the BACKEND decide logo replication (bead …-2yq19)', async () => {
    (api.createSyncTarget as Mock).mockResolvedValue({ ...TARGET, id: 8, name: 'New B' });
    await renderCard([]);

    fireEvent.click(await screen.findByRole('button', { name: /add sync target/i }));
    fireEvent.change(await screen.findByLabelText(/^name/i), { target: { value: 'New B' } });
    fireEvent.change(screen.getByLabelText(/base url/i), {
      target: { value: 'https://new-b.example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create target/i }));

    await waitFor(() => expect(api.createSyncTarget).toHaveBeenCalledTimes(1));
    // The create path must not carry `sync_logos` AT ALL, in either direction.
    // This used to assert `payload.sync_logos ?? false === false`, which passed
    // both on omission and on an explicit `false` — indistinguishable, and only
    // one of them is right now that the backend default is ON. An explicit
    // `false` here would silently re-impose the old default from the client and
    // hand every new replica a lineup with no artwork, which is the failure
    // epic f5a5j is named for. Omission is the assertion.
    const payload = (api.createSyncTarget as Mock).mock.calls[0][0];
    expect('sync_logos' in payload).toBe(false);
  });

  // -------------------------------------------------------------------------
  // Per-target in-flight state (PR #752 review, Warn).
  //
  // With per-target sync tasks, two targets can legitimately be syncing at
  // once. A single scalar `busyId` cannot represent that: starting B
  // overwrites A's in-flight marker, and B finishing CLEARS it entirely — so
  // A's row re-enables while A's request is still outstanding, and the next
  // click produces an avoidable ALREADY_RUNNING error from the backend.
  // -------------------------------------------------------------------------

  it('keeps target A busy while target B runs and finishes (two in flight)', async () => {
    const TARGET_B: api.SyncTarget = { ...TARGET, id: 9, name: 'Bedroom B' };

    // Deferred promises so both runs can be in flight simultaneously.
    let resolveA!: (v: unknown) => void;
    let resolveB!: (v: unknown) => void;
    const runA = new Promise((res) => {
      resolveA = res;
    });
    const runB = new Promise((res) => {
      resolveB = res;
    });
    (api.runTask as Mock).mockImplementation((taskId: string) =>
      taskId === 'dbas_sync_7' ? runA : runB,
    );

    await renderCard([TARGET, TARGET_B]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    // Start A, then B — both now in flight.
    fireEvent.click(screen.getByTestId('sync-target-preview-7'));
    await waitFor(() => expect(screen.getByTestId('sync-target-preview-7')).toBeDisabled());
    fireEvent.click(screen.getByTestId('sync-target-preview-9'));
    await waitFor(() => expect(screen.getByTestId('sync-target-preview-9')).toBeDisabled());

    // Starting B must not have released A.
    expect(screen.getByTestId('sync-target-preview-7')).toBeDisabled();

    // Finish B only. A is STILL running, so A's row must stay disabled —
    // this is the regression: a scalar busyId clears here and re-enables A.
    resolveB({ success: true, message: 'preview ok' });
    await waitFor(() => expect(screen.getByTestId('sync-target-preview-9')).toBeEnabled());
    expect(screen.getByTestId('sync-target-preview-7')).toBeDisabled();

    // Finishing A releases only A.
    resolveA({ success: true, message: 'preview ok' });
    await waitFor(() => expect(screen.getByTestId('sync-target-preview-7')).toBeEnabled());
  });

  it('a failed run on one target does not release another in-flight target', async () => {
    const TARGET_B: api.SyncTarget = { ...TARGET, id: 9, name: 'Bedroom B' };

    let resolveA!: (v: unknown) => void;
    let rejectB!: (e: unknown) => void;
    const runA = new Promise((res) => {
      resolveA = res;
    });
    const runB = new Promise((_res, rej) => {
      rejectB = rej;
    });
    (api.runTask as Mock).mockImplementation((taskId: string) =>
      taskId === 'dbas_sync_7' ? runA : runB,
    );

    await renderCard([TARGET, TARGET_B]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-preview-7'));
    await waitFor(() => expect(screen.getByTestId('sync-target-preview-7')).toBeDisabled());
    fireEvent.click(screen.getByTestId('sync-target-preview-9'));
    await waitFor(() => expect(screen.getByTestId('sync-target-preview-9')).toBeDisabled());

    rejectB(new Error('B unreachable'));
    await waitFor(() => expect(screen.getByTestId('sync-target-preview-9')).toBeEnabled());
    expect(screen.getByTestId('sync-target-preview-7')).toBeDisabled();

    resolveA({ success: true, message: 'preview ok' });
    await waitFor(() => expect(screen.getByTestId('sync-target-preview-7')).toBeEnabled());
  });
  // -------------------------------------------------------------------------
  // Correcting a target in place (bead …-a3lby).
  //
  // Every field the create form sets — name, base URL, credentials, the
  // insecure-TLS opt-out — was WRITE-ONCE in the UI: the only way to fix a
  // mistyped character was delete-and-recreate, which resets `sync_logos` to
  // its default OFF (bead …-8gnik shipped that control) and hands the
  // replacement the deleted target's execution history, because that history is
  // keyed on a REUSABLE target id (bead …-5dp92).
  //
  // `PUT /api/sync-targets/{id}` already accepted all of them and the api.ts
  // client already exposed them; only the affordance was missing.
  //
  // THE INVARIANT (the specification; base URL and credentials are examples of
  // it): any sync-target field an operator can set at creation can be corrected
  // afterwards without destroying the target — and correcting one must not
  // disturb the settings the operator set elsewhere.
  // -------------------------------------------------------------------------

  it('Edit opens the form prefilled with the target as it stands', async () => {
    await renderCard([{ ...TARGET, insecure: true }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-edit-7'));

    expect(await screen.findByLabelText(/^name/i)).toHaveValue('Living Room B');
    expect(screen.getByLabelText(/base url/i)).toHaveValue('https://b.example.com');
    expect(screen.getByRole('checkbox')).toBeChecked();
  });

  it('never prefills the credential inputs with the masked stored value', async () => {
    // The read shape masks credentials to last-4 (`***user`). Putting that in
    // the box invites the operator to "keep" a value that is not the secret.
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-edit-7'));

    expect(await screen.findByLabelText(/username/i)).toHaveValue('');
    expect(screen.getByLabelText(/password/i)).toHaveValue('');
  });

  it('corrects the base URL WITHOUT touching sync_logos, enabled or the credentials', async () => {
    // The whole point of the bead: a typo fix must not cost the operator their
    // logo choice, their kill-switch state, or their stored secret. A PUT is
    // partial, so the payload carries ONLY what the form edits.
    (api.updateSyncTarget as Mock).mockResolvedValue({ ...TARGET });
    await renderCard([{ ...TARGET, sync_logos: true, enabled: false }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-edit-7'));
    fireEvent.change(await screen.findByLabelText(/base url/i), {
      target: { value: 'https://corrected-b.example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(api.updateSyncTarget).toHaveBeenCalledTimes(1));
    const [id, payload] = (api.updateSyncTarget as Mock).mock.calls[0];
    expect(id).toBe(7);
    expect(payload).toEqual({
      name: 'Living Room B',
      base_url: 'https://corrected-b.example.com',
      insecure: false,
    });
    // Explicit, because omission is the mechanism: naming any of these would
    // overwrite state the operator set on the row, not correct the typo.
    expect(payload).not.toHaveProperty('sync_logos');
    expect(payload).not.toHaveProperty('enabled');
    expect(payload).not.toHaveProperty('credentials');
  });

  it('sends the new credentials when they are entered in full', async () => {
    (api.updateSyncTarget as Mock).mockResolvedValue({ ...TARGET });
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-edit-7'));
    fireEvent.change(await screen.findByLabelText(/username/i), { target: { value: 'admin' } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'corrected' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(api.updateSyncTarget).toHaveBeenCalledTimes(1));
    expect(api.updateSyncTarget).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ credentials: { username: 'admin', password: 'corrected' } }),
    );
  });

  it('refuses a HALF-entered credential rather than erasing the other half', async () => {
    // The backend REPLACES the whole credentials dict — it does not merge — and
    // ECM cannot read the stored secret back to fill the gap. Sending
    // `{username: '', password: 'x'}` would blank the username. Refuse instead.
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-edit-7'));
    fireEvent.change(await screen.findByLabelText(/password/i), { target: { value: 'only-this' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(api.updateSyncTarget).not.toHaveBeenCalled();
  });

  it('refuses a switch to API key with no key entered', async () => {
    // Changing the auth MODE changes the shape of the credentials dict, so the
    // stored username/password cannot be carried over — the new secret is
    // mandatory, and silently keeping the old shape would be a lie.
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-edit-7'));
    fireEvent.change(await screen.findByLabelText(/authentication/i), {
      target: { value: 'api_key' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    expect(api.updateSyncTarget).not.toHaveBeenCalled();
  });

  it('prefills the auth mode from the stored credential shape (api_key)', async () => {
    await renderCard([{ ...TARGET, credentials: { api_key: '***key1' } }]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-edit-7'));

    expect(await screen.findByLabelText(/authentication/i)).toHaveValue('api_key');
    expect(screen.getByLabelText(/api key/i)).toHaveValue('');
  });

  it('Cancel closes the edit form and writes nothing', async () => {
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-edit-7'));
    fireEvent.change(await screen.findByLabelText(/^name/i), { target: { value: 'Discarded' } });
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

    await waitFor(() => expect(screen.queryByLabelText(/base url/i)).not.toBeInTheDocument());
    expect(api.updateSyncTarget).not.toHaveBeenCalled();
  });

  it('Add still creates — the shared form did not turn creates into updates', async () => {
    (api.createSyncTarget as Mock).mockResolvedValue({ ...TARGET, id: 8, name: 'New B' });
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /add sync target/i }));
    fireEvent.change(await screen.findByLabelText(/^name/i), { target: { value: 'New B' } });
    fireEvent.change(screen.getByLabelText(/base url/i), {
      target: { value: 'https://new-b.example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create target/i }));

    await waitFor(() => expect(api.createSyncTarget).toHaveBeenCalledTimes(1));
    expect(api.updateSyncTarget).not.toHaveBeenCalled();
  });

  it('says plainly that a blank credential box keeps the stored secret', async () => {
    await renderCard([TARGET]);
    await waitFor(() => expect(screen.getByText('Living Room B')).toBeInTheDocument());

    fireEvent.click(screen.getByTestId('sync-target-edit-7'));

    expect(await screen.findByTestId('stc-credentials-hint')).toHaveTextContent(
      /leave .*blank to keep the stored credentials/i,
    );
  });
});
