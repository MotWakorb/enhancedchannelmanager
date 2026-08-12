import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { UserMenu } from './UserMenu';

const updateProfile = vi.fn();
const changePassword = vi.fn();
vi.mock('../services/api', () => ({
  updateProfile: (...args: unknown[]) => updateProfile(...args),
  changePassword: (...args: unknown[]) => changePassword(...args),
}));
vi.mock('../hooks/useAuth', () => ({
  useAuthRequired: () => true,
  useAuth: () => ({
    user: { username: 'operator', display_name: 'Operator', email: '', is_admin: true, auth_provider: 'local' },
    logout: vi.fn(),
    isLoading: false,
    refreshUser: vi.fn(),
  }),
}));
vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

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
