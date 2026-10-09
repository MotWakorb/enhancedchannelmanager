/**
 * AlertMethodModal (GH #1026): add a Discord / Telegram destination, and edit
 * any destination's severities and alert categories without ever holding its
 * secret.
 *
 * Contracts under test:
 *   - Create sends the typed credentials, the severity defaults (info off) and
 *     an `alert_sources` of `{}` when no category is touched.
 *   - Edit never pre-fills a secret. A blank secret box sends the masked
 *     value back, which PATCH keeps as the stored secret; config keys the form
 *     does not show survive. SMTP / ntfy edits send no `config` at all.
 *   - Category edits become the matching `alert_sources` sections.
 *   - "Send test" tests what was typed, or the saved destination when nothing
 *     was typed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AlertMethodModal } from './AlertMethodModal';

vi.mock('../../services/api', () => ({
  createAlertMethod: vi.fn(),
  updateAlertMethod: vi.fn(),
  testAlertMethod: vi.fn(),
  testDiscordWebhook: vi.fn(),
  testTelegramBot: vi.fn(),
  getEPGSources: vi.fn(),
  getM3UAccounts: vi.fn(),
}));

const mockSuccess = vi.fn();
const mockError = vi.fn();
const mockNotifications = { success: mockSuccess, error: mockError, warning: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => mockNotifications,
}));

vi.mock('../CustomSelect', () => ({
  CustomSelect: ({ id, value, onChange, options, ariaLabel }: {
    id?: string;
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
    ariaLabel?: string;
  }) => (
    <select id={id} aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  ),
}));

vi.mock('../GroupMultiSelectDropdown', () => ({
  GroupMultiSelectDropdown: ({ label, options, onChange }: {
    label: string;
    options: { id: number; name: string }[];
    onChange: (ids: number[]) => void;
  }) => (
    <button type="button" onClick={() => onChange(options.map((o) => o.id))}>
      {`Select all ${label}`}
    </button>
  ),
}));

import * as api from '../../services/api';

const WEBHOOK = 'https://discord.com/api/webhooks/1/abcdefghijklmnop';
const BOT_TOKEN = '123456789:' + 'AAEabcdefghijklmno' + 'pqrstuvwxyz012345';

function makeMethod(overrides: Partial<api.AlertMethod> = {}): api.AlertMethod {
  return {
    id: 7,
    name: 'Discord',
    method_type: 'discord',
    enabled: true,
    config: { webhook_url: '********', username: 'ECM Alerts' },
    notify_info: true,
    notify_success: true,
    notify_warning: true,
    notify_error: true,
    alert_sources: null,
    ...overrides,
  };
}

function renderModal(method: api.AlertMethod | null) {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(<AlertMethodModal method={method} onClose={onClose} onSaved={onSaved} />);
  return { onClose, onSaved };
}

describe('AlertMethodModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getEPGSources).mockResolvedValue([{ id: 11, name: 'Guide A' }, { id: 12, name: 'Guide B' }] as never);
    vi.mocked(api.getM3UAccounts).mockResolvedValue([] as never);
    vi.mocked(api.createAlertMethod).mockResolvedValue({ id: 1, name: 'x', method_type: 'discord', enabled: true });
    vi.mocked(api.updateAlertMethod).mockResolvedValue({ success: true });
  });

  describe('create', () => {
    it('adds a Discord destination with the default severities and no filters', async () => {
      const { onClose, onSaved } = renderModal(null);

      fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Errors only' } });
      fireEvent.change(screen.getByLabelText('Webhook URL'), { target: { value: WEBHOOK } });
      fireEvent.click(screen.getByRole('button', { name: 'Add destination' }));

      await waitFor(() => expect(api.createAlertMethod).toHaveBeenCalledWith({
        name: 'Errors only',
        method_type: 'discord',
        enabled: true,
        notify_error: true,
        notify_warning: true,
        notify_success: true,
        notify_info: false,
        alert_sources: {},
        config: { webhook_url: WEBHOOK, username: 'ECM Alerts' },
      }));
      expect(onSaved).toHaveBeenCalled();
      expect(onClose).toHaveBeenCalled();
    });

    it('adds a Telegram destination', async () => {
      renderModal(null);

      fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'telegram' } });
      fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Phone' } });
      fireEvent.change(screen.getByLabelText('Bot token'), { target: { value: BOT_TOKEN } });
      fireEvent.change(screen.getByLabelText('Chat ID'), { target: { value: '-1001' } });
      fireEvent.click(screen.getByRole('button', { name: 'Add destination' }));

      await waitFor(() => expect(api.createAlertMethod).toHaveBeenCalledWith(expect.objectContaining({
        method_type: 'telegram',
        config: { bot_token: BOT_TOKEN, chat_id: '-1001' },
      })));
    });

    it('refuses a webhook that is not on a Discord host', async () => {
      renderModal(null);

      fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'X' } });
      fireEvent.change(screen.getByLabelText('Webhook URL'), { target: { value: 'https://evil.example.com/api/webhooks/1/x' } });
      fireEvent.click(screen.getByRole('button', { name: 'Add destination' }));

      expect(api.createAlertMethod).not.toHaveBeenCalled();
      expect(mockError).toHaveBeenCalledWith(expect.stringMatching(/discord\.com webhook/), 'Alert Methods');
    });

    it('tests the typed webhook before it is saved', async () => {
      vi.mocked(api.testDiscordWebhook).mockResolvedValue({ success: true, message: 'Sent' });
      renderModal(null);

      fireEvent.change(screen.getByLabelText('Webhook URL'), { target: { value: WEBHOOK } });
      fireEvent.click(screen.getByRole('button', { name: /Send test/ }));

      await waitFor(() => expect(api.testDiscordWebhook).toHaveBeenCalledWith(WEBHOOK));
      expect(api.testAlertMethod).not.toHaveBeenCalled();
    });
  });

  describe('edit', () => {
    it('never pre-fills a secret and sends the mask back for a blank box', async () => {
      renderModal(makeMethod());

      expect(screen.getByRole('dialog', { name: 'Edit Discord' })).toBeInTheDocument();
      expect(screen.getByLabelText('Webhook URL')).toHaveValue('');
      expect(screen.getByLabelText('Webhook URL')).toHaveAttribute('placeholder', 'Saved — leave blank to keep');

      fireEvent.click(screen.getByLabelText('Emby'));
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

      await waitFor(() => expect(api.updateAlertMethod).toHaveBeenCalledWith(7, {
        name: 'Discord',
        enabled: true,
        notify_error: true,
        notify_warning: true,
        notify_success: true,
        notify_info: true,
        alert_sources: { version: 1, emby: { enabled: false } },
        config: { webhook_url: '********', username: 'ECM Alerts' },
      }));
    });

    it('replaces only the Telegram value that was typed and keeps unshown config keys', async () => {
      renderModal(makeMethod({
        name: 'Phone',
        method_type: 'telegram',
        config: { bot_token: '********', chat_id: '********', parse_mode: 'HTML' },
      }));

      fireEvent.change(screen.getByLabelText('Chat ID'), { target: { value: '-100999' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

      await waitFor(() => expect(api.updateAlertMethod).toHaveBeenCalledWith(7, expect.objectContaining({
        config: { bot_token: '********', chat_id: '-100999', parse_mode: 'HTML' },
      })));
    });

    it('sends no config for an email destination', async () => {
      renderModal(makeMethod({ name: 'Email', method_type: 'smtp', config: { to_emails: ['a@example.com'] } }));

      expect(screen.getByText(/Email recipients are set under Email Alert Recipients/)).toBeInTheDocument();
      fireEvent.click(screen.getByLabelText('Info'));
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

      await waitFor(() => expect(api.updateAlertMethod).toHaveBeenCalled());
      const [, payload] = vi.mocked(api.updateAlertMethod).mock.calls[0];
      expect(payload).not.toHaveProperty('config');
      expect(payload.notify_info).toBe(false);
    });

    it('turns an EPG source selection into an alert_sources filter', async () => {
      renderModal(makeMethod());

      fireEvent.change(screen.getByLabelText('Which EPG refreshes alert'), { target: { value: 'only_selected' } });
      await waitFor(() => expect(api.getEPGSources).toHaveBeenCalled());
      fireEvent.click(await screen.findByRole('button', { name: 'Select all EPG sources' }));
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

      await waitFor(() => expect(api.updateAlertMethod).toHaveBeenCalledWith(7, expect.objectContaining({
        alert_sources: { version: 1, epg_refresh: { filter_mode: 'only_selected', source_ids: [11, 12] } },
      })));
    });

    it('keeps the probe threshold a whole number of at least 1', async () => {
      renderModal(makeMethod());
      const threshold = screen.getByLabelText(/only when at least/);

      fireEvent.change(threshold, { target: { value: '2.7' } });
      expect(threshold).toHaveValue(2);
      fireEvent.change(threshold, { target: { value: '' } });
      expect(threshold).toHaveValue(1);

      fireEvent.change(threshold, { target: { value: '3' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      await waitFor(() => expect(api.updateAlertMethod).toHaveBeenCalledWith(7, expect.objectContaining({
        alert_sources: { version: 1, probe_failures: { min_failures: 3 } },
      })));
    });

    it('says so when the EPG source list cannot be loaded', async () => {
      vi.mocked(api.getEPGSources).mockRejectedValue(new Error('502 Bad Gateway'));
      renderModal(makeMethod());

      await waitFor(() => expect(mockError).toHaveBeenCalledWith(
        'Could not load EPG sources for the filters: 502 Bad Gateway',
        'Alert Methods',
      ));
    });

    it('tests the saved destination when no credential was typed', async () => {
      vi.mocked(api.testAlertMethod).mockResolvedValue({ success: true, message: 'Sent' });
      renderModal(makeMethod());

      fireEvent.click(screen.getByRole('button', { name: /Send test/ }));

      await waitFor(() => expect(api.testAlertMethod).toHaveBeenCalledWith(7));
      expect(api.testDiscordWebhook).not.toHaveBeenCalled();
    });

    it('will not test a half-typed Telegram pair', () => {
      renderModal(makeMethod({ method_type: 'telegram', config: { bot_token: '********', chat_id: '********' } }));

      fireEvent.change(screen.getByLabelText('Bot token'), { target: { value: BOT_TOKEN } });
      fireEvent.click(screen.getByRole('button', { name: /Send test/ }));

      expect(api.testTelegramBot).not.toHaveBeenCalled();
      expect(api.testAlertMethod).not.toHaveBeenCalled();
      expect(mockError).toHaveBeenCalledWith(expect.stringMatching(/Enter both/), 'Alert Methods');
    });
  });
});
