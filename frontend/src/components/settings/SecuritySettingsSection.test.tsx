/**
 * Tests for SecuritySettingsSection (bead nngkg).
 *
 * Contract:
 *   - Reversible Settings home for the outbound-destination policy: the
 *     operator can switch between "home network" (LAN-friendly, DEFAULT) and
 *     "internet only" (public-only) and the choice is persisted.
 *   - The current mode is loaded from settings and reflected in the radios.
 *   - Changing the mode persists via saveSecurityMode.
 *   - PLAIN LANGUAGE: no "SSRF" / "RFC1918" jargon anywhere in the copy.
 *   - Admin-gated (mirrors the other admin settings sections).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

vi.mock('../../services/api', () => ({
  getSettings: vi.fn(),
  saveSecurityMode: vi.fn(),
}));

const notify = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

import * as api from '../../services/api';
import { SecuritySettingsSection } from './SecuritySettingsSection';

type Mock = ReturnType<typeof vi.fn>;

function mockMode(mode: api.OutboundPolicyMode) {
  (api.getSettings as Mock).mockResolvedValue({ ssrf_outbound_mode: mode });
}

describe('SecuritySettingsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.saveSecurityMode as Mock).mockResolvedValue({ ssrf_outbound_mode: 'public_only' });
  });

  it('blocks non-admins', () => {
    render(<SecuritySettingsSection isAdmin={false} />);
    expect(screen.getByText(/only administrators/i)).toBeInTheDocument();
    expect(api.getSettings).not.toHaveBeenCalled();
  });

  it('loads and reflects the LAN-friendly (default) mode', async () => {
    mockMode('lan_friendly');
    render(<SecuritySettingsSection isAdmin />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    const lan = screen.getByTestId('outbound-mode-lan_friendly') as HTMLInputElement;
    expect(lan.checked).toBe(true);
  });

  it('reflects public-only when that is the stored mode', async () => {
    mockMode('public_only');
    render(<SecuritySettingsSection isAdmin />);
    await waitFor(() => {
      const pub = screen.getByTestId('outbound-mode-public_only') as HTMLInputElement;
      expect(pub.checked).toBe(true);
    });
  });

  it('persists a switch to internet-only (public_only)', async () => {
    mockMode('lan_friendly');
    render(<SecuritySettingsSection isAdmin />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('outbound-mode-public_only'));

    await waitFor(() =>
      expect(api.saveSecurityMode).toHaveBeenCalledWith('public_only'),
    );
  });

  it('is reversible — can switch back to home network (lan_friendly)', async () => {
    mockMode('public_only');
    (api.saveSecurityMode as Mock).mockResolvedValue({ ssrf_outbound_mode: 'lan_friendly' });
    render(<SecuritySettingsSection isAdmin />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('outbound-mode-lan_friendly'));

    await waitFor(() =>
      expect(api.saveSecurityMode).toHaveBeenCalledWith('lan_friendly'),
    );
  });

  it('uses plain language — no SSRF / RFC1918 jargon', async () => {
    mockMode('lan_friendly');
    const { container } = render(<SecuritySettingsSection isAdmin />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    const text = container.textContent ?? '';
    expect(text).not.toMatch(/SSRF/i);
    expect(text).not.toMatch(/RFC\s?1918/i);
  });
});
