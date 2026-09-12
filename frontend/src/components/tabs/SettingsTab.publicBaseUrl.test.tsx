/**
 * bead qsqfv - the Public Base URL field on Settings > Email.
 *
 * The backend now builds the emailed password-reset link from
 * `public_base_url` when it is set, and only falls back to the
 * caller-controlled `X-Forwarded-Host` / `Host` headers when it is not. That
 * makes the field the operator's only way to close a P1 account-takeover
 * vector, so it has to be reachable and round-trip correctly.
 *
 * These tests pin the UI half: the stored value loads into the field, an
 * operator's edit reaches the save payload trimmed, and the badge tells them
 * which of the two modes their install is in.
 *
 * Scaffolding follows ./SettingsTab.notificationRedaction.test.tsx.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const notificationMocks = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), dismiss: vi.fn(),
  notify: vi.fn().mockReturnValue('toast-id'),
}));

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  getChannelProfiles: vi.fn(),
  generateMCPApiKey: vi.fn(),
  revokeMCPApiKey: vi.fn(),
  getMCPStatus: vi.fn(),
  listAlertMethods: vi.fn(),
  createAlertMethod: vi.fn(),
  updateAlertMethod: vi.fn(),
  getStreamGroups: vi.fn(),
  getM3UAccounts: vi.fn(),
  getExportSections: vi.fn(),
  listSavedBackups: vi.fn(),
  getStreams: vi.fn(),
  getProbeHistory: vi.fn(),
  getProbeProgress: vi.fn(),
  getM3UDigestSettings: vi.fn(),
  updateM3UDigestSettings: vi.fn(),
  sendTestM3UDigest: vi.fn(),
  testSmtpConnection: vi.fn(),
  testDiscordWebhook: vi.fn(),
  testTelegramBot: vi.fn(),
}));

vi.mock('../../services/channelPipelineApi', () => ({
  getChannelPipelineRules: vi.fn(),
  getChannelPipelineGroups: vi.fn(),
  generateAndFetchDebugBundle: vi.fn(),
  getEventSyncTeamAliases: vi.fn(),
  updateEventSyncTeamAliases: vi.fn(),
}));

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notificationMocks,
}));

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ user: { is_admin: true, username: 'admin' } }),
}));

vi.mock('../settings/NormalizationEngineSection', () => ({
  NormalizationEngineSection: () => <div data-testid="stub-normalization" />,
}));
vi.mock('../settings/TagEngineSection', () => ({
  TagEngineSection: () => <div data-testid="stub-tag-engine" />,
}));
vi.mock('../settings/AuthSettingsSection', () => ({
  AuthSettingsSection: () => <div data-testid="stub-auth" />,
}));
vi.mock('../settings/UserManagementSection', () => ({
  UserManagementSection: () => <div data-testid="stub-users" />,
}));
vi.mock('../settings/LinkedAccountsSection', () => ({
  LinkedAccountsSection: () => <div data-testid="stub-linked-accounts" />,
}));
vi.mock('../settings/TLSSettingsSection', () => ({
  TLSSettingsSection: () => <div data-testid="stub-tls" />,
}));
vi.mock('../settings/BackupRestoreSection', () => ({
  BackupRestoreSection: () => <div data-testid="stub-backup" />,
}));
vi.mock('../settings/MCPSettingsSection', () => ({
  MCPSettingsSection: () => <div data-testid="stub-mcp" />,
}));
vi.mock('../ScheduledTasksSection', () => ({
  ScheduledTasksSection: () => <div data-testid="stub-scheduled-tasks" />,
}));
vi.mock('../SettingsModal', () => ({
  SettingsModal: () => <div data-testid="stub-settings-modal" />,
}));
vi.mock('../DeleteOrphanedGroupsModal', () => ({
  DeleteOrphanedGroupsModal: () => <div data-testid="stub-delete-orphaned" />,
}));
vi.mock('../ModalOverlay', () => ({
  ModalOverlay: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('../CustomSelect', () => ({
  CustomSelect: ({ value, onChange, options }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
  }) => (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o: { value: string; label: string }) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  ),
}));

import * as api from '../../services/api';
import * as pipelineApi from '../../services/channelPipelineApi';
import { logger } from '../../utils/logger';
import { SettingsTab } from './SettingsTab';
import { settingsBase } from '../../test/mocks/settings';
import { invalidateServerData } from '../../hooks/useServerDataInvalidation';

function makeSettings(overrides: Partial<typeof settingsBase> = {}): Awaited<ReturnType<typeof api.getSettings>> {
  return { ...settingsBase, ...overrides } as Awaited<ReturnType<typeof api.getSettings>>;
}

function renderEmailPage() {
  return render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="email" />);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function smtpMethod(id: number, recipient: string): api.AlertMethod {
  return { id, name: 'Email', method_type: 'smtp', enabled: true, config: { to_emails: recipient },
    notify_info: false, notify_success: true, notify_warning: true, notify_error: true };
}

const oldSettings = makeSettings({ url: 'http://old-connection.invalid', username: 'old-user',
  public_base_url: 'https://old.invalid', smtp_host: 'old-smtp.invalid', stats_poll_interval: 20 });
const newSettings = makeSettings({ url: 'http://new-connection.invalid', username: 'new-user',
  public_base_url: 'https://new.invalid', smtp_host: 'new-smtp.invalid', stats_poll_interval: 40 });

/** The badge next to the heading, which reads Configured or Not set. */
function publicBaseUrlBadge(): HTMLElement {
  const header = screen.getByRole('heading', { name: 'Public Base URL' }).parentElement!;
  return within(header).getByText(/Configured|Not set/);
}

