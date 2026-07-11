/**
 * Tests for LinkedAccountsSection (bead f9rlc).
 *
 * Contract:
 *   - The "Link Another Account" grid never offers an OIDC option. The
 *     redirect target /api/auth/identities/link/oidc/authorize does not
 *     exist in backend/auth/ (verified: zero matches), so the button used
 *     to dead-end. get_enabled_providers() (backend/auth/settings.py) can
 *     also never return 'oidc' today — but the test forces it into
 *     `enabled_providers` anyway to prove the frontend itself will never
 *     offer the broken flow even if that backend gating changes later.
 *   - Already-linked identities of any provider (including a legacy 'oidc'
 *     row) still render correctly in the identity list — this bead only
 *     removes the dead "link" entry point, not display of existing links.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import type { AuthStatus, LinkedIdentitiesResponse } from '../../types';

vi.mock('../../services/api', () => ({
  getLinkedIdentities: vi.fn(),
  getAuthStatus: vi.fn(),
}));

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
  }),
}));

import * as api from '../../services/api';
import { LinkedAccountsSection } from './LinkedAccountsSection';

type Mock = ReturnType<typeof vi.fn>;

function authStatus(overrides: Partial<AuthStatus> = {}): AuthStatus {
  return {
    setup_complete: true,
    require_auth: true,
    enabled_providers: ['local', 'dispatcharr'],
    primary_auth_mode: 'local',
    smtp_configured: false,
    ...overrides,
  };
}

function identities(list: LinkedIdentitiesResponse['identities'] = []): LinkedIdentitiesResponse {
  return { identities: list };
}

describe('LinkedAccountsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('never renders a Link OpenID Connect button, even when the backend reports oidc as enabled', async () => {
    (api.getLinkedIdentities as Mock).mockResolvedValue(identities([]));
    (api.getAuthStatus as Mock).mockResolvedValue(
      authStatus({ enabled_providers: ['local', 'dispatcharr', 'oidc'] })
    );

    render(<LinkedAccountsSection />);

    await waitFor(() => {
      expect(screen.getByText('Link Another Account')).toBeInTheDocument();
    });

    expect(screen.queryByText(/Link OpenID Connect/i)).not.toBeInTheDocument();
    // The other providers still offer their (working, modal-based) link flow.
    expect(screen.getByText(/Link Local/i)).toBeInTheDocument();
    expect(screen.getByText(/Link Dispatcharr/i)).toBeInTheDocument();
  });

  it('clicking Link Local never navigates the page (no dead redirect wiring survives on non-oidc providers)', async () => {
    (api.getLinkedIdentities as Mock).mockResolvedValue(identities([]));
    (api.getAuthStatus as Mock).mockResolvedValue(authStatus());

    render(<LinkedAccountsSection />);

    await waitFor(() => {
      expect(screen.getByText(/Link Local/i)).toBeInTheDocument();
    });

    const originalHref = window.location.href;
    fireEvent.click(screen.getByText(/Link Local/i).closest('button')!);

    // Opens the credential modal in place — no navigation occurred.
    expect(window.location.href).toBe(originalHref);
    await waitFor(() => {
      expect(
        screen.getByText(/Enter your Local credentials to link this account\./i)
      ).toBeInTheDocument();
    });
  });

  it('still displays an already-linked OIDC identity in the identity list', async () => {
    (api.getLinkedIdentities as Mock).mockResolvedValue(
      identities([
        {
          id: 1,
          user_id: 1,
          provider: 'local',
          external_id: null,
          identifier: 'admin',
          linked_at: '2026-01-01T00:00:00Z',
          last_used_at: null,
        },
        {
          id: 2,
          user_id: 1,
          provider: 'oidc',
          external_id: 'sub-123',
          identifier: 'admin@example.com',
          linked_at: '2026-01-01T00:00:00Z',
          last_used_at: null,
        },
      ])
    );
    (api.getAuthStatus as Mock).mockResolvedValue(authStatus());

    render(<LinkedAccountsSection />);

    await waitFor(() => {
      expect(screen.getByText('admin@example.com')).toBeInTheDocument();
    });
    expect(screen.getByText('OpenID Connect')).toBeInTheDocument();
  });
});
