/**
 * Unit tests for ProtectedRoute: the first-run setup handoff.
 *
 * These cover the seam that let bead enhancedchannelmanager-lf29s through:
 * ProtectedRoute decides what to render from `authStatus`, which AuthProvider
 * fetches exactly once at mount. POST /api/auth/setup changes the server's
 * persisted answer, so the value cached at mount is stale the moment the
 * setup wizard finishes. Nothing here had test coverage before,
 * which is why a live browser was the only place the regression showed up.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ProtectedRoute } from './ProtectedRoute';
import { AuthProvider } from '../hooks/useAuth';
import { HttpError } from '../services/httpClient';
import type { AuthStatus } from '../types';

vi.mock('../services/api', () => ({
  checkSetupRequired: vi.fn(),
  completeSetup: vi.fn(),
  getAuthStatus: vi.fn(),
  getCurrentUser: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  dispatcharrLogin: vi.fn(),
  requestPasswordReset: vi.fn(),
  resetPassword: vi.fn(),
}));

import * as api from '../services/api';

const APP_CONTENT = 'protected application content';

// What GET /api/auth/status reports on a fresh instance: auth is on, but the
// server has not recorded that setup happened yet.
const STATUS_BEFORE_SETUP: AuthStatus = {
  setup_complete: false,
  require_auth: true,
  enabled_providers: ['local'],
  primary_auth_mode: 'local',
  smtp_configured: false,
};

// What the same endpoint reports after POST /api/auth/setup persists the gate.
const STATUS_AFTER_SETUP: AuthStatus = { ...STATUS_BEFORE_SETUP, setup_complete: true };

const ADMIN_USER = {
  id: 1,
  username: 'admin',
  email: 'admin@example.com',
  display_name: null,
  is_admin: true,
  is_active: true,
  auth_provider: 'local',
  external_id: null,
};

function renderProtected(children: ReactNode = <div>{APP_CONTENT}</div>) {
  return render(
    <AuthProvider>
      <ProtectedRoute>{children}</ProtectedRoute>
    </AuthProvider>,
  );
}

/** Drive the real SetupPage form the way an operator does. */
function submitSetupForm() {
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'admin' } });
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'admin@example.com' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'Str0ng-Passw0rd!' } });
  fireEvent.change(screen.getByLabelText('Confirm Password'), {
    target: { value: 'Str0ng-Passw0rd!' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Create Admin Account' }));
}

describe('ProtectedRoute', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, '', '/');
  });

  describe('first-run setup', () => {
    beforeEach(() => {
      vi.mocked(api.checkSetupRequired).mockResolvedValue({ required: true });
      vi.mocked(api.getAuthStatus).mockResolvedValue(STATUS_BEFORE_SETUP);
      // A fresh instance has no session cookie, so /api/auth/me is a 401.
      vi.mocked(api.getCurrentUser).mockRejectedValue(new HttpError('Unauthorized', 401));
      vi.mocked(api.completeSetup).mockResolvedValue({
        user: ADMIN_USER,
        message: 'Setup complete',
      });
    });

    it('shows the setup page when no user exists', async () => {
      renderProtected();

      expect(await screen.findByRole('button', { name: 'Create Admin Account' })).toBeInTheDocument();
      expect(screen.queryByText(APP_CONTENT)).not.toBeInTheDocument();
    });

    it('re-reads auth status after setup instead of trusting the mount-time value', async () => {
      renderProtected();
      await screen.findByRole('button', { name: 'Create Admin Account' });

      vi.mocked(api.getAuthStatus).mockResolvedValue(STATUS_AFTER_SETUP);
      submitSetupForm();

      await waitFor(() => expect(api.completeSetup).toHaveBeenCalledTimes(1));
      // Once at mount, once when the setup wizard reports completion.
      await waitFor(() => expect(api.getAuthStatus).toHaveBeenCalledTimes(2));
    });

    it('sends the operator to the login page after setup, without a reload', async () => {
      renderProtected();
      await screen.findByRole('button', { name: 'Create Admin Account' });

      vi.mocked(api.getAuthStatus).mockResolvedValue(STATUS_AFTER_SETUP);
      submitSetupForm();

      expect(await screen.findByRole('button', { name: 'Sign In' })).toBeInTheDocument();
      // The regression this pins: the app used to render here with no session,
      // and its auto-opened Settings modal then called
      // /api/backup/restore-initial anonymously (bead
      // enhancedchannelmanager-lf29s).
      expect(screen.queryByText(APP_CONTENT)).not.toBeInTheDocument();
    });

    it('renders the app once the operator signs in', async () => {
      renderProtected();
      await screen.findByRole('button', { name: 'Create Admin Account' });

      vi.mocked(api.getAuthStatus).mockResolvedValue(STATUS_AFTER_SETUP);
      submitSetupForm();
      await screen.findByRole('button', { name: 'Sign In' });

      vi.mocked(api.login).mockResolvedValue({ user: ADMIN_USER, message: 'Login successful' });
      fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'admin' } });
      fireEvent.change(screen.getByLabelText('Password'), {
        target: { value: 'Str0ng-Passw0rd!' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));

      expect(await screen.findByText(APP_CONTENT)).toBeInTheDocument();
    });
  });

  describe('auth disabled', () => {
    const STATUS_AUTH_OFF = { ...STATUS_AFTER_SETUP, require_auth: false };

    beforeEach(() => {
      vi.mocked(api.checkSetupRequired).mockResolvedValue({ required: false });
      vi.mocked(api.getAuthStatus).mockResolvedValue(STATUS_AUTH_OFF);
      vi.mocked(api.getCurrentUser).mockRejectedValue(new HttpError('Unauthorized', 401));
    });

    it('renders children without a login page', async () => {
      renderProtected();

      expect(await screen.findByText(APP_CONTENT)).toBeInTheDocument();
    });

    // Bead enhancedchannelmanager-jy006 made three identity primitives
    // (initial restore, the MCP API key, TLS key material) require an
    // authenticated human admin even while require_auth is false, on an
    // instance that already holds an operator identity. The API accepts a
    // login in that mode, but ProtectedRoute used to rewrite /login to / here,
    // so those surfaces had no reachable way in.
    it('serves the login page at /login when the instance has an operator identity', async () => {
      window.history.replaceState({}, '', '/login');

      renderProtected();

      expect(await screen.findByRole('button', { name: 'Sign In' })).toBeInTheDocument();
      expect(window.location.pathname).toBe('/login');
    });

    // The carve-out: setup_complete false means there is no account to sign
    // in to, so /login would be a dead end. Keep the old rewrite there.
    it('still rewrites /login away when no operator identity exists', async () => {
      vi.mocked(api.getAuthStatus).mockResolvedValue({
        ...STATUS_AUTH_OFF,
        setup_complete: false,
      });
      window.history.replaceState({}, '', '/login');

      renderProtected();

      expect(await screen.findByText(APP_CONTENT)).toBeInTheDocument();
      expect(window.location.pathname).toBe('/');
    });
  });

  // Bead enhancedchannelmanager-p388h. Both probes used to resolve their own
  // failure to a permissive answer: useAuthRequired() returned false on a null
  // authStatus, and the setup check's catch set setupRequired=false under the
  // comment "if we can't check, assume setup is not required". Together they
  // rendered the full app shell against a backend that had answered nothing,
  // and the !authRequired branch rewrote /login to / so the operator could not
  // reach a sign-in page even by typing the URL.
  describe('backend unreachable', () => {
    const unreachable = () => new HttpError('Service Unavailable', 503);

    it('shows the cannot-reach screen instead of the app when the auth status probe fails', async () => {
      vi.mocked(api.checkSetupRequired).mockResolvedValue({ required: false });
      vi.mocked(api.getAuthStatus).mockRejectedValue(unreachable());
      vi.mocked(api.getCurrentUser).mockRejectedValue(unreachable());

      renderProtected();

      expect(await screen.findByText('Cannot reach ECM')).toBeInTheDocument();
      expect(screen.queryByText(APP_CONTENT)).not.toBeInTheDocument();
    });

    // When only the setup probe dies, auth status still carries the same fact
    // (setup_complete is the server's own answer to "does a user exist"), so
    // the app loads rather than wedging on a single flaky endpoint. The old
    // code reached the same screen here for the wrong reason: it ASSUMED
    // setup was not required rather than reading an answer.
    it('falls back to auth status when only the setup probe fails', async () => {
      vi.mocked(api.checkSetupRequired).mockRejectedValue(unreachable());
      vi.mocked(api.getAuthStatus).mockResolvedValue(STATUS_AFTER_SETUP);
      vi.mocked(api.getCurrentUser).mockResolvedValue({ user: ADMIN_USER });

      renderProtected();

      expect(await screen.findByText(APP_CONTENT)).toBeInTheDocument();
    });

    // ... and the fallback carries the OTHER answer too: a status response
    // reporting setup_complete=false still routes to the setup wizard.
    it('shows the setup page when the setup probe fails but auth status says setup is incomplete', async () => {
      vi.mocked(api.checkSetupRequired).mockRejectedValue(unreachable());
      vi.mocked(api.getAuthStatus).mockResolvedValue(STATUS_BEFORE_SETUP);
      vi.mocked(api.getCurrentUser).mockRejectedValue(new HttpError('Unauthorized', 401));

      renderProtected();

      expect(await screen.findByRole('button', { name: 'Create Admin Account' })).toBeInTheDocument();
      expect(screen.queryByText(APP_CONTENT)).not.toBeInTheDocument();
    });

    // The two probes are NOT interchangeable, which the comment on
    // `setupRequired` used to claim (bead enhancedchannelmanager-9kwzp; live
    // QA disproved it). GET /api/auth/status auto-corrects a stale
    // setup_complete=false up to true when users exist, but nothing ever flips
    // a persisted true back to false when the last user row goes away, so an
    // instance can report setup_complete=true with zero users while
    // GET /api/auth/setup-required correctly answers required=true.
    //
    // The direct probe must win outright in that state. Deriving "setup is not
    // required" from setup_complete=true instead would strand the operator on
    // a login page for an instance with no account to log into.
    it('trusts the setup probe over setup_complete when the two disagree', async () => {
      vi.mocked(api.checkSetupRequired).mockResolvedValue({ required: true });
      vi.mocked(api.getAuthStatus).mockResolvedValue(STATUS_AFTER_SETUP);
      vi.mocked(api.getCurrentUser).mockRejectedValue(new HttpError('Unauthorized', 401));

      renderProtected();

      expect(await screen.findByRole('button', { name: 'Create Admin Account' })).toBeInTheDocument();
      expect(screen.queryByText(APP_CONTENT)).not.toBeInTheDocument();
    });

    it('shows the cannot-reach screen when both probes fail', async () => {
      vi.mocked(api.checkSetupRequired).mockRejectedValue(unreachable());
      vi.mocked(api.getAuthStatus).mockRejectedValue(unreachable());
      vi.mocked(api.getCurrentUser).mockRejectedValue(unreachable());

      renderProtected();

      expect(await screen.findByText('Cannot reach ECM')).toBeInTheDocument();
      expect(screen.queryByText(APP_CONTENT)).not.toBeInTheDocument();
    });

    // The recoverability defect, pinned: the Log in link has to actually get
    // the operator to a login form.
    it('reaches the login page from the Log in link', async () => {
      vi.mocked(api.checkSetupRequired).mockResolvedValue({ required: false });
      vi.mocked(api.getAuthStatus).mockRejectedValue(unreachable());
      vi.mocked(api.getCurrentUser).mockRejectedValue(unreachable());

      renderProtected();
      fireEvent.click(await screen.findByRole('link', { name: 'Log in' }));

      expect(await screen.findByRole('button', { name: 'Sign In' })).toBeInTheDocument();
    });

    it('renders the app after Retry re-reads both probes successfully', async () => {
      vi.mocked(api.checkSetupRequired).mockRejectedValue(unreachable());
      vi.mocked(api.getAuthStatus).mockRejectedValue(unreachable());
      vi.mocked(api.getCurrentUser).mockRejectedValue(unreachable());

      renderProtected();
      await screen.findByText('Cannot reach ECM');

      vi.mocked(api.checkSetupRequired).mockResolvedValue({ required: false });
      vi.mocked(api.getAuthStatus).mockResolvedValue({
        ...STATUS_AFTER_SETUP,
        require_auth: false,
      });
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

      expect(await screen.findByText(APP_CONTENT)).toBeInTheDocument();
    });
  });
});
