/**
 * The Settings section rail must be complete, and its anchors must resolve,
 * from FIRST PAINT — not once each page's fetch settles (bead
 * enhancedchannelmanager-b32co; the Stats half is 22fef24d / bead mch8j).
 *
 * WHY THIS IS WORSE ON SETTINGS THAN ON STATS. `StickySectionNav` selects
 * Settings sections with `.settings-section, [data-settings-section]` and
 * generates each section's `id` by SLUGGING ITS LABEL. Stats pins its ids
 * explicitly; Settings cannot, because `discover()` discards any existing
 * `settings-`-prefixed id in favour of the generated one. So on Settings a
 * section that is absent — or present but unlabelled — has no anchor AT ALL,
 * and a shared `#settings/<page>?section=…` link has nothing to name.
 *
 * TWO CONTRACTS, ASSERTED TOGETHER, because they are one contract: the rail's
 * entries AND the exact id strings. A section that gained a label but whose
 * slug changed would still break every previously-shared link. The id strings
 * below were read off the rendered app before this bead was fixed — they are
 * the ids already in circulation, not ids invented here.
 *
 * EVERY RELEVANT FETCH STAYS PENDING FOR THE WHOLE TEST, so nothing here can
 * pass by waiting for a page to settle. `getSettings` is pending too: nothing
 * on these pages is gated on it, which is itself worth pinning.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  getChannelProfiles: vi.fn(),
  generateMCPApiKey: vi.fn(),
  revokeMCPApiKey: vi.fn(),
  getMCPStatus: vi.fn(),
  getAuthSettings: vi.fn(),
  updateAuthSettings: vi.fn(),
  listAlertMethods: vi.fn(),
  getM3UAccounts: vi.fn(),
  getExportSections: vi.fn(),
  listSavedBackups: vi.fn(),
  getStreams: vi.fn(),
  getProbeHistory: vi.fn(),
  getProbeProgress: vi.fn(),
  getM3UDigestSettings: vi.fn(),
  updateM3UDigestSettings: vi.fn(),
  sendTestM3UDigest: vi.fn(),
}));

vi.mock('../../services/channelPipelineApi', () => ({
  getChannelPipelineRules: vi.fn(),
  getChannelPipelineGroups: vi.fn(),
  generateAndFetchDebugBundle: vi.fn(),
}));

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    notify: vi.fn().mockReturnValue('toast-id'),
    dismiss: vi.fn(),
  }),
}));

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ user: { is_admin: true, username: 'admin' } }),
}));

// Sub-components pulled in at SettingsTab module scope that none of these
// pages render. AuthSettingsSection and MCPSettingsSection are deliberately
// NOT stubbed — they are two of the three pages under test.
vi.mock('../settings/NormalizationEngineSection', () => ({
  NormalizationEngineSection: () => <div data-testid="stub-normalization" />,
}));
vi.mock('../settings/TagEngineSection', () => ({
  TagEngineSection: () => <div data-testid="stub-tag-engine" />,
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
vi.mock('../ScheduledTasksSection', () => ({
  ScheduledTasksSection: () => <div data-testid="stub-scheduled-tasks" />,
}));
vi.mock('../SettingsModal', () => ({
  SettingsModal: () => <div data-testid="stub-settings-modal" />,
}));
vi.mock('../DeleteOrphanedGroupsModal', () => ({
  DeleteOrphanedGroupsModal: () => <div data-testid="stub-delete-orphaned" />,
}));

import * as api from '../../services/api';
import type { SettingsPage } from '../../hooks';
import { SettingsTab } from './SettingsTab';

/** A promise that never settles — the page stays in its loading branch. */
const pending = <T,>() => new Promise<T>(() => {});

const idleProbe = {
  in_progress: false, total: 0, current: 0, status: 'idle', current_stream: '',
  success_count: 0, failed_count: 0, skipped_count: 0, black_screen_count: 0,
  low_fps_count: 0, percentage: 0,
};

function renderPage(page: SettingsPage) {
  return render(<SettingsTab onSaved={vi.fn()} initialSettingsPage={page} />);
}

/**
 * Asserts the rail lists exactly `sections` in order, and that each one's
 * anchor exists under the id a shared link would name.
 */
async function expectRail(container: HTMLElement, sections: readonly (readonly [string, string])[]) {
  const nav = await screen.findByRole('navigation', { name: 'On this page' });
  expect(within(nav).getAllByRole('button').map((b) => b.textContent))
    .toEqual(sections.map(([label]) => label));
  for (const [, id] of sections) {
    expect(container.querySelector(`#${id}`), id).toBeInTheDocument();
  }
}

