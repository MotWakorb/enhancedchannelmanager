/**
 * The operator must SEE why a plaintext sign-in was refused.
 *
 * Bead enhancedchannelmanager-04c0u.9 remediation. `/api/auth/login` used to
 * answer 200 over plain HTTP with ECM terminating TLS: the password was
 * verified, a session row was created, and only then did the browser silently
 * discard the `Secure` cookie (RFC 6265bis 5.6). This component resolved on the
 * 200, fired `onLoginSuccess`, the first API call 401'd, and the operator was
 * bounced back here with `setError` never called — so the natural response was
 * to retry and ship the cleartext password again.
 *
 * The backend now answers 403 with a message naming the HTTPS URL and the
 * break-glass options. Nothing pinned that a rejected sign-in surfaces rather
 * than bouncing, so this does.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { LoginPage } from './LoginPage';
import { HttpError } from '../services/httpClient';

const login = vi.fn();
const loginWithDispatcharr = vi.fn();

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    login,
    loginWithDispatcharr,
    authStatus: { enabled_providers: ['local'] },
    isLoading: false,
  }),
}));

const REFUSAL =
  'This instance terminates TLS, so ECM will not start a browser session over ' +
  'plaintext HTTP. Sign in at https://ecm.example.test:6143 instead. If HTTPS is ' +
  "unreachable, an admin can enable 'Emergency recovery: allow authenticated " +
  "sessions over HTTP' in TLS Settings, or set " +
  'ECM_ALLOW_HTTP_SESSION_COOKIES=true on the container and restart ECM.';

async function submit() {
  fireEvent.change(screen.getByLabelText(/username/i), { target: { value: 'admin' } });
  fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'pw' } });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));
  });
}

describe('LoginPage plaintext refusal', () => {
  beforeEach(() => {
    login.mockReset();
    loginWithDispatcharr.mockReset();
  });

  it('renders the refusal message instead of bouncing to the app shell', async () => {
    login.mockRejectedValue(new HttpError(REFUSAL, 403));
    const onLoginSuccess = vi.fn();

    render(<LoginPage onLoginSuccess={onLoginSuccess} />);
    await submit();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/will not start a browser session over plaintext HTTP/i);
    // The two recovery routes must both survive into the rendered text — this
    // is the only place a locked-out operator can read them.
    expect(alert).toHaveTextContent('https://ecm.example.test:6143');
    expect(alert).toHaveTextContent('ECM_ALLOW_HTTP_SESSION_COOKIES');
    expect(onLoginSuccess).not.toHaveBeenCalled();
  });

  it('still signs in when the transport is acceptable', async () => {
    login.mockResolvedValue(undefined);
    const onLoginSuccess = vi.fn();

    render(<LoginPage onLoginSuccess={onLoginSuccess} />);
    await submit();

    await waitFor(() => expect(onLoginSuccess).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
