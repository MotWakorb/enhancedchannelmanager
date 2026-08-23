import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../services/api';
import { TLSSettingsSection } from './TLSSettingsSection';

vi.mock('../../services/api', () => ({
  getTLSStatus: vi.fn(),
  getTLSSettings: vi.fn(),
  configureTLS: vi.fn(),
}));

const notifications = {
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(),
};

vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => notifications,
}));

const status = {
  enabled: true,
  mode: 'manual' as const,
  domain: 'ecm.example.test',
  https_port: 6143,
  cert_issued_at: null,
  cert_expires_at: null,
  cert_subject: null,
  cert_issuer: null,
  days_until_expiry: null,
  auto_renew: true,
  last_renewal_attempt: null,
  last_renewal_error: null,
  has_certificate: true,
  certificate_valid: true,
  https_server_running: true,
  allow_http_session_cookies: false,
  http_session_cookies_env_override: false,
  session_cookies_plaintext: false,
};

const settings = {
  enabled: true,
  mode: 'manual' as const,
  domain: 'ecm.example.test',
  https_port: 6143,
  acme_email: '',
  use_staging: false,
  dns_provider: '',
  dns_api_token: '',
  dns_zone_id: '',
  aws_access_key_id: '',
  aws_secret_access_key: '',
  aws_region: 'us-east-1',
  auto_renew: true,
  renew_days_before_expiry: 30,
  allow_http_session_cookies: false,
};

describe('TLS session transport recovery control', () => {
  beforeEach(() => {
    vi.mocked(api.getTLSStatus).mockResolvedValue(status);
    vi.mocked(api.getTLSSettings).mockResolvedValue(settings);
    vi.mocked(api.configureTLS).mockResolvedValue({ success: true, message: 'saved' });
  });

  it('labels HTTP as unauthenticated and makes the plaintext risk explicit', async () => {
    render(<TLSSettingsSection isAdmin />);

    expect(await screen.findByText(/HTTP remains available without authenticated sessions/i)).toBeInTheDocument();
    expect(screen.getByText(/another device on the network may steal them/i)).toBeInTheDocument();
  });

  it('sends the explicit break-glass choice when settings are saved', async () => {
    render(<TLSSettingsSection isAdmin />);
    const checkbox = await screen.findByRole('checkbox', {
      name: /Emergency recovery: allow authenticated sessions over HTTP/i,
    });
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => expect(api.configureTLS).toHaveBeenCalledWith(
      expect.objectContaining({ allow_http_session_cookies: true }),
    ));
  });

  it('shows no break-glass banner while sessions are protected', async () => {
    render(<TLSSettingsSection isAdmin />);

    await screen.findByText(/HTTP remains available without authenticated sessions/i);
    expect(screen.queryByText(/Session cookies are being sent over plaintext HTTP/i)).toBeNull();
  });

  it('warns that sessions travel in plaintext when the stored hatch is open', async () => {
    vi.mocked(api.getTLSStatus).mockResolvedValue({
      ...status,
      allow_http_session_cookies: true,
      session_cookies_plaintext: true,
    });

    render(<TLSSettingsSection isAdmin />);

    expect(
      await screen.findByText(/Session cookies are being sent over plaintext HTTP/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/via the setting below/i)).toBeInTheDocument();
  });

  it('names the environment variable when that is what is holding the hatch open', async () => {
    // The checkbox renders from tls_settings.json and stays UNCHECKED here, so
    // without this banner the panel actively contradicts the live policy: an
    // operator who recovered with ECM_ALLOW_HTTP_SESSION_COOKIES and forgot the
    // line saw an unchecked box and an "Encrypted" badge indefinitely.
    vi.mocked(api.getTLSStatus).mockResolvedValue({
      ...status,
      http_session_cookies_env_override: true,
      session_cookies_plaintext: true,
    });

    render(<TLSSettingsSection isAdmin />);

    expect(
      await screen.findByText(/Session cookies are being sent over plaintext HTTP/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/ECM_ALLOW_HTTP_SESSION_COOKIES environment variable/i)).toBeInTheDocument();

    const checkbox = screen.getByRole('checkbox', {
      name: /Emergency recovery: allow authenticated sessions over HTTP/i,
    }) as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
  });
});
