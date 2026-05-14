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
import { render, screen, waitFor } from '@testing-library/react';
import { MCPSettingsSection } from './MCPSettingsSection';

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  generateMCPApiKey: vi.fn(),
  revokeMCPApiKey: vi.fn(),
  getMCPStatus: vi.fn(),
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
