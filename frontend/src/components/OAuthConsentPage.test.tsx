/**
 * Unit tests for OAuthConsentPage (bead buiqr.7).
 *
 * Pinned behavior:
 *  - The client NAME is rendered from the server-fetched consent-context
 *    (registry-pinned), NOT from the client_id query parameter (CP1).
 *  - The PO-locked permission summary copy is shown verbatim.
 *  - "See full access list" toggles the tool-category grid.
 *  - The approve form carries the CSRF `state` to /api/oauth/authorize/approve
 *    (buiqr.4 AC6 — UI round-trips state).
 *  - Returning-user state offers Keep vs Re-authorize.
 *  - Cancel mints no token (navigates away without POSTing).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { OAuthConsentPage } from './OAuthConsentPage';

vi.mock('../services/api', () => ({
  getConsentContext: vi.fn(),
  revokeOAuthGrant: vi.fn(),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({ user: { is_admin: true, username: 'admin' }, isLoading: false }),
}));

import * as api from '../services/api';

function setLocation(search: string) {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: {
      ...window.location,
      search,
      assign: vi.fn(),
    },
  });
}

const VALID_SEARCH =
  '?client_id=claude-desktop&redirect_uri=https://claude.ai/cb&code_challenge=abc' +
  '&code_challenge_method=S256&scope=mcp&state=csrf-state-123';

const firstTimeContext = {
  client_name: 'Claude Desktop',
  client_id: 'claude-desktop',
  scope: 'mcp',
  already_connected: false,
  existing_grant: null,
  return_to: '/?tab=settings',
};

describe('OAuthConsentPage — first-time consent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setLocation(VALID_SEARCH);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the registry-pinned client name from consent-context', async () => {
    vi.mocked(api.getConsentContext).mockResolvedValue(firstTimeContext);

    render(<OAuthConsentPage />);

    await waitFor(() => {
      expect(screen.getByTestId('oauth-consent-card')).toBeInTheDocument();
    });
    // The name comes from the fetched context (Claude Desktop), and the API was
    // called with the client_id only — the page does not render the raw query.
    expect(api.getConsentContext).toHaveBeenCalledWith('claude-desktop');
    expect(screen.getByRole('heading', { name: /Authorize Claude Desktop/i })).toBeInTheDocument();
  });

  it('shows the PO-locked permission summary copy', async () => {
    vi.mocked(api.getConsentContext).mockResolvedValue(firstTimeContext);

    render(<OAuthConsentPage />);

    await waitFor(() => {
      expect(screen.getByTestId('oauth-consent-summary')).toBeInTheDocument();
    });
    expect(screen.getByTestId('oauth-consent-summary')).toHaveTextContent(
      /will be able to read and manage your ECM channels, streams, M3U accounts, and EPG sources\./i,
    );
  });

  it('toggles the full access list (tool grid)', async () => {
    vi.mocked(api.getConsentContext).mockResolvedValue(firstTimeContext);

    render(<OAuthConsentPage />);
    await waitFor(() => screen.getByTestId('oauth-consent-disclosure'));

    expect(screen.queryByTestId('oauth-consent-access-list')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('oauth-consent-disclosure'));
    expect(screen.getByTestId('oauth-consent-access-list')).toBeInTheDocument();
    // The grid reuses the tool categories (Channels is one of them).
    expect(screen.getByText('Channels')).toBeInTheDocument();
  });

  it('approve form carries the CSRF state to the approve endpoint', async () => {
    vi.mocked(api.getConsentContext).mockResolvedValue(firstTimeContext);

    render(<OAuthConsentPage />);
    await waitFor(() => screen.getByTestId('oauth-consent-card'));

    const form = document.getElementById('oauth-approve-form') as HTMLFormElement;
    expect(form).toBeTruthy();
    expect(form.getAttribute('action')).toBe('/api/oauth/authorize/approve');
    expect(form.getAttribute('method')).toBe('POST');
    const stateInput = form.querySelector('input[name="state"]') as HTMLInputElement;
    expect(stateInput.value).toBe('csrf-state-123');
    const clientInput = form.querySelector('input[name="client_id"]') as HTMLInputElement;
    expect(clientInput.value).toBe('claude-desktop');
  });

  it('Authorize submits the approve form (mints a code server-side)', async () => {
    vi.mocked(api.getConsentContext).mockResolvedValue(firstTimeContext);

    render(<OAuthConsentPage />);
    await waitFor(() => screen.getByTestId('oauth-consent-approve'));

    const form = document.getElementById('oauth-approve-form') as HTMLFormElement;
    const submitSpy = vi.spyOn(form, 'requestSubmit').mockImplementation(() => {});
    fireEvent.click(screen.getByTestId('oauth-consent-approve'));
    expect(submitSpy).toHaveBeenCalled();
  });

  it('Cancel mints no token — navigates to the guarded return_to without submitting', async () => {
    vi.mocked(api.getConsentContext).mockResolvedValue(firstTimeContext);

    render(<OAuthConsentPage />);
    await waitFor(() => screen.getByTestId('oauth-consent-cancel'));

    const form = document.getElementById('oauth-approve-form') as HTMLFormElement;
    const submitSpy = vi.spyOn(form, 'requestSubmit').mockImplementation(() => {});
    fireEvent.click(screen.getByTestId('oauth-consent-cancel'));
    expect(submitSpy).not.toHaveBeenCalled();
    expect(window.location.assign).toHaveBeenCalledWith('/?tab=settings');
  });
});

describe('OAuthConsentPage — returning user (already connected)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setLocation(VALID_SEARCH);
  });

  const connectedContext = {
    client_name: 'Claude Desktop',
    client_id: 'claude-desktop',
    scope: 'mcp',
    already_connected: true,
    existing_grant: {
      id: 'fam-1',
      client_id: 'claude-desktop',
      client_name: 'Claude Desktop',
      granted_at: 1_700_000_000,
      last_used: 1_700_100_000,
    },
    return_to: '/?tab=settings',
  };

  it('renders the already-connected state with both choices', async () => {
    vi.mocked(api.getConsentContext).mockResolvedValue(connectedContext);

    render(<OAuthConsentPage />);

    await waitFor(() => {
      expect(screen.getByTestId('oauth-consent-already-connected')).toBeInTheDocument();
    });
    expect(screen.getByTestId('oauth-keep-existing')).toBeInTheDocument();
    expect(screen.getByTestId('oauth-reauthorize')).toBeInTheDocument();
  });

  it('Keep existing mints no token (no revoke, no approve submit)', async () => {
    vi.mocked(api.getConsentContext).mockResolvedValue(connectedContext);

    render(<OAuthConsentPage />);
    await waitFor(() => screen.getByTestId('oauth-keep-existing'));

    fireEvent.click(screen.getByTestId('oauth-keep-existing'));
    expect(api.revokeOAuthGrant).not.toHaveBeenCalled();
    expect(window.location.assign).toHaveBeenCalledWith('/?tab=settings');
  });

  it('Re-authorize revokes the old grant then submits the approve form', async () => {
    vi.mocked(api.getConsentContext).mockResolvedValue(connectedContext);
    vi.mocked(api.revokeOAuthGrant).mockResolvedValue(undefined);

    render(<OAuthConsentPage />);
    await waitFor(() => screen.getByTestId('oauth-reauthorize'));

    const form = document.getElementById('oauth-approve-form') as HTMLFormElement;
    const submitSpy = vi.spyOn(form, 'requestSubmit').mockImplementation(() => {});

    fireEvent.click(screen.getByTestId('oauth-reauthorize'));

    await waitFor(() => {
      expect(api.revokeOAuthGrant).toHaveBeenCalledWith('fam-1');
    });
    await waitFor(() => {
      expect(submitSpy).toHaveBeenCalled();
    });
  });
});

describe('OAuthConsentPage — error + gating states', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows an error when the consent-context fetch fails (unknown client)', async () => {
    setLocation(VALID_SEARCH);
    vi.mocked(api.getConsentContext).mockRejectedValue(new Error('invalid_client'));

    render(<OAuthConsentPage />);

    await waitFor(() => {
      expect(screen.getByTestId('oauth-consent-error')).toBeInTheDocument();
    });
    expect(api.revokeOAuthGrant).not.toHaveBeenCalled();
  });

  it('shows an error when there is no client_id in the URL', async () => {
    setLocation('');

    render(<OAuthConsentPage />);

    await waitFor(() => {
      expect(screen.getByTestId('oauth-consent-error')).toBeInTheDocument();
    });
    // No request is made for a missing client_id.
    expect(api.getConsentContext).not.toHaveBeenCalled();
  });
});
