/**
 * Unit tests for MCPSettingsSection — self-diagnosing Server Status panel.
 *
 * Pinned behavior (bd-ix1g6): when /api/settings/mcp-status returns the
 * MCP server reachable but unconfigured, the panel surfaces a machine-
 * readable diagnostic code (api_key_status) + a setup_hint so the operator
 * can tell apart deployment misconfiguration from a not-yet-generated key
 * without container shell access.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MCPSettingsSection } from './MCPSettingsSection';

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  generateMCPApiKey: vi.fn(),
  revokeMCPApiKey: vi.fn(),
  getMCPStatus: vi.fn(),
  getOAuthGrants: vi.fn(),
  revokeOAuthGrant: vi.fn(),
  revokeAllOAuthGrants: vi.fn(),
}));

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

import * as api from '../../services/api';

const settingsConfigured = {
  mcp_api_key_configured: true,
  // Minimal stub — only the field the component reads is asserted upstream.
} as unknown as Awaited<ReturnType<typeof api.getSettings>>;

const settingsUnconfigured = {
  mcp_api_key_configured: false,
} as unknown as Awaited<ReturnType<typeof api.getSettings>>;

describe('MCPSettingsSection — Server Status diagnostic (bd-ix1g6)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the online badge with tool count when reachable AND key configured', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(settingsConfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({
      reachable: true,
      api_key_configured: true,
      api_key_status: 'ok',
      tools_available: 124,
    });

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => {
      expect(screen.getByText(/MCP server online — 124 tools available/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId('mcp-status-unconfigured')).not.toBeInTheDocument();
  });

  it('shows the file_not_found diagnostic when the volume mount is broken', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(settingsUnconfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({
      reachable: true,
      api_key_configured: false,
      api_key_status: 'file_not_found',
      setup_hint:
        'ECM has not written settings.json yet, or the MCP container\'s /config volume is not sharing the same data as ECM. Verify both containers mount the same volume and that ECM Settings has been saved at least once.',
    });

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => {
      expect(screen.getByTestId('mcp-status-unconfigured')).toBeInTheDocument();
    });
    expect(screen.getByTestId('mcp-api-key-status')).toHaveTextContent('file_not_found');
    expect(screen.getByTestId('mcp-status-hint')).toHaveTextContent(/volume/i);
  });

  it('shows the field_empty diagnostic when no key has been generated yet', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(settingsUnconfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({
      reachable: true,
      api_key_configured: false,
      api_key_status: 'field_empty',
      setup_hint: 'No MCP API key configured. Generate one in ECM Settings > MCP Integration.',
    });

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => {
      expect(screen.getByTestId('mcp-api-key-status')).toHaveTextContent('field_empty');
    });
    expect(screen.getByTestId('mcp-status-hint')).toHaveTextContent(/generate one/i);
  });

  it('shows the offline badge when the MCP server is unreachable', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(settingsUnconfigured);
    vi.mocked(api.getMCPStatus).mockRejectedValue(new Error('connection refused'));

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => {
      expect(screen.getByText(/MCP server not reachable/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId('mcp-status-unconfigured')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mcp-status-hint')).not.toBeInTheDocument();
  });
});

/**
 * bd-buiqr10 (Option-A slice): signing_key_missing diagnostic rendering.
 *
 * SKIPPED (bd-9axgc): the MCP OAuth offering was retired. The OAuth signing-key
 * diagnostic (signing_key_status / signing_key_hint) was removed from the
 * MCPSettingsSection Server Status panel, so these assertions no longer have a
 * UI to exercise. Re-enable when MCP OAuth is re-offered.
 */
