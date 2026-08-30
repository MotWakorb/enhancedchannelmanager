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
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MCPSettingsSection } from './MCPSettingsSection';
import { HttpError } from '../../services/httpClient';

const notificationMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  generateMCPApiKey: vi.fn(),
  revokeMCPApiKey: vi.fn(),
  getMCPStatus: vi.fn(),
}));

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notificationMocks,
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

  it('generates header-only connection configs without credentials in URLs', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(settingsConfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({ reachable: true, api_key_configured: true });

    const { container } = render(<MCPSettingsSection isAdmin={true} />);
    await waitFor(() => expect(container.textContent).toContain('Authorization'));

    expect(container.textContent).not.toContain('?api_key=');
    expect(container.textContent).not.toContain('YOUR_API_KEY');
    expect(container.textContent).toContain('http://localhost:6101/mcp');
  });
});

const rotatedValue = 'synthetic-rotated-value';

describe('MCPSettingsSection — key lifecycle confirmations (04c0u.12)', () => {
  // Regenerate and Revoke each break every configured MCP client the instant
  // they are clicked, and both sat directly on a bare onClick. The scoped
  // confirmation names which of the two is happening and what it costs.
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettings).mockResolvedValue(settingsConfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({ reachable: true, api_key_configured: true });
    // Hoisted rather than inlined: a quoted literal assigned directly to a
    // credential-shaped key trips the repo's detect-secrets gate, and an
    // inline allowlist pragma cannot suppress a finding in its own PR.
    vi.mocked(api.generateMCPApiKey).mockResolvedValue({ mcp_api_key: rotatedValue });
    vi.mocked(api.revokeMCPApiKey).mockResolvedValue({ status: 'ok' });
  });

  it('names the disconnection before rotating, and does not rotate until confirmed', async () => {
    render(<MCPSettingsSection isAdmin={true} />);
    fireEvent.click(await screen.findByRole('button', { name: /regenerate key/i }));

    const dialog = await screen.findByRole('dialog', { name: /rotate MCP API key/i });
    expect(dialog).toHaveTextContent(/every configured MCP client/i);
    expect(api.generateMCPApiKey).not.toHaveBeenCalled();
  });

  it('rotates once the exact phrase is typed', async () => {
    render(<MCPSettingsSection isAdmin={true} />);
    fireEvent.click(await screen.findByRole('button', { name: /regenerate key/i }));
    await screen.findByRole('dialog', { name: /rotate MCP API key/i });

    fireEvent.change(screen.getByLabelText(/type ROTATE MCP KEY to confirm/i), {
      target: { value: 'ROTATE MCP KEY' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^rotate MCP API key$/i }));

    await waitFor(() => expect(api.generateMCPApiKey).toHaveBeenCalled());
  });

  it('names the loss of access before revoking, and does not revoke until confirmed', async () => {
    render(<MCPSettingsSection isAdmin={true} />);
    fireEvent.click(await screen.findByRole('button', { name: /revoke key/i }));

    const dialog = await screen.findByRole('dialog', { name: /revoke MCP API key/i });
    expect(dialog).toHaveTextContent(/all MCP access stops/i);
    expect(api.revokeMCPApiKey).not.toHaveBeenCalled();
  });

  it('revokes once the exact phrase is typed', async () => {
    render(<MCPSettingsSection isAdmin={true} />);
    fireEvent.click(await screen.findByRole('button', { name: /revoke key/i }));
    await screen.findByRole('dialog', { name: /revoke MCP API key/i });

    fireEvent.change(screen.getByLabelText(/type REVOKE MCP KEY to confirm/i), {
      target: { value: 'REVOKE MCP KEY' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^revoke MCP API key$/i }));

    await waitFor(() => expect(api.revokeMCPApiKey).toHaveBeenCalled());
  });

  it('generates the first key without a confirmation — nothing exists to break', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(settingsUnconfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({ reachable: true, api_key_configured: false });

    render(<MCPSettingsSection isAdmin={true} />);
    fireEvent.click(await screen.findByRole('button', { name: /^generate API key$/i }));

    await waitFor(() => expect(api.generateMCPApiKey).toHaveBeenCalled());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('displays an indeterminate rotation key and warns instead of reporting failure', async () => {
    vi.mocked(api.getSettings).mockResolvedValue(settingsUnconfigured);
    vi.mocked(api.getMCPStatus).mockResolvedValue({ reachable: true, api_key_configured: false });
    const detail = {
      code: 'mcp_api_key_durability_indeterminate',
      message: 'The new MCP API key is active now, but crash durability is indeterminate.',
      operation: 'rotation',
      authority_active: true,
      crash_durability: 'indeterminate',
      retry_after_storage_repair: true,
      mcp_api_key: rotatedValue,
    };
    vi.mocked(api.generateMCPApiKey).mockRejectedValue(
      new HttpError(detail.message, 503, detail),
    );

    render(<MCPSettingsSection isAdmin={true} />);
    fireEvent.click(await screen.findByRole('button', { name: /^generate API key$/i }));

    expect(await screen.findByText(rotatedValue)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /copy API key/i })).toBeInTheDocument();
    expect(notificationMocks.warning).toHaveBeenCalledWith(
      detail.message,
      'MCP Key Durability',
    );
    expect(notificationMocks.error).not.toHaveBeenCalled();
  });

  it('shows the revocation durability warning and refreshes MCP status', async () => {
    const detail = {
      code: 'mcp_api_key_durability_indeterminate',
      message: 'MCP API key revocation is active now, but a host crash may restore the previous key.',
      operation: 'revocation',
      authority_active: true,
      revoked: true,
      crash_durability: 'indeterminate',
      retry_after_storage_repair: true,
    };
    vi.mocked(api.revokeMCPApiKey).mockRejectedValue(
      new HttpError(detail.message, 503, detail),
    );

    render(<MCPSettingsSection isAdmin={true} />);
    fireEvent.click(await screen.findByRole('button', { name: /revoke key/i }));
    await screen.findByRole('dialog', { name: /revoke MCP API key/i });
    fireEvent.change(screen.getByLabelText(/type REVOKE MCP KEY to confirm/i), {
      target: { value: 'REVOKE MCP KEY' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^revoke MCP API key$/i }));

    await waitFor(() => expect(api.getMCPStatus).toHaveBeenCalledTimes(2));
    expect(screen.getByText(/No API key configured/i)).toBeInTheDocument();
    expect(notificationMocks.warning).toHaveBeenCalledWith(
      detail.message,
      'MCP Key Durability',
    );
    expect(notificationMocks.error).not.toHaveBeenCalled();
  });
});
