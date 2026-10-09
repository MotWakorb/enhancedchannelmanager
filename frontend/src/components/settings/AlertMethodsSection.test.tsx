/**
 * Unit tests for AlertMethodsSection (enhancedchannelmanager-p4qt8).
 *
 * Contracts under test:
 *   - Lists alert methods from GET /api/alert-methods, with a routing summary,
 *     and reports the list to `onMethodsChanged` (GH #1026).
 *   - "Add" and "Edit" open AlertMethodModal (GH #1026).
 *   - "Send test" calls POST /api/alert-methods/{id}/test and surfaces the result.
 *   - "Delete" opens a type-to-confirm dialog gated on the method's name and,
 *     once confirmed, calls DELETE /api/alert-methods/{id} and removes the row.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AlertMethodsSection } from './AlertMethodsSection';

vi.mock('../../services/api', () => ({
  listAlertMethods: vi.fn(),
  createAlertMethod: vi.fn(),
  updateAlertMethod: vi.fn(),
  testAlertMethod: vi.fn(),
  deleteAlertMethod: vi.fn(),
  testDiscordWebhook: vi.fn(),
  testTelegramBot: vi.fn(),
  getEPGSources: vi.fn().mockResolvedValue([]),
  getM3UAccounts: vi.fn().mockResolvedValue([]),
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
    expect(screen.getByRole('heading', { level: 3, name: 'Alert Methods' })).toBeInTheDocument();
  });

  it('renders a create-only ntfy form with password token input', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    render(<AlertMethodsSection isAdmin />);

    expect(await screen.findByRole('heading', { name: 'Add ntfy target' })).toBeInTheDocument();
    expect(screen.getByLabelText('Name')).toBeRequired();
    expect(screen.getByLabelText('Server URL')).toBeRequired();
    expect(screen.getByLabelText('Topic')).toBeRequired();
    expect(screen.getByLabelText('Access token (optional)')).toHaveAttribute('type', 'password');
  });

  it('validates ntfy server URL and topic before create', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    render(<AlertMethodsSection isAdmin />);
    await screen.findByRole('heading', { name: 'Add ntfy target' });

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Home' } });
    fireEvent.change(screen.getByLabelText('Server URL'), { target: { value: 'ftp://host' } });
    fireEvent.change(screen.getByLabelText('Topic'), { target: { value: 'bad topic' } });
    fireEvent.submit(screen.getByRole('button', { name: 'Add ntfy target' }).closest('form')!);

    expect(api.createAlertMethod).not.toHaveBeenCalled();
    expect(mockError).toHaveBeenCalledWith(expect.stringMatching(/HTTP.*server URL/i), 'Alert Methods');
  });

  it('rejects an invalid ntfy topic before create', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    render(<AlertMethodsSection isAdmin />);
    await screen.findByRole('heading', { name: 'Add ntfy target' });

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Home' } });
    fireEvent.change(screen.getByLabelText('Server URL'), { target: { value: 'https://ntfy.example.test' } });
    fireEvent.change(screen.getByLabelText('Topic'), { target: { value: 'bad topic' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add ntfy target' }));

    expect(api.createAlertMethod).not.toHaveBeenCalled();
    expect(mockError).toHaveBeenCalledWith(expect.stringMatching(/Topic must contain 1-64/i), 'Alert Methods');
  });

  it('creates an unauthenticated ntfy target with notification defaults', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    vi.mocked(api.createAlertMethod).mockResolvedValue({ id: 3, name: 'Home', method_type: 'ntfy', enabled: true });
    render(<AlertMethodsSection isAdmin />);
    await screen.findByRole('heading', { name: 'Add ntfy target' });

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Home' } });
    fireEvent.change(screen.getByLabelText('Server URL'), { target: { value: 'http://192.168.1.20:8080/base/' } });
    fireEvent.change(screen.getByLabelText('Topic'), { target: { value: 'ECM_home-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add ntfy target' }));

    await waitFor(() => expect(api.createAlertMethod).toHaveBeenCalledWith({
      name: 'Home', method_type: 'ntfy',
      config: { server_url: 'http://192.168.1.20:8080/base/', topic: 'ECM_home-1' },
      enabled: true, notify_info: false, notify_success: true, notify_warning: true, notify_error: true,
    }));
  });

  it('includes an opaque access token, reloads, and clears form state after success', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValueOnce([]).mockResolvedValueOnce([]);
    vi.mocked(api.createAlertMethod).mockResolvedValue({ id: 3, name: 'Home', method_type: 'ntfy', enabled: true });
    render(<AlertMethodsSection isAdmin />);
    await screen.findByRole('heading', { name: 'Add ntfy target' });

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Home' } });
    fireEvent.change(screen.getByLabelText('Server URL'), { target: { value: 'https://ntfy.example.test' } });
    fireEvent.change(screen.getByLabelText('Topic'), { target: { value: 'ecm' } });
    fireEvent.change(screen.getByLabelText('Access token (optional)'), { target: { value: '<opaque-token>' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add ntfy target' }));

    await waitFor(() => expect(api.createAlertMethod).toHaveBeenCalledWith(expect.objectContaining({
      config: {
        server_url: 'https://ntfy.example.test', topic: 'ecm', access_token: '<opaque-token>',
      },
    })));
    await waitFor(() => expect(api.listAlertMethods).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText('Name')).toHaveValue('');
    expect(screen.getByLabelText('Access token (optional)')).toHaveValue('');
  });

  it('rejects an access token sent over plaintext HTTP before create', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    render(<AlertMethodsSection isAdmin />);
    await screen.findByRole('heading', { name: 'Add ntfy target' });

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Home' } });
    fireEvent.change(screen.getByLabelText('Server URL'), { target: { value: 'http://ntfy.internal:8080' } });
    fireEvent.change(screen.getByLabelText('Topic'), { target: { value: 'ecm' } });
    fireEvent.change(screen.getByLabelText('Access token (optional)'), { target: { value: '<opaque-token>' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add ntfy target' }));

    expect(api.createAlertMethod).not.toHaveBeenCalled();
    expect(mockError).toHaveBeenCalledWith(expect.stringMatching(/HTTPS/i), 'Alert Methods');
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
    // GH #1026: each row summarises what it receives.
    expect(screen.getAllByText('Errors, warnings, successes · all categories')).toHaveLength(2);
  });

  it('summarises a destination with categories switched off and a filter', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([{
      ...discordMethod,
      notify_info: true,
      alert_sources: {
        emby: { enabled: false },
        epg_refresh: { filter_mode: 'only_selected', source_ids: [3] },
      },
    }]);
    render(<AlertMethodsSection isAdmin />);

    expect(await screen.findByText('All severities · 1 category off · 1 filtered')).toBeInTheDocument();
  });

  it('reports the loaded list, and the list after a delete, to onMethodsChanged', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([smtpMethod, discordMethod]);
    vi.mocked(api.deleteAlertMethod).mockResolvedValue({ success: true });
    const onMethodsChanged = vi.fn();
    render(<AlertMethodsSection isAdmin onMethodsChanged={onMethodsChanged} />);

    await waitFor(() => expect(onMethodsChanged).toHaveBeenCalledWith([smtpMethod, discordMethod]));

    fireEvent.click(screen.getByLabelText('Delete Discord Alerts'));
    fireEvent.change(screen.getByLabelText(/type/i), { target: { value: 'Discord Alerts' } });
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(onMethodsChanged).toHaveBeenLastCalledWith([smtpMethod]));
  });

  it('opens the add-destination dialog from the header button', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([]);
    render(<AlertMethodsSection isAdmin />);
    await screen.findByText(/no alert methods configured/i);

    fireEvent.click(screen.getByRole('button', { name: /Add Discord or Telegram/ }));

    expect(await screen.findByRole('dialog', { name: 'Add alert destination' })).toBeInTheDocument();
  });

  it('opens the edit dialog for a row and reloads after saving', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([discordMethod]);
    vi.mocked(api.updateAlertMethod).mockResolvedValue({ success: true });
    render(<AlertMethodsSection isAdmin />);
    await screen.findByText('Discord Alerts');

    fireEvent.click(screen.getByLabelText('Edit Discord Alerts'));
    expect(await screen.findByRole('dialog', { name: 'Edit Discord Alerts' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() => expect(api.updateAlertMethod).toHaveBeenCalledWith(2, expect.any(Object)));
    await waitFor(() => expect(api.listAlertMethods).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole('dialog', { name: 'Edit Discord Alerts' })).not.toBeInTheDocument();
  });

  it('labels ntfy rows and preserves the generic test action', async () => {
    vi.mocked(api.listAlertMethods).mockResolvedValue([{ ...smtpMethod, id: 9, name: 'Phone', method_type: 'ntfy' }]);
    vi.mocked(api.testAlertMethod).mockResolvedValue({ success: true, message: 'Test notification sent successfully' });
    render(<AlertMethodsSection isAdmin />);

    await screen.findByText('Phone');
    expect(screen.getByText('ntfy')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Send test to Phone'));
    await waitFor(() => expect(api.testAlertMethod).toHaveBeenCalledWith(9));
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

    expect(screen.getByRole('heading', { level: 3, name: 'Alert Methods' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add Discord or Telegram/ })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Only administrators can view or manage alert methods\./),
    ).toBeInTheDocument();
    expect(api.listAlertMethods).not.toHaveBeenCalled();
    expect(screen.queryByRole('heading', { name: 'Add ntfy target' })).not.toBeInTheDocument();
    expect(screen.queryByText('Discord Alerts')).not.toBeInTheDocument();
  });
});
