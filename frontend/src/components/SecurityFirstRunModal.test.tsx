/**
 * Tests for SecurityFirstRunModal (bead nngkg).
 *
 * Contract (modal-once-then-Settings):
 *   - Shows on first run only — once the operator has made (or skipped) the
 *     choice, a localStorage flag is set so it never shows again. Changing it
 *     later happens in Settings > Security.
 *   - DEFAULT is the home-network (LAN-friendly) choice.
 *   - Escape / dismiss = ACCEPT the LAN-friendly default + show a toast telling
 *     the operator it can be changed in Settings.
 *   - Plain language: no SSRF / RFC1918 jargon.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

vi.mock('../services/api', () => ({
  saveSecurityMode: vi.fn(),
}));

const notify = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() };
vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => notify,
}));

import * as api from '../services/api';
import { SecurityFirstRunModal, SECURITY_FIRST_RUN_KEY } from './SecurityFirstRunModal';

type Mock = ReturnType<typeof vi.fn>;

describe('SecurityFirstRunModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    (api.saveSecurityMode as Mock).mockResolvedValue({ ssrf_outbound_mode: 'lan_friendly' });
  });

  it('renders on first run when the flag is unset', () => {
    render(<SecurityFirstRunModal />);
    expect(screen.getByTestId('security-first-run-modal')).toBeInTheDocument();
  });

  it('does NOT render once the flag is set', () => {
    localStorage.setItem(SECURITY_FIRST_RUN_KEY, '1');
    render(<SecurityFirstRunModal />);
    expect(screen.queryByTestId('security-first-run-modal')).not.toBeInTheDocument();
  });

  it('persists the chosen mode and sets the seen flag on confirm', async () => {
    render(<SecurityFirstRunModal />);
    // Pick public-only, then confirm.
    fireEvent.click(screen.getByTestId('first-run-mode-public_only'));
    fireEvent.click(screen.getByTestId('security-first-run-confirm'));

    await waitFor(() => expect(api.saveSecurityMode).toHaveBeenCalledWith('public_only'));
    expect(localStorage.getItem(SECURITY_FIRST_RUN_KEY)).toBe('1');
    await waitFor(() =>
      expect(screen.queryByTestId('security-first-run-modal')).not.toBeInTheDocument(),
    );
  });

  it('defaults to the home-network (lan_friendly) choice', async () => {
    render(<SecurityFirstRunModal />);
    // Without changing anything, confirming keeps LAN-friendly.
    fireEvent.click(screen.getByTestId('security-first-run-confirm'));
    await waitFor(() => expect(api.saveSecurityMode).toHaveBeenCalledWith('lan_friendly'));
  });

  it('Escape accepts the LAN-friendly default + toasts where to change it', async () => {
    render(<SecurityFirstRunModal />);
    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => expect(api.saveSecurityMode).toHaveBeenCalledWith('lan_friendly'));
    expect(localStorage.getItem(SECURITY_FIRST_RUN_KEY)).toBe('1');
    expect(notify.info).toHaveBeenCalled();
  });

  it('uses plain language — no SSRF / RFC1918 jargon', () => {
    const { container } = render(<SecurityFirstRunModal />);
    const text = container.textContent ?? '';
    expect(text).not.toMatch(/SSRF/i);
    expect(text).not.toMatch(/RFC\s?1918/i);
  });
});
