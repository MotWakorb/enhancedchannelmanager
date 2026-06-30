/**
 * Tests for SecurityFirstRunModal (beads nngkg, s5a3o).
 *
 * Contract (controlled, modal-once-then-Settings):
 *   - CONTROLLED: the modal renders whenever its host mounts it; it no longer
 *     self-gates on the localStorage flag (the host decides when to show it).
 *     It reports closure via the onClose prop.
 *   - DEFAULT is the home-network (LAN-friendly) choice.
 *   - Confirm persists the chosen mode, sets the seen flag, and calls onClose.
 *   - Escape / dismiss = ACCEPT the LAN-friendly default + show a toast telling
 *     the operator it can be changed in Settings + set the flag + onClose.
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

  it('renders whenever the host mounts it (controlled, no self-gating)', () => {
    render(<SecurityFirstRunModal onClose={vi.fn()} />);
    expect(screen.getByTestId('security-first-run-modal')).toBeInTheDocument();
  });

  it('persists the chosen mode, sets the seen flag, and calls onClose on confirm', async () => {
    const onClose = vi.fn();
    render(<SecurityFirstRunModal onClose={onClose} />);
    // Pick public-only, then confirm.
    fireEvent.click(screen.getByTestId('first-run-mode-public_only'));
    fireEvent.click(screen.getByTestId('security-first-run-confirm'));

    await waitFor(() => expect(api.saveSecurityMode).toHaveBeenCalledWith('public_only'));
    expect(localStorage.getItem(SECURITY_FIRST_RUN_KEY)).toBe('1');
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('defaults to the home-network (lan_friendly) choice', async () => {
    render(<SecurityFirstRunModal onClose={vi.fn()} />);
    // Without changing anything, confirming keeps LAN-friendly.
    fireEvent.click(screen.getByTestId('security-first-run-confirm'));
    await waitFor(() => expect(api.saveSecurityMode).toHaveBeenCalledWith('lan_friendly'));
  });

  it('Escape accepts the LAN-friendly default + toasts + sets flag + onClose', async () => {
    const onClose = vi.fn();
    render(<SecurityFirstRunModal onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => expect(api.saveSecurityMode).toHaveBeenCalledWith('lan_friendly'));
    expect(localStorage.getItem(SECURITY_FIRST_RUN_KEY)).toBe('1');
    expect(notify.info).toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('uses plain language — no SSRF / RFC1918 jargon', () => {
    const { container } = render(<SecurityFirstRunModal onClose={vi.fn()} />);
    const text = container.textContent ?? '';
    expect(text).not.toMatch(/SSRF/i);
    expect(text).not.toMatch(/RFC\s?1918/i);
  });
});
