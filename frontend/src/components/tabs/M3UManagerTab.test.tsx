/**
 * Tests for the dedicated "Custom streams" Smart Sort priority control in
 * M3UManagerTab (enhancedchannelmanager-4g2t1 / GH #244).
 *
 * Operator-added custom streams in Dispatcharr belong to a real M3U account
 * literally named "custom" (verified live: id=1). They carry
 * m3u_account_id = that account's id, NOT None, so the backend Smart Sort
 * already ranks them via settings.m3u_account_priorities[str(custom_id)].
 *
 * PR #310 added a reserved literal "custom" key to the backend that only fires
 * for m3u_account_id None — which never occurs for real custom streams — so it
 * was a no-op. This control writes under String(customAccount.id) instead, the
 * SAME key the backend reads. The "writes the right key" test below is the
 * load-bearing lock: it guards against shipping a control that writes a key the
 * backend ignores (exactly the bug PR #310 shipped).
 *
 * The custom account must NOT appear as a normal M3U account row — it stays
 * excluded from filteredAccounts and is surfaced only through this control.
 */
import type * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { M3UManagerTab } from './M3UManagerTab';
import { NotificationProvider } from '../../contexts/NotificationContext';
import * as api from '../../services/api';
import type { M3UAccount } from '../../types';
import type { SettingsResponse } from '../../services/api';

// Mock the API module — the M3UChangesTab tests use the same pattern.
vi.mock('../../services/api');

const renderWithProviders = (ui: React.JSX.Element) =>
  render(<NotificationProvider>{ui}</NotificationProvider>);

// Build a complete M3UAccount; callers override only what the test cares about.
function makeAccount(overrides: Partial<M3UAccount> = {}): M3UAccount {
  return {
    id: 99,
    name: 'Provider A',
    server_url: 'http://provider-a.example/m3u',
    file_path: null,
    server_group: null,
    max_streams: 0,
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    user_agent: null,
    profiles: [],
    locked: false,
    channel_groups: [],
    refresh_interval: 0,
    custom_properties: null,
    account_type: 'STD',
    username: null,
    password: null,
    stale_stream_days: 0,
    priority: 0,
    status: 'success',
    last_message: null,
    enable_vod: false,
    auto_enable_new_groups_live: false,
    auto_enable_new_groups_vod: false,
    auto_enable_new_groups_series: false,
    ...overrides,
  };
}

// Minimal SettingsResponse — only the fields the component reads matter; the
// rest are cast through to satisfy the type without enumerating every field.
function makeSettings(overrides: Partial<SettingsResponse> = {}): SettingsResponse {
  return {
    linked_m3u_accounts: [],
    m3u_account_priorities: {},
    ...overrides,
  } as unknown as SettingsResponse;
}

const customAccount = makeAccount({ id: 1, name: 'custom', account_type: 'STD' });
const normalAccount = makeAccount({ id: 42, name: 'Provider A' });

describe('M3UManagerTab — Custom streams priority control (4g2t1 / GH #244)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getServerGroups).mockResolvedValue([]);
  });

  it('renders the Custom streams control showing the saved priority for the custom account id', async () => {
    vi.mocked(api.getM3UAccounts).mockResolvedValue([customAccount, normalAccount]);
    vi.mocked(api.getSettings).mockResolvedValue(
      makeSettings({ m3u_account_priorities: { '1': 200 } }),
    );

    renderWithProviders(<M3UManagerTab />);

    // The dedicated control renders and reflects the saved value (200) read
    // from settings under String(customAccount.id) === "1".
    const control = await screen.findByTestId('custom-streams-row');
    expect(control).toBeInTheDocument();
    const input = screen.getByLabelText('Custom streams priority') as HTMLInputElement;
    expect(input.value).toBe('200');
    expect(screen.getByText('Custom streams')).toBeInTheDocument();
  });

  it('does NOT surface the custom account as a normal M3U account row', async () => {
    vi.mocked(api.getM3UAccounts).mockResolvedValue([customAccount, normalAccount]);
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());

    renderWithProviders(<M3UManagerTab />);

    // The real account renders as an account row.
    expect(await screen.findByText('Provider A')).toBeInTheDocument();

    // The custom account name appears ONLY inside the dedicated control, never
    // as an .m3u-account-row. filteredAccounts must still exclude it.
    const accountRows = document.querySelectorAll('.m3u-account-row');
    expect(accountRows.length).toBe(1);
    accountRows.forEach(row => {
      expect(row.textContent?.toLowerCase()).not.toContain('custom');
    });
  });

  it('saves the custom priority under String(customAccount.id) — the key the backend reads', async () => {
    vi.mocked(api.getM3UAccounts).mockResolvedValue([customAccount, normalAccount]);
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());
    vi.mocked(api.saveSettings).mockResolvedValue({
      status: 'ok',
      configured: true,
      server_changed: false,
    });

    renderWithProviders(<M3UManagerTab />);

    const input = (await screen.findByLabelText(
      'Custom streams priority',
    )) as HTMLInputElement;

    // Operator types a priority for custom streams.
    fireEvent.change(input, { target: { value: '75' } });
    expect(input.value).toBe('75');

    // Click Save Priorities (enabled once there are pending changes).
    const saveBtn = screen.getByRole('button', { name: /Save Priorities/i });
    await waitFor(() => expect(saveBtn).not.toBeDisabled());
    fireEvent.click(saveBtn);

    await waitFor(() => expect(api.saveSettings).toHaveBeenCalled());

    // The saved payload must key the priority under String(customAccount.id).
    const savedPayload = vi.mocked(api.saveSettings).mock.calls[0][0] as {
      m3u_account_priorities?: Record<string, number>;
    };
    const expectedKey = String(customAccount.id); // "1"
    expect(savedPayload.m3u_account_priorities).toBeDefined();
    expect(Object.keys(savedPayload.m3u_account_priorities!)).toContain(expectedKey);
    expect(savedPayload.m3u_account_priorities![expectedKey]).toBe(75);
    // The literal "custom" string key must NOT be written — that was PR #310's
    // dead key; the backend ignores it for real custom streams.
    expect(Object.keys(savedPayload.m3u_account_priorities!)).not.toContain('custom');
  });

  it('does NOT render the Custom streams control when no custom account exists', async () => {
    vi.mocked(api.getM3UAccounts).mockResolvedValue([normalAccount]);
    vi.mocked(api.getSettings).mockResolvedValue(makeSettings());

    renderWithProviders(<M3UManagerTab />);

    // Wait for load to settle (the real account row renders).
    expect(await screen.findByText('Provider A')).toBeInTheDocument();
    expect(screen.queryByTestId('custom-streams-row')).toBeNull();
    expect(screen.queryByLabelText('Custom streams priority')).toBeNull();
  });
});
