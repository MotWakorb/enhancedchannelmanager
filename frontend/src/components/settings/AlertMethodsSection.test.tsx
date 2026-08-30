/**
 * Unit tests for AlertMethodsSection (enhancedchannelmanager-p4qt8).
 *
 * Contracts under test:
 *   - Lists alert methods from GET /api/alert-methods.
 *   - "Send test" calls POST /api/alert-methods/{id}/test and surfaces the result.
 *   - "Delete" opens a type-to-confirm dialog gated on the method's name and,
 *     once confirmed, calls DELETE /api/alert-methods/{id} and removes the row.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AlertMethodsSection } from './AlertMethodsSection';

vi.mock('../../services/api', () => ({
  listAlertMethods: vi.fn(),
  testAlertMethod: vi.fn(),
  deleteAlertMethod: vi.fn(),
}));

const mockSuccess = vi.fn();
const mockError = vi.fn();
// Stable object identity, matching the real NotificationContext's useMemo
// (see contexts/NotificationContext.tsx) — a fresh object literal per call
// would make any consumer's useCallback([...,notifications]) unstable and
// re-fire its effect on every render.
const mockNotifications = {
  success: mockSuccess,
  error: mockError,
  warning: vi.fn(),
  info: vi.fn(),
};
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => mockNotifications,
}));

import * as api from '../../services/api';

const smtpMethod: api.AlertMethod = {
  id: 1,
  name: 'Email',
  method_type: 'smtp',
  enabled: true,
  config: {},
  notify_info: false,
  notify_success: true,
  notify_warning: true,
  notify_error: true,
};

const discordMethod: api.AlertMethod = {
  id: 2,
  name: 'Discord Alerts',
  method_type: 'discord',
  enabled: false,
  config: {},
  notify_info: false,
  notify_success: true,
  notify_warning: true,
  notify_error: true,
};

describe('AlertMethodsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders an empty state when no alert methods exist', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    render(<AlertMethodsSection isAdmin />);

    await waitFor(() => {
      expect(screen.getByText(/no alert methods configured/i)).toBeInTheDocument();
    });
  });

  it('lists alert methods with type and enabled state', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([smtpMethod, discordMethod]);
    render(<AlertMethodsSection isAdmin />);

    await waitFor(() => {
      expect(screen.getByText('Email')).toBeInTheDocument();
      expect(screen.getByText('Discord Alerts')).toBeInTheDocument();
    });
    expect(screen.getByText('Email (SMTP)')).toBeInTheDocument();
    expect(screen.getByText('Discord')).toBeInTheDocument();
    expect(screen.getByText('Enabled')).toBeInTheDocument();
    expect(screen.getByText('Disabled')).toBeInTheDocument();
  });

  it('sends a test message for a row and shows the result', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([smtpMethod]);
    vi.mocked(api.testAlertMethod).mockResolvedValue({ success: true, message: 'Test email sent' });

    render(<AlertMethodsSection isAdmin />);
    await waitFor(() => screen.getByText('Email'));

    fireEvent.click(screen.getByLabelText('Send test to Email'));

    await waitFor(() => {
      expect(api.testAlertMethod).toHaveBeenCalledWith(1);
      expect(mockSuccess).toHaveBeenCalledWith('Test email sent', 'Alert Methods');
    });
  });

  it('shows an error notification when the test fails', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([smtpMethod]);
    vi.mocked(api.testAlertMethod).mockResolvedValue({ success: false, message: 'SMTP auth failed' });

    render(<AlertMethodsSection isAdmin />);
    await waitFor(() => screen.getByText('Email'));

    fireEvent.click(screen.getByLabelText('Send test to Email'));

    await waitFor(() => {
      expect(mockError).toHaveBeenCalledWith('SMTP auth failed', 'Alert Methods');
    });
  });

  it('deletes a method only after typing its exact name to confirm', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([discordMethod]);
    vi.mocked(api.deleteAlertMethod).mockResolvedValue({ success: true });

    render(<AlertMethodsSection isAdmin />);
    await waitFor(() => screen.getByText('Discord Alerts'));

    fireEvent.click(screen.getByLabelText('Delete Discord Alerts'));

    const confirmBtn = screen.getByRole('button', { name: 'Delete' });
    expect(confirmBtn).toBeDisabled();
    expect(api.deleteAlertMethod).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(/type/i), { target: { value: 'Discord Alerts' } });
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.deleteAlertMethod).toHaveBeenCalledWith(2);
    });
    await waitFor(() => {
      expect(screen.queryByText('Discord Alerts')).not.toBeInTheDocument();
    });
  });

  it('cancelling delete does not call the API', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([discordMethod]);

    render(<AlertMethodsSection isAdmin />);
    await waitFor(() => screen.getByText('Discord Alerts'));

    fireEvent.click(screen.getByLabelText('Delete Discord Alerts'));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(api.deleteAlertMethod).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.getByText('Discord Alerts')).toBeInTheDocument();
    });
  });

  // bead enhancedchannelmanager-9kwzp.10 item 4: every backing endpoint is now
  // admin-gated on the backend, because an alert method's `config` holds the
  // Discord webhook URL, the Telegram bot token and the SMTP password. The
  // component must not issue the request it would be refused, so a non-admin
  // gets the lock notice instead of a 403 toast.
  it('shows the admin-only notice and issues no request for a non-admin', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([discordMethod]);

    render(<AlertMethodsSection isAdmin={false} />);

    expect(
      screen.getByText(/Only administrators can view or manage alert methods\./),
    ).toBeInTheDocument();
    expect(api.listAlertMethods).not.toHaveBeenCalled();
    expect(screen.queryByText('Discord Alerts')).not.toBeInTheDocument();
  });
});
