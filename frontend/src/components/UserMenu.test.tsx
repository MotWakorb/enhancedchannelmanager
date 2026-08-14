import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { UserMenu } from './UserMenu';

const updateProfile = vi.fn();
const changePassword = vi.fn();
vi.mock('../services/api', () => ({
  updateProfile: (...args: unknown[]) => updateProfile(...args),
  changePassword: (...args: unknown[]) => changePassword(...args),
}));
// Mutable so the sign-in tests below can drive the no-session states. Hoisted
// because vi.mock factories run before module-scope initialisers.
const authState = vi.hoisted(() => ({
  current: {
    user: { username: 'operator', display_name: 'Operator', email: '', is_admin: true, auth_provider: 'local' },
    authStatus: { setup_complete: true, require_auth: false },
    logout: vi.fn(),
    isLoading: false,
    refreshUser: vi.fn(),
  } as Record<string, unknown>,
}));
const SIGNED_IN = { ...authState.current };
vi.mock('../hooks/useAuth', () => ({
  useAuth: () => authState.current,
}));

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

beforeEach(() => {
  authState.current = { ...SIGNED_IN };
  window.history.replaceState({}, '', '/');
});

describe('UserMenu dialog states', () => {
  it('gives each state a unique resolved name and restores focus after Escape', async () => {
    const user = userEvent.setup();
    render(<UserMenu />);
    const trigger = screen.getByRole('button', { name: /Operator/ });

    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: /Edit Profile$/ }));
    const profile = screen.getByRole('dialog', { name: 'Edit Profile' });
    const profileId = profile.getAttribute('aria-labelledby');
    expect(document.getElementById(profileId!)).toHaveTextContent('Edit Profile');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(trigger).toHaveFocus());

    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: /Change Password$/ }));
    const password = screen.getByRole('dialog', { name: 'Change Password' });
    const passwordId = password.getAttribute('aria-labelledby');
    expect(document.getElementById(passwordId!)).toHaveTextContent('Change Password');
    expect(passwordId).not.toBe(profileId);
  });

  it('does not dismiss a profile save that is still pending', async () => {
    const user = userEvent.setup();
    updateProfile.mockReturnValue(new Promise(() => {}));
    render(<UserMenu />);
    await user.click(screen.getByRole('button', { name: /Operator/ }));
    await user.click(screen.getByRole('button', { name: /Edit Profile$/ }));
    await user.click(screen.getByRole('button', { name: 'Save Changes' }));
    expect(await screen.findByRole('button', { name: 'Saving...' })).toBeDisabled();

    expect(screen.getByRole('button', { name: 'Close profile dialog' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();

    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog', { name: 'Edit Profile' })).toBeInTheDocument();
  });

  it('does not dismiss a password change that is still pending', async () => {
    const user = userEvent.setup();
    changePassword.mockReturnValue(new Promise(() => {}));
    render(<UserMenu />);
    await user.click(screen.getByRole('button', { name: /Operator/ }));
    await user.click(screen.getByRole('button', { name: /Change Password$/ }));
    const currentPassword = screen.getByLabelText('Current Password');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close password dialog' })).toHaveFocus());
    await user.click(currentPassword);
    await user.type(currentPassword, 'synthetic-old');
    await user.type(screen.getByLabelText('New Password'), 'synthetic-new');
    await user.type(screen.getByLabelText('Confirm New Password'), 'synthetic-new');
    await user.click(screen.getByRole('button', { name: 'Change Password' }));

    expect(await screen.findByRole('button', { name: 'Changing...' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Close password dialog' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog', { name: 'Change Password' })).toBeInTheDocument();
  });
});

// The app shell's only sign-in affordance (bead enhancedchannelmanager-9kwzp).
//
// Live QA on an auth-disabled instance holding an operator identity found the
// shell offered no way in at all: this component returned null with no user,
// nothing else in the header links to /login, and ProtectedRoute's auth-off
// branch only serves the login page for a path the operator types by hand. The
// route bead enhancedchannelmanager-p388h reopened, so that jy006's three
// gated surfaces could be reached, was effectively unreachable.
describe('UserMenu sign-in affordance', () => {
  it('offers Sign in when there is no session but the instance has an operator identity', async () => {
    const user = userEvent.setup();
    authState.current = {
      ...SIGNED_IN,
      user: null,
      authStatus: { setup_complete: true, require_auth: false },
    };

    render(<UserMenu />);
    const signIn = screen.getByRole('button', { name: /Sign in/i });

    await user.click(signIn);
    expect(window.location.pathname).toBe('/login');
  });

  // Without an operator row the button would lead to a login form nobody can
  // satisfy, so it must not appear at all.
  it('renders nothing when no operator identity exists to sign in as', () => {
    authState.current = {
      ...SIGNED_IN,
      user: null,
      authStatus: { setup_complete: false, require_auth: false },
    };

    const { container } = render(<UserMenu />);
    expect(container).toBeEmptyDOMElement();
  });

  // A null authStatus means the probe has not answered. Guessing "there is an
  // account" here would be the same fail-open shape bead p388h removed.
  it('renders nothing while the auth status is unresolved', () => {
    authState.current = { ...SIGNED_IN, user: null, authStatus: null };

    const { container } = render(<UserMenu />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing while the initial auth check is still in flight', () => {
    authState.current = { ...SIGNED_IN, user: null, isLoading: true };

    const { container } = render(<UserMenu />);
    expect(container).toBeEmptyDOMElement();
  });

  // The signed-in menu must not gain a second, contradictory entry point.
  it('shows the account menu and no Sign in button once a session exists', () => {
    render(<UserMenu />);

    expect(screen.getByRole('button', { name: /Operator/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Sign in/i })).not.toBeInTheDocument();
  });
});
