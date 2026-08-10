/**
 * Unit tests for ProtectedRoute: the first-run setup handoff.
 *
 * These cover the seam that let bead enhancedchannelmanager-lf29s through:
 * ProtectedRoute decides what to render from `authStatus`, which AuthProvider
 * fetches exactly once at mount. POST /api/auth/setup changes the server's
 * answer without persisting `setup_complete` (bead
 * enhancedchannelmanager-qg14z), so the value cached at mount is stale the
 * moment the setup wizard finishes. Nothing here had test coverage before,
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

// What the same endpoint reports once a user row exists: the backend repairs
// `setup_complete` on read (backend/auth/routes.py, GET /status).
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
    it('renders children without a login page', async () => {
      vi.mocked(api.checkSetupRequired).mockResolvedValue({ required: false });
      vi.mocked(api.getAuthStatus).mockResolvedValue({
        ...STATUS_AFTER_SETUP,
        require_auth: false,
      });
      vi.mocked(api.getCurrentUser).mockRejectedValue(new HttpError('Unauthorized', 401));

      renderProtected();

      expect(await screen.findByText(APP_CONTENT)).toBeInTheDocument();
    });
  });
});