describe.skip('MCPSettingsSection — signing key diagnostic (bd-buiqr10) [retired bd-9axgc]', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows signing_key_hint when signing_key_status is signing_key_missing', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(settingsConfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({
      reachable: true,
      api_key_configured: true,
      api_key_status: 'ok',
      tools_available: 80,
      signing_key_status: 'signing_key_missing',
      signing_key_hint:
        'The OAuth signing secret (mcp_oauth_signing_secret) is not present in settings.json. ' +
        'OAuth Bearer-JWT verification requires this shared HS256 secret.',
    });

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => {
      expect(screen.getByTestId('mcp-signing-key-hint')).toBeInTheDocument();
    });
    expect(screen.getByTestId('mcp-signing-key-hint')).toHaveTextContent(/oauth/i);
    // api_key_status hint should not appear (api key is ok)
    expect(screen.queryByTestId('mcp-status-hint')).not.toBeInTheDocument();
  });

  it('does NOT show signing_key_hint when signing_key_status is ok', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(settingsConfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({
      reachable: true,
      api_key_configured: true,
      api_key_status: 'ok',
      tools_available: 80,
      signing_key_status: 'ok',
    });

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => {
      expect(screen.getByText(/MCP server online — 80 tools available/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId('mcp-signing-key-hint')).not.toBeInTheDocument();
  });

  it('does NOT show signing_key_hint when there is no signing_key_hint text', async () => {
    // If the server returns signing_key_status != 'ok' but no signing_key_hint,
    // nothing should be rendered for it.
    vi.mocked(api.getSettings).mockResolvedValue(settingsConfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({
      reachable: true,
      api_key_configured: true,
      api_key_status: 'ok',
      tools_available: 80,
      signing_key_status: 'signing_key_missing',
      // No signing_key_hint provided
    });

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => {
      expect(screen.getByText(/MCP server online — 80 tools available/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId('mcp-signing-key-hint')).not.toBeInTheDocument();
  });

  it('does NOT show signing_key_hint when the server is unreachable', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(settingsUnconfigured);
    vi.mocked(api.getMCPStatus).mockRejectedValue(new Error('connection refused'));

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => {
      expect(screen.getByText(/MCP server not reachable/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId('mcp-signing-key-hint')).not.toBeInTheDocument();
  });

  it('can show both setup_hint and signing_key_hint simultaneously', async () => {
    // Both api_key and signing key are misconfigured — operator sees both hints.
    vi.mocked(api.getSettings).mockResolvedValue(settingsUnconfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({
      reachable: true,
      api_key_configured: false,
      api_key_status: 'field_missing',
      setup_hint: 'Open ECM Settings > MCP Integration and generate a key.',
      signing_key_status: 'signing_key_missing',
      signing_key_hint:
        'The OAuth signing secret (mcp_oauth_signing_secret) is not present in settings.json.',
    });

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => {
      expect(screen.getByTestId('mcp-status-hint')).toBeInTheDocument();
    });
    expect(screen.getByTestId('mcp-signing-key-hint')).toBeInTheDocument();
    // The two hints should have distinct text
    const setupHintText = screen.getByTestId('mcp-status-hint').textContent ?? '';
    const signingHintText = screen.getByTestId('mcp-signing-key-hint').textContent ?? '';
    expect(setupHintText).not.toBe(signingHintText);
  });
});

/**
 * Active Connections (OAuth grants) — bead buiqr.7 (d).
 *
 * SKIPPED (bd-9axgc): the MCP OAuth offering was retired. The "Active
 * Connections" (OAuth grants) section was removed from MCPSettingsSection, so
 * there is no grants list / inline-revoke UI to exercise. Re-enable when MCP
 * OAuth is re-offered.
 */
describe.skip('MCPSettingsSection — Active Connections (bd-buiqr.7) [retired bd-9axgc]', () => {
  const grant = {
    id: 'fam-1',
    client_id: 'claude-desktop',
    client_name: 'Claude Desktop',
    granted_at: 1_700_000_000,
    last_used: 1_700_100_000,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue(settingsConfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({
      reachable: true,
      api_key_configured: true,
      api_key_status: 'ok',
      tools_available: 83,
    });
  });

  it('does NOT render the section when there are no grants', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [] });

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => {
      expect(screen.getByText(/MCP server online/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId('oauth-grants-section')).not.toBeInTheDocument();
  });

  it('lists active grants with client name and timestamps', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [grant] });

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => {
      expect(screen.getByTestId('oauth-grants-section')).toBeInTheDocument();
    });
    const row = screen.getByTestId('oauth-grant-row');
    expect(row).toHaveTextContent('Claude Desktop');
    expect(row).toHaveTextContent(/Granted/i);
    expect(row).toHaveTextContent(/Last used/i);
  });

  it('uses inline confirmation (not a modal) before revoking', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [grant] });

    render(<MCPSettingsSection isAdmin={true} />);
    await waitFor(() => screen.getByTestId('oauth-grant-revoke'));

    // No confirm UI until the revoke button is clicked.
    expect(screen.queryByRole('group', { name: /confirm revoke/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('oauth-grant-revoke'));
    // Inline confirm appears in place — no modal/dialog role.
    expect(screen.getByRole('group', { name: /confirm revoke/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /yes, revoke/i })).toBeInTheDocument();
  });

  it('revokes the grant, removes the row, and fires a success toast', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [grant] });
    vi.mocked(api.revokeOAuthGrant).mockResolvedValue(undefined);

    render(<MCPSettingsSection isAdmin={true} />);
    await waitFor(() => screen.getByTestId('oauth-grant-revoke'));

    fireEvent.click(screen.getByTestId('oauth-grant-revoke'));
    fireEvent.click(screen.getByRole('button', { name: /yes, revoke/i }));

    await waitFor(() => {
      expect(api.revokeOAuthGrant).toHaveBeenCalledWith('fam-1');
    });
    await waitFor(() => {
      expect(screen.queryByTestId('oauth-grant-row')).not.toBeInTheDocument();
    });
  });

  it('canceling the inline confirm leaves the grant in place', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [grant] });

    render(<MCPSettingsSection isAdmin={true} />);
    await waitFor(() => screen.getByTestId('oauth-grant-revoke'));

    fireEvent.click(screen.getByTestId('oauth-grant-revoke'));
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

    expect(api.revokeOAuthGrant).not.toHaveBeenCalled();
    expect(screen.getByTestId('oauth-grant-row')).toBeInTheDocument();
  });
});

