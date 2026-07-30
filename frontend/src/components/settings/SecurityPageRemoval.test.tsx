/**
 * Regression test for the removed Administration → "Security" nav item
 * (bead 09x38.12). The page held exactly one setting (the backup-destination
 * SSRF allowlist), which relocated to Backup & Restore (OutboundPolicyCard);
 * the nav item and standalone page are gone.
 *
 * Renders SettingsTab in isolation with the heavyweight sub-sections stubbed
 * out, mirroring the pattern in DeduplicationSettingsSection.test.tsx.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  getChannelProfiles: vi.fn(),
  generateMCPApiKey: vi.fn(),
  revokeMCPApiKey: vi.fn(),
  getMCPStatus: vi.fn(),
  listAlertMethods: vi.fn(),
  getM3UAccounts: vi.fn(),
  getExportSections: vi.fn(),
  listSavedBackups: vi.fn(),
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
import { SettingsTab } from '../tabs/SettingsTab';
import { TabNavigation } from '../TabNavigation';
import { SETTINGS_SECTIONS } from '../settingsSections';

describe('Administration nav — Security page removed (bead 09x38.12)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue({} as Awaited<ReturnType<typeof api.getSettings>>);
    vi.mocked(api.getChannelProfiles).mockResolvedValue([]);
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([]);
  });

  // The Settings section list moved out of SettingsTab into the primary
  // sidebar's drill-in view, so the nav assertions now target that.
  it('does not render a "Security" item under the Administration nav group', () => {
    render(
      <TabNavigation activeTab="settings" onTabChange={vi.fn()} isAdmin settingsPage="general" />,
    );

    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).getByRole('heading', { name: 'Administration' })).toBeInTheDocument();
    expect(within(nav).queryByRole('link', { name: 'Security' })).not.toBeInTheDocument();
    expect(SETTINGS_SECTIONS.map((section) => section.label)).not.toContain('Security');
  });

  it('still renders the other Administration nav items (Authentication, TLS Certificates, MCP Integration)', () => {
    render(
      <TabNavigation activeTab="settings" onTabChange={vi.fn()} isAdmin settingsPage="general" />,
    );

    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    for (const label of ['Authentication', 'TLS Certificates', 'MCP Integration']) {
      expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument();
    }
  });

  it('withholds the Administration sections from non-admin operators', () => {
    render(
      <TabNavigation activeTab="settings" onTabChange={vi.fn()} settingsPage="general" />,
    );

    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).queryByRole('heading', { name: 'Administration' })).not.toBeInTheDocument();
    for (const label of ['Authentication', 'User Management', 'TLS Certificates', 'MCP Integration']) {
      expect(within(nav).queryByRole('link', { name: label })).not.toBeInTheDocument();
    }
    expect(within(nav).getByRole('link', { name: 'General' })).toBeInTheDocument();
  });

  it('renders the Backup & Restore section (now home to the relocated policy card) via nav click', async () => {
    render(<SettingsTab onSaved={vi.fn()} initialSettingsPage="backup-restore" />);

    await waitFor(() => {
      expect(screen.getByTestId('stub-backup')).toBeInTheDocument();
    });
  });
});