describe('Settings section rail — complete from first paint (bead b32co)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    // SettingsTab's own mount fetches. `getSettings` stays pending on purpose:
    // no section on any page under test is gated on it.
    vi.mocked(api.getSettings).mockReturnValue(pending());
    vi.mocked(api.getStreams).mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
    vi.mocked(api.getProbeHistory).mockResolvedValue([]);
    vi.mocked(api.getProbeProgress).mockResolvedValue(idleProbe);
  });

  it('lists every M3U Digest section while the digest fetch is still pending', async () => {
    vi.mocked(api.getM3UDigestSettings).mockReturnValue(pending());
    vi.mocked(api.getM3UAccounts).mockReturnValue(pending());

    const { container } = renderPage('m3u-digest');

    // This is the loading window, not a settled page.
    expect(screen.getByText('Loading digest settings...')).toBeInTheDocument();
    await expectRail(container, [
      ['Digest Notifications', 'settings-m3u-digest-section-digest-notifications'],
      ['Frequency', 'settings-m3u-digest-section-frequency'],
      ['Content Filters', 'settings-m3u-digest-section-content-filters'],
      ['Account Filter', 'settings-m3u-digest-section-account-filter'],
      ['Exclude Patterns', 'settings-m3u-digest-section-exclude-patterns'],
      ['Email Recipients', 'settings-m3u-digest-section-email-recipients'],
      ['Discord Notification', 'settings-m3u-digest-section-discord-notification'],
    ]);
  });

  it('lists every Authentication section while the auth fetch is still pending', async () => {
    vi.mocked(api.getAuthSettings).mockReturnValue(pending());

    const { container } = renderPage('auth-settings');

    expect(screen.getByText('Loading authentication settings...')).toBeInTheDocument();
    await expectRail(container, [
      ['Global Settings', 'settings-auth-settings-section-global-settings'],
      ['Local Authentication', 'settings-auth-settings-section-local-authentication'],
      ['Dispatcharr SSO', 'settings-auth-settings-section-dispatcharr-sso'],
    ]);
  });

  it('lists every always-present MCP section while the MCP fetches are still pending', async () => {
    // MCPSettingsSection waits on the same `getSettings` the whole tab uses,
    // which is already pending above; `getMCPStatus` joins it.
    vi.mocked(api.getMCPStatus).mockReturnValue(pending());

    const { container } = renderPage('mcp-settings');

    expect(screen.getByText('Loading MCP settings...')).toBeInTheDocument();
    // "Connection" and "Available Tools" are gated on a key being configured,
    // which is data, not loading — deliberately not listed here (bead ue130).
    await expectRail(container, [
      ['Server Status', 'settings-mcp-settings-section-server-status'],
      ['API Key', 'settings-mcp-settings-section-api-key'],
    ]);
  });
});

/**
 * The Maintenance page's probe-progress banner is a transient status card, not
 * a settings section: it mounts only while a probe is running and has no
 * heading of its own. It used to carry `.settings-section` for card chrome,
 * which put it inside the rail's selector while leaving it unlabelled — so
 * `discover()` skipped it and it never received an id, forever.
 *
 * The general contract this pins: NOTHING the rail's selector matches may be
 * unlabelled. An unlabelled match is a card that can never be linked to.
 */
describe('Settings section rail — the probe-progress banner is not a section (bead b32co)', () => {
  const runningProbe = {
    ...idleProbe, in_progress: true, total: 100, current: 37, status: 'running',
    current_stream: 'Example HD', success_count: 30, failed_count: 5, skipped_count: 2, percentage: 37,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(api.getSettings).mockReturnValue(pending());
    vi.mocked(api.getStreams).mockResolvedValue({ count: 0, next: null, previous: null, results: [] });
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
    vi.mocked(api.getProbeHistory).mockResolvedValue([]);
    vi.mocked(api.getProbeProgress).mockResolvedValue(runningProbe);
  });

  afterEach(() => {
    vi.mocked(api.getProbeProgress).mockResolvedValue(idleProbe);
  });

  it('shows the running-probe banner without adding a rail entry or an unlabelled section', async () => {
    const { container } = renderPage('maintenance');

    // The banner is on screen — this is the state the defect lived in.
    await waitFor(() => {
      expect(screen.getByText(/Probing streams\.\.\. 37\/100/)).toBeInTheDocument();
    });

    const pane = container.querySelector<HTMLElement>('.settings-content-main');
    expect(pane).not.toBeNull();
    const matches = [...pane!.querySelectorAll<HTMLElement>('.settings-section, [data-settings-section]')];
    const unlabelled = matches.filter((el) => !el.id);
    expect(unlabelled.map((el) => el.className)).toEqual([]);

    // "Reset Probe State" is in this list BECAUSE a probe is running: the
    // section is unconditional since bead enhancedchannelmanager-xg9gp, so the
    // rail no longer loses an entry — and shift every entry below it — when a
    // backend-scheduled probe starts under the reader.
    await expectRail(container, [
      ['Stream Probing', 'settings-maintenance-section-stream-probing'],
      ['Reset Probe State', 'settings-maintenance-section-reset-probe-state'],
      ['Orphaned Channel Groups', 'settings-maintenance-section-orphaned-channel-groups'],
      ['Strike Rule', 'settings-maintenance-section-strike-rule'],
      ['Stale Streams', 'settings-maintenance-section-stale-streams'],
      ['Auto-Created Channels', 'settings-maintenance-section-auto-created-channels'],
      ['Channel Groups Diagnostic', 'settings-maintenance-section-channel-groups-diagnostic'],
      ['Channel Groups With Streams', 'settings-maintenance-section-channel-groups-with-streams'],
    ]);
  });
});