/**
 * Bulk-revoke panic button — bead buiqr.12.
 *
 * SKIPPED (bd-9axgc): the MCP OAuth offering was retired. The bulk-revoke panic
 * button lived inside the now-removed "Active Connections" (OAuth grants)
 * section, so there is no UI to exercise. Re-enable when MCP OAuth is
 * re-offered.
 */
describe.skip('MCPSettingsSection — Bulk Revoke (bd-buiqr.12) [retired bd-9axgc]', () => {
  const grant = {
    id: 'fam-bulk-1',
    client_id: 'claude-desktop',
    client_name: 'Claude Desktop',
    granted_at: 1_700_000_000,
    last_used: 1_700_100_000,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue(settingsConfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({
      reachable: true,
      api_key_configured: true,
      api_key_status: 'ok',
      tools_available: 80,
    });
  });

  it('AC1: bulk-revoke button is visible when grants exist', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [grant] });

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() => screen.getByTestId('oauth-grants-section'));
    expect(screen.getByTestId('oauth-bulk-revoke-btn')).toBeInTheDocument();
  });

  it('AC1: bulk-revoke button is NOT present when there are no grants', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [] });

    render(<MCPSettingsSection isAdmin={true} />);

    await waitFor(() =>
      expect(screen.queryByTestId('oauth-grants-section')).not.toBeInTheDocument()
    );
    expect(screen.queryByTestId('oauth-bulk-revoke-btn')).not.toBeInTheDocument();
  });

  it('AC2: first confirmation prompt appears on button click', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [grant] });

    render(<MCPSettingsSection isAdmin={true} />);
    await waitFor(() => screen.getByTestId('oauth-bulk-revoke-btn'));

    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-btn'));

    expect(screen.getByTestId('oauth-bulk-revoke-confirm1')).toBeInTheDocument();
    expect(screen.getByText(/all apps/i)).toBeInTheDocument();
    // The type-REVOKE gate must NOT be shown yet (only after the first confirm).
    expect(screen.queryByTestId('oauth-bulk-revoke-confirm2')).not.toBeInTheDocument();
    expect(api.revokeAllOAuthGrants).not.toHaveBeenCalled();
  });

  it('AC2: canceling the first confirmation returns to idle state', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [grant] });

    render(<MCPSettingsSection isAdmin={true} />);
    await waitFor(() => screen.getByTestId('oauth-bulk-revoke-btn'));

    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-btn'));
    expect(screen.getByTestId('oauth-bulk-revoke-confirm1')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-cancel'));

    expect(screen.queryByTestId('oauth-bulk-revoke-confirm1')).not.toBeInTheDocument();
    expect(screen.getByTestId('oauth-bulk-revoke-btn')).toBeInTheDocument();
    expect(api.revokeAllOAuthGrants).not.toHaveBeenCalled();
  });

  it('AC2: second confirmation (type-REVOKE) appears after first yes', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [grant] });

    render(<MCPSettingsSection isAdmin={true} />);
    await waitFor(() => screen.getByTestId('oauth-bulk-revoke-btn'));

    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-btn'));
    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-confirm1-yes'));

    expect(screen.getByTestId('oauth-bulk-revoke-confirm2')).toBeInTheDocument();
    expect(screen.getByTestId('oauth-bulk-revoke-input')).toBeInTheDocument();
    // The final button must be disabled until the user types REVOKE.
    expect(screen.getByTestId('oauth-bulk-revoke-confirm2-yes')).toBeDisabled();
    expect(api.revokeAllOAuthGrants).not.toHaveBeenCalled();
  });

  it('AC2: final confirm button is disabled until "REVOKE" is typed exactly', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [grant] });

    render(<MCPSettingsSection isAdmin={true} />);
    await waitFor(() => screen.getByTestId('oauth-bulk-revoke-btn'));

    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-btn'));
    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-confirm1-yes'));

    const input = screen.getByTestId('oauth-bulk-revoke-input');
    const confirmBtn = screen.getByTestId('oauth-bulk-revoke-confirm2-yes');

    fireEvent.change(input, { target: { value: 'revoke' } }); // lowercase — not valid
    expect(confirmBtn).toBeDisabled();

    fireEvent.change(input, { target: { value: 'REVOK' } }); // partial — not valid
    expect(confirmBtn).toBeDisabled();

    fireEvent.change(input, { target: { value: 'REVOKE' } }); // exact match
    expect(confirmBtn).not.toBeDisabled();
  });

  it('AC3: confirming bulk revoke clears all grants and fires success toast', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [grant] });
    vi.mocked(api.revokeAllOAuthGrants).mockResolvedValue({ revoked: 1 });

    const notifMock = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
    // Re-mock the notification context for this test to capture the call.
    vi.doMock('../../contexts/NotificationContext', () => ({
      useNotifications: () => notifMock,
    }));

    render(<MCPSettingsSection isAdmin={true} />);
    await waitFor(() => screen.getByTestId('oauth-bulk-revoke-btn'));

    // Drive through both confirmation steps.
    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-btn'));
    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-confirm1-yes'));

    const input = screen.getByTestId('oauth-bulk-revoke-input');
    fireEvent.change(input, { target: { value: 'REVOKE' } });
    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-confirm2-yes'));

    await waitFor(() => {
      expect(api.revokeAllOAuthGrants).toHaveBeenCalledTimes(1);
    });

    // After bulk revoke the grants list clears — section is gone.
    await waitFor(() => {
      expect(screen.queryByTestId('oauth-grants-section')).not.toBeInTheDocument();
    });
  });

  it('AC3: canceling the type-REVOKE gate returns to idle', async () => {
    vi.mocked(api.getOAuthGrants).mockResolvedValue({ grants: [grant] });

    render(<MCPSettingsSection isAdmin={true} />);
    await waitFor(() => screen.getByTestId('oauth-bulk-revoke-btn'));

    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-btn'));
    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-confirm1-yes'));

    // Cancel from the second stage.
    fireEvent.click(screen.getByTestId('oauth-bulk-revoke-cancel'));

    expect(screen.queryByTestId('oauth-bulk-revoke-confirm2')).not.toBeInTheDocument();
    expect(screen.getByTestId('oauth-bulk-revoke-btn')).toBeInTheDocument();
    expect(api.revokeAllOAuthGrants).not.toHaveBeenCalled();
  });
});
