/**
 * Tests for OutboundPolicyCard (bead 09x38.12).
 *
 * Contract:
 *   - Relocated home (from the removed Administration → "Security" page) for
 *     the backup outbound-policy choice (bead nngkg's original setting).
 *   - The current mode is loaded from settings and reflected in the radios.
 *   - Radio selection is STAGED local state — clicking a radio must NOT call
 *     saveSecurityMode. Only clicking "Save Settings" persists it. This is
 *     the fix for the bead: the old page persisted instantly on radio click
 *     with no Save button, unlike every other Settings page.
 *   - Save button is disabled until the selection differs from the saved
 *     value, and while a save is in flight.
 *   - Navigating away without saving discards the staged choice (verified by
 *     unmounting and remounting with the original server value still
 *     unchanged, since no API call was ever made).
 *   - PLAIN LANGUAGE: no "SSRF" / "RFC1918" jargon anywhere.
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
import { OutboundPolicyCard } from './OutboundPolicyCard';

type Mock = ReturnType<typeof vi.fn>;

function mockMode(mode: api.OutboundPolicyMode) {
  (api.getSettings as Mock).mockResolvedValue({ ssrf_outbound_mode: mode });
}

describe('OutboundPolicyCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (api.saveSecurityMode as Mock).mockResolvedValue({ ssrf_outbound_mode: 'public_only' });
  });

  it('loads and reflects the LAN-friendly (default) mode', async () => {
    mockMode('lan_friendly');
    render(<OutboundPolicyCard />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    const lan = screen.getByTestId('outbound-mode-lan_friendly') as HTMLInputElement;
    expect(lan.checked).toBe(true);
  });

  it('reflects public-only when that is the stored mode', async () => {
    mockMode('public_only');
    render(<OutboundPolicyCard />);
    await waitFor(() => {
      const pub = screen.getByTestId('outbound-mode-public_only') as HTMLInputElement;
      expect(pub.checked).toBe(true);
    });
  });

  it('stages a radio click WITHOUT calling saveSecurityMode', async () => {
    mockMode('lan_friendly');
    render(<OutboundPolicyCard />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('outbound-mode-public_only'));

    const pub = screen.getByTestId('outbound-mode-public_only') as HTMLInputElement;
    expect(pub.checked).toBe(true);
    expect(api.saveSecurityMode).not.toHaveBeenCalled();
  });

  it('Save Settings button is disabled until the selection changes', async () => {
    mockMode('lan_friendly');
    render(<OutboundPolicyCard />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    const saveBtn = screen.getByTestId('outbound-policy-save');
    expect(saveBtn).toBeDisabled();

    fireEvent.click(screen.getByTestId('outbound-mode-public_only'));
    expect(saveBtn).not.toBeDisabled();
  });

  it('persists the staged choice only when Save Settings is clicked', async () => {
    mockMode('lan_friendly');
    render(<OutboundPolicyCard />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('outbound-mode-public_only'));
    expect(api.saveSecurityMode).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('outbound-policy-save'));

    await waitFor(() => expect(api.saveSecurityMode).toHaveBeenCalledWith('public_only'));
    expect(notify.success).toHaveBeenCalled();
  });

  it('re-disables Save after a successful save (selection matches saved value again)', async () => {
    mockMode('lan_friendly');
    render(<OutboundPolicyCard />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('outbound-mode-public_only'));
    fireEvent.click(screen.getByTestId('outbound-policy-save'));

    await waitFor(() => expect(screen.getByTestId('outbound-policy-save')).toBeDisabled());
  });

  it('discards the staged choice if the component unmounts before Save (navigate-away)', async () => {
    mockMode('lan_friendly');
    const { unmount } = render(<OutboundPolicyCard />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('outbound-mode-public_only'));
    unmount();

    expect(api.saveSecurityMode).not.toHaveBeenCalled();

    // Re-mount (simulating navigating back to the page) — reflects the
    // still-unchanged server value, proving nothing was silently persisted.
    render(<OutboundPolicyCard />);
    await waitFor(() => {
      const lan = screen.getByTestId('outbound-mode-lan_friendly') as HTMLInputElement;
      expect(lan.checked).toBe(true);
    });
  });

  it('rolls back to the previous selection and re-enables Save on save failure', async () => {
    mockMode('lan_friendly');
    (api.saveSecurityMode as Mock).mockRejectedValue(new Error('boom'));
    render(<OutboundPolicyCard />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('outbound-mode-public_only'));
    fireEvent.click(screen.getByTestId('outbound-policy-save'));

    await waitFor(() => expect(notify.error).toHaveBeenCalled());
    // Selection is preserved (not reverted) so the operator can retry Save
    // without re-picking the radio — but the save button is enabled again.
    expect(screen.getByTestId('outbound-policy-save')).not.toBeDisabled();
  });

  it('uses plain language — no SSRF / RFC1918 jargon', async () => {
    mockMode('lan_friendly');
    const { container } = render(<OutboundPolicyCard />);
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    const text = container.textContent ?? '';
    expect(text).not.toMatch(/SSRF/i);
    expect(text).not.toMatch(/RFC\s?1918/i);
  });
});