/**
 * bead enhancedchannelmanager-xg9gp — the recovery control must not be hidden
 * by the condition it recovers from.
 *
 * THE LOOP THIS PINS SHUT. "Reset Stuck Probe" calls
 * `force_reset_probe_state()`, the only thing in the backend that clears
 * `StreamProber._probing_in_progress`. That flag is what the probe-progress
 * poll reports as `in_progress`, which is what drives `probingAll`, which used
 * to gate the whole section behind `{!probingAll && …}`. So a probe that wedges
 * reports `in_progress: true` forever, and the section that clears the flag is
 * never rendered. The banner's Cancel is not an escape hatch either:
 * `cancel_probe()` only sets `_probe_cancelled = True` for the probe loop to
 * observe, and a wedged loop never observes it.
 *
 * THE PROBE-PROGRESS FETCH RESOLVES HERE, deliberately — `in_progress: true` IS
 * the failure state under test, so it has to arrive. Every OTHER fetch on the
 * page stays pending, so nothing below can pass by waiting for the page to
 * settle.
 */
describe('Settings maintenance — the probe recovery control survives a running probe (bead xg9gp)', () => {
  const runningProbe = {
    ...idleProbe, in_progress: true, total: 100, current: 37, status: 'running',
    current_stream: 'Example HD', success_count: 30, failed_count: 5, skipped_count: 2, percentage: 37,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollTo = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    vi.mocked(api.getSettings).mockReturnValue(pending());
    vi.mocked(api.getStreams).mockReturnValue(pending());
    vi.mocked(api.getM3UAccounts).mockReturnValue(pending());
    vi.mocked(api.getProbeHistory).mockReturnValue(pending());
    vi.mocked(api.getProbeProgress).mockResolvedValue(runningProbe);
  });

  afterEach(() => {
    vi.mocked(api.getProbeProgress).mockResolvedValue(idleProbe);
  });

  it('keeps Reset Stuck Probe rendered, enabled and rail-linked while the probe reports in_progress', async () => {
    const { container } = renderPage('maintenance');

    // The banner is up: the page believes a probe is running. This is the
    // exact state an operator following the section's own copy is in.
    await waitFor(() => {
      expect(screen.getByText(/Probing streams\.\.\. 37\/100/)).toBeInTheDocument();
    });

    // Reachable three ways, because a control you cannot get to is not a
    // recovery path: rendered, enabled, and addressable from the rail.
    const reset = screen.getByRole('button', { name: /Reset Stuck Probe/ });
    expect(reset).toBeEnabled();
    expect(container.querySelector('#settings-maintenance-section-reset-probe-state')).toBeInTheDocument();
    const nav = await screen.findByRole('navigation', { name: 'On this page' });
    expect(within(nav).getByRole('button', { name: 'Reset Probe State' })).toBeInTheDocument();

    // Its neighbour is the ordinary control, and it IS suspended during a
    // probe — with the reason attached to the control rather than left to
    // inference, which is what makes hiding unnecessary.
    const clear = screen.getByRole('button', { name: /Clear All Probe Stats/ });
    expect(clear).toBeDisabled();
    expect(clear).toHaveAccessibleDescription(/probe is running/i);
  });

  it('leaves both controls enabled once no probe is running', async () => {
    vi.mocked(api.getProbeProgress).mockResolvedValue(idleProbe);

    renderPage('maintenance');

    const clear = await screen.findByRole('button', { name: /Clear All Probe Stats/ });
    await waitFor(() => expect(clear).toBeEnabled());
    expect(screen.getByRole('button', { name: /Reset Stuck Probe/ })).toBeEnabled();
    expect(screen.queryByText(/probe is running/i)).not.toBeInTheDocument();
  });
});