async function saveSettingsPage() {
  fireEvent.click(screen.getByRole('button', { name: /Save Settings/i }));
  await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());
}

describe('SettingsTab public base URL (bead qsqfv)', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    notificationMocks.notify.mockReturnValue('toast-id');
    vi.mocked(api.saveSettings).mockResolvedValue({ status: 'ok', configured: true, server_changed: false });
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getStreamGroups).mockResolvedValue([]);
    vi.mocked(pipelineApi.getEventSyncTeamAliases).mockResolvedValue({ groups: [] });
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
    vi.mocked(api.getStreams).mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
    vi.mocked(api.getProbeHistory).mockResolvedValue([]);
    vi.mocked(api.getProbeProgress).mockResolvedValue({ in_progress: false } as Awaited<ReturnType<typeof api.getProbeProgress>>);
  });
  afterEach(() => vi.restoreAllMocks());

  it('loads the stored value and reports it as configured', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      public_base_url: 'https://ecm.example.com',
    }));

    renderEmailPage();

    await waitFor(() => {
      expect(screen.getByLabelText('Public Base URL')).toHaveValue('https://ecm.example.com');
    });
    expect(publicBaseUrlBadge()).toHaveTextContent('Configured');
  });

  it('waits for the shared settings baseline before accepting edits or saves', async () => {
    const user = userEvent.setup();
    let resolveSettings!: (settings: Awaited<ReturnType<typeof api.getSettings>>) => void;
    vi.mocked(api.getSettings).mockReturnValue(new Promise(resolve => { resolveSettings = resolve; }));
    renderEmailPage();
    const input = screen.getByLabelText('Public Base URL');
    await user.type(input, 'https://unsafe-edit.invalid');
    expect(input).toHaveValue('');
    expect(input).toBeDisabled();
    expect(screen.getByRole('status', { name: 'Settings loading' })).toHaveTextContent('Loading shared settings');
    const save = screen.getByRole('button', { name: /Save Settings/i });
    expect(save).toBeDisabled();
    await user.click(save);
    expect(api.saveSettings).not.toHaveBeenCalled();

    await act(async () => { resolveSettings(makeSettings({ public_base_url: 'https://stored.invalid' })); });
    await waitFor(() => expect(input).toBeEnabled());
    expect(input).toHaveValue('https://stored.invalid');
    await user.clear(input);
    await user.type(input, 'https://accepted.invalid');
    await user.click(save);
    await waitFor(() => expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ public_base_url: 'https://accepted.invalid' })));
  });

  it('keeps initial-load failure read-only until an explicit retry succeeds', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getSettings).mockRejectedValueOnce(new Error('Unavailable')).mockResolvedValue(makeSettings());
    renderEmailPage();
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load shared settings');
    const input = screen.getByLabelText('Public Base URL');
    expect(input).toBeDisabled();
    expect(screen.getByRole('button', { name: /Save Settings/i })).toBeDisabled();
    expect(api.getSettings).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Retry loading settings' }));
    await waitFor(() => expect(input).toBeEnabled());
    expect(api.getSettings).toHaveBeenCalledTimes(2);
    await user.type(input, 'https://retry-edit.invalid');
    await saveSettingsPage();
    expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ public_base_url: 'https://retry-edit.invalid' }));
  });

  it.each([0, 1])('accepts only the newest shared load and saves its connection (first response: %s)', async (first) => {
    const user = userEvent.setup();
    const releases: ((settings: api.SettingsResponse) => void)[] = [];
    vi.mocked(api.getSettings).mockImplementation(() => new Promise(resolve => { releases.push(resolve); }));
    renderEmailPage();
    act(() => invalidateServerData('settings'));
    expect(releases).toHaveLength(2);
    const input = screen.getByLabelText('Public Base URL');
    await act(async () => { releases[first]([oldSettings, newSettings][first]); });
    await user.type(input, '/unsafe');
    expect.soft(input).toHaveValue(first === 0 ? '' : 'https://new.invalid');
    expect(input).toBeDisabled();
    expect(screen.getByRole('button', { name: /Save Settings/i })).toBeDisabled();
    await act(async () => { releases[1 - first]([oldSettings, newSettings][1 - first]); });
    await waitFor(() => expect(input).toBeEnabled());
    expect.soft(input).toHaveValue('https://new.invalid');
    expect.soft(screen.getByLabelText('SMTP Host')).toHaveValue('new-smtp.invalid');
    expect(screen.queryByRole('status', { name: 'Unsaved settings' })).not.toBeInTheDocument();
    await user.type(input, '/accepted');
    await saveSettingsPage();
    expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ url: newSettings.url,
      username: newSettings.username, public_base_url: 'https://new.invalid/accepted',
      smtp_host: newSettings.smtp_host, stats_poll_interval: newSettings.stats_poll_interval }));
    expect(notificationMocks.notify).not.toHaveBeenCalled(); // Original restart references are also newest.
  });

  it.each([0, 1])('ignores superseded recipients and saves to the newest method (first response: %s)', async (first) => {
    const user = userEvent.setup();
    const recipients = [deferred<api.AlertMethod[]>(), deferred<api.AlertMethod[]>()];
    // The real independent child loads first; the next two reads belong to loadSettings.
    vi.mocked(api.listAlertMethods).mockResolvedValueOnce([])
      .mockReturnValueOnce(recipients[0].promise).mockReturnValueOnce(recipients[1].promise);
    vi.mocked(api.getSettings).mockResolvedValueOnce(oldSettings).mockResolvedValue(newSettings);
    renderEmailPage();
    await waitFor(() => expect(api.listAlertMethods).toHaveBeenCalledTimes(2));
    act(() => invalidateServerData('settings'));
    await waitFor(() => expect(api.listAlertMethods).toHaveBeenCalledTimes(3));
    const input = screen.getByLabelText('Email alert recipients');
    const methods = [smtpMethod(11, 'old@example.com'), smtpMethod(22, 'new@example.com')];
    await act(async () => recipients[first].resolve([methods[first]]));
    expect.soft(input).toHaveValue(first === 0 ? '' : 'new@example.com');
    expect(input).toBeDisabled();
    await act(async () => recipients[1 - first].resolve([methods[1 - first]]));
    await waitFor(() => expect(input).toBeEnabled());
    expect.soft(input).toHaveValue('new@example.com');
    await user.clear(input);
    await user.type(input, 'accepted@example.com');
    vi.mocked(api.updateAlertMethod).mockResolvedValue({ success: true });
    await user.click(screen.getByRole('button', { name: /Save Recipients/ }));
    await waitFor(() => expect(api.updateAlertMethod).toHaveBeenCalledWith(22, expect.objectContaining({
      config: expect.objectContaining({ to_emails: 'accepted@example.com' }),
    })));
    await user.type(screen.getByLabelText('Public Base URL'), '/accepted');
    await saveSettingsPage();
    expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ url: newSettings.url,
      public_base_url: 'https://new.invalid/accepted' }));
  });

  it('ignores a stale recipient error without ending the newest recipient loading state', async () => {
    const warn = vi.spyOn(logger, 'warn');
    const recipients = [deferred<api.AlertMethod[]>(), deferred<api.AlertMethod[]>()];
    vi.mocked(api.listAlertMethods).mockResolvedValueOnce([])
      .mockReturnValueOnce(recipients[0].promise).mockReturnValueOnce(recipients[1].promise);
    vi.mocked(api.getSettings).mockResolvedValueOnce(oldSettings).mockResolvedValue(newSettings);
    renderEmailPage();
    await waitFor(() => expect(api.listAlertMethods).toHaveBeenCalledTimes(2));
    act(() => invalidateServerData('settings'));
    await waitFor(() => expect(api.listAlertMethods).toHaveBeenCalledTimes(3));
    await act(async () => recipients[0].reject(new Error('stale recipient error')));
    expect.soft(warn).not.toHaveBeenCalled();
    expect.soft(screen.getByLabelText('Email alert recipients')).toHaveAttribute('placeholder', 'Loading recipients…');
    await act(async () => recipients[1].resolve([smtpMethod(22, 'new@example.com')]));
    expect(screen.getByLabelText('Email alert recipients')).toHaveValue('new@example.com');
    expect(screen.getByLabelText('Public Base URL')).toBeEnabled();
  });

  it('settles recipient loading when the newest shared reload fails before its recipient read', async () => {
    const recipients = deferred<api.AlertMethod[]>();
    vi.mocked(api.getSettings).mockResolvedValueOnce(newSettings).mockResolvedValueOnce(newSettings)
      .mockRejectedValueOnce(new Error('newest reload failed'));
    vi.mocked(api.listAlertMethods).mockResolvedValueOnce([])
      .mockResolvedValueOnce([smtpMethod(22, 'accepted@example.com')]).mockReturnValueOnce(recipients.promise);
    renderEmailPage();
    const input = screen.getByLabelText('Email alert recipients');
    await waitFor(() => expect(input).toBeEnabled());
    act(() => invalidateServerData('settings'));
    await waitFor(() => expect(api.listAlertMethods).toHaveBeenCalledTimes(3));
    await act(async () => invalidateServerData('settings'));
    await act(async () => recipients.resolve([smtpMethod(11, 'stale@example.com')]));
    expect(input).toHaveValue('accepted@example.com');
    expect(input).toBeEnabled();
    expect(input).not.toHaveAttribute('placeholder', 'Loading recipients…');
  });

  it('does not publish a superseded shared error after a newer successful baseline', async () => {
    const error = vi.spyOn(logger, 'error');
    const initial = deferred<api.SettingsResponse>();
    vi.mocked(api.getSettings).mockReturnValueOnce(initial.promise).mockResolvedValue(newSettings);
    renderEmailPage();
    act(() => invalidateServerData('settings'));
    await waitFor(() => expect(screen.getByLabelText('Public Base URL')).toHaveValue('https://new.invalid'));
    await act(async () => initial.reject(new Error('stale shared error')));
    expect(error).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Public Base URL')).toBeEnabled();
  });

  it('does not adopt a stale success when the newest initial load failed', async () => {
    const initial = deferred<api.SettingsResponse>();
    vi.mocked(api.getSettings).mockReturnValueOnce(initial.promise).mockRejectedValueOnce(new Error('newest failed'));
    renderEmailPage();
    act(() => invalidateServerData('settings'));
    await screen.findByRole('alert');
    await act(async () => initial.resolve(oldSettings));
    expect(screen.getByLabelText('Public Base URL')).toHaveValue('');
    expect(screen.getByLabelText('Public Base URL')).toBeDisabled();
    expect(screen.getByRole('button', { name: /Save Settings/i })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load shared settings');
  });

  it.each(['success', 'error'])('keeps edits when a superseded cancel returns %s and the newest reload failed', async (outcome) => {
    const user = userEvent.setup();
    const cancelled = deferred<api.SettingsResponse>();
    vi.mocked(api.getSettings).mockResolvedValueOnce(newSettings).mockReturnValueOnce(cancelled.promise)
      .mockRejectedValueOnce(new Error('newest reload failed'));
    renderEmailPage();
    const input = screen.getByLabelText('Public Base URL');
    await waitFor(() => expect(input).toBeEnabled());
    await user.type(input, '/keep');
    await user.click(screen.getByRole('button', { name: 'Cancel changes' }));
    await act(async () => invalidateServerData('settings'));
    await act(async () => {
      if (outcome === 'success') cancelled.resolve(oldSettings);
      else cancelled.reject(new Error('stale cancel failed'));
    });
    expect(input).toHaveValue('https://new.invalid/keep');
    expect(input).toBeEnabled();
    expect(screen.getByRole('status', { name: 'Unsaved settings' })).toBeInTheDocument();
    expect(screen.queryByText(/Could not reload saved settings/)).not.toBeInTheDocument();
    await saveSettingsPage();
    expect(api.saveSettings).toHaveBeenCalledWith(expect.objectContaining({ url: newSettings.url,
      public_base_url: 'https://new.invalid/keep' }));
  });

  it.each(['pending', 'failed'])('keeps real ntfy controls usable with %s shared settings', async (state) => {
    const user = userEvent.setup();
    const shared = deferred<api.SettingsResponse>();
    vi.mocked(api.getSettings).mockReturnValue(shared.promise);
    vi.mocked(api.createAlertMethod).mockResolvedValue({ ...smtpMethod(33, ''), name: 'Independent', method_type: 'ntfy' });
    renderEmailPage();
    await screen.findByText('No alert methods configured yet.');
    if (state === 'failed') await act(async () => shared.reject(new Error('shared failed')));
    expect(screen.getByRole('button', { name: 'Add ntfy target' })).toBeEnabled();
    await user.type(screen.getByLabelText('Name'), 'Independent');
    await user.type(screen.getByLabelText('Server URL'), 'https://ntfy.invalid');
    await user.type(screen.getByLabelText('Topic'), 'test');
    await user.click(screen.getByRole('button', { name: 'Add ntfy target' }));
    await waitFor(() => expect(api.createAlertMethod).toHaveBeenCalledWith(expect.objectContaining({ name: 'Independent' })));
    expect(screen.getByLabelText('Public Base URL')).toBeDisabled();
    expect(screen.getByRole('button', { name: /Save Settings/i })).toBeDisabled();
    expect(api.saveSettings).not.toHaveBeenCalled();
    if (state === 'pending') await act(async () => shared.resolve(newSettings));
  });

  it.each(['pending', 'failed'])('keeps real team-alias controls usable with %s shared settings', async (state) => {
    const user = userEvent.setup();
    const shared = deferred<api.SettingsResponse>();
    vi.mocked(api.getSettings).mockReturnValue(shared.promise);
    vi.mocked(pipelineApi.updateEventSyncTeamAliases).mockResolvedValue({ groups: [{ terms: ['Alpha', 'AFC'], note: null }] });
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="channel-pipeline" />);
    await screen.findByText(/No alias groups configured/);
    if (state === 'failed') await act(async () => shared.reject(new Error('shared failed')));
    expect(screen.getByRole('button', { name: /Add alias group/ })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: /Add alias group/ }));
    for (const term of ['Alpha', 'AFC']) {
      await user.type(screen.getByPlaceholderText('Add a spelling, e.g. MUFC'), `${term}{Enter}`);
    }
    await user.click(screen.getByRole('button', { name: 'Save Team Aliases' }));
    await waitFor(() => expect(pipelineApi.updateEventSyncTeamAliases).toHaveBeenCalledWith([{ terms: ['Alpha', 'AFC'], note: null }]));
    expect(screen.getByLabelText('Max channels created per run')).toBeDisabled();
    expect(screen.getByRole('button', { name: /Save Settings/i })).toBeDisabled();
    expect(api.saveSettings).not.toHaveBeenCalled();
    if (state === 'pending') await act(async () => shared.resolve(newSettings));
  });

  it.each([false, true])('mounts and remounts without a discarded stream lookup (loader failures: %s)', async (failLoads) => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      public_base_url: 'https://ecm.example.com',
    }));
    if (failLoads) {
      vi.mocked(api.getProbeHistory).mockRejectedValue(new Error('history unavailable'));
      vi.mocked(api.getProbeProgress).mockRejectedValue(new Error('progress unavailable'));
      vi.mocked(api.getM3UAccounts).mockRejectedValue(new Error('accounts unavailable'));
    }

    let firstPayload: Parameters<typeof api.saveSettings>[0] | undefined;
    for (let mount = 1; mount <= 2; mount++) {
      const view = renderEmailPage();
      await waitFor(() => expect(screen.getByLabelText('Public Base URL')).toHaveValue('https://ecm.example.com'));
      for (const load of [api.getSettings, api.getProbeHistory, api.getProbeProgress, api.getM3UAccounts]) {
        expect(load).toHaveBeenCalledTimes(mount);
      }
      await saveSettingsPage();
      await waitFor(() => expect(api.saveSettings).toHaveBeenCalledTimes(mount));
      const payload = vi.mocked(api.saveSettings).mock.calls[mount - 1][0];
      expect(payload).toMatchObject({
        public_base_url: 'https://ecm.example.com',
        stream_probe_timeout: 30,
        max_concurrent_probes: 8,
        stream_fetch_page_limit: 200,
      });
      expect(payload).not.toHaveProperty('total_stream_count');
      if (firstPayload) expect(payload).toEqual(firstPayload);
      firstPayload = payload;
      view.unmount();
      expect(api.getStreams).not.toHaveBeenCalled();
    }
  });

  it('shows Not set when the install is still on header-derived links', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({ public_base_url: '' }));

    renderEmailPage();

    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    expect(publicBaseUrlBadge()).toHaveTextContent('Not set');
  });

  it('sends the edited value, trimmed, in the save payload', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({ public_base_url: '' }));

    renderEmailPage();
    await waitFor(() => expect(screen.getByLabelText('Public Base URL')).toHaveValue(''));

    fireEvent.change(screen.getByLabelText('Public Base URL'), {
      target: { value: '  https://ecm.example.com  ' },
    });
    await saveSettingsPage();

    const payload = vi.mocked(api.saveSettings).mock.calls[0][0];
    expect(payload.public_base_url).toBe('https://ecm.example.com');
  });

  it('round-trips an untouched stored value instead of dropping the field', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings({
      public_base_url: 'https://ecm.example.com',
    }));

    renderEmailPage();
    await waitFor(() => expect(screen.getByLabelText('Public Base URL')).toHaveValue('https://ecm.example.com'));

    await saveSettingsPage();

    const payload = vi.mocked(api.saveSettings).mock.calls[0][0];
    expect(payload.public_base_url).toBe('https://ecm.example.com');
  });

  it('shows the restart action when the backend reports a pending log policy', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
    vi.mocked(api.saveSettings).mockResolvedValue({
      status: 'ok',
      configured: true,
      server_changed: false,
      restart_required: true,
    });

    renderEmailPage();
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    await saveSettingsPage();

    expect(notificationMocks.notify).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Restart Required',
        message: 'Persistent logging settings changed. Restart services to apply.',
      }),
    );
  });
});
