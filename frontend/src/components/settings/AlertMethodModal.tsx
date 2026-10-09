import { useEffect, useState } from 'react';
import * as api from '../../services/api';
import type { AlertSourceFilterMode } from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import { ModalOverlay } from '../ModalOverlay';
import { useOwnedDialog } from '../../hooks/useOwnedDialog';
import { CustomSelect } from '../CustomSelect';
import { GroupMultiSelectDropdown, type GroupMultiSelectOption } from '../GroupMultiSelectDropdown';
import {
  ALERT_CATEGORIES,
  SEVERITIES,
  alertSourcesFromDraft,
  draftFromAlertSources,
  type AlertSourcesDraft,
  type CategoryDraft,
  type SeverityFlag,
} from '../../utils/alertSources';
import '../ModalBase.css';
import './AlertMethodModal.css';

/**
 * Add or edit one alert destination (GH #1026).
 *
 * Creates Discord and Telegram destinations, and edits any alert method's
 * name, enabled state, severities and alert categories. Email recipients and
 * ntfy targets are created by their own forms; their connection settings are
 * not edited here.
 *
 * Secrets are never shown. GET /api/alert-methods masks the Discord webhook
 * URL and the Telegram bot token and chat id to "********", so an edit starts
 * with those boxes empty. The masked value is sent back for any box left
 * empty, and PATCH keeps the stored secret for a blank or masked Discord /
 * Telegram value (routers/alert_methods.py ``_PRESERVED_ON_UPDATE``).
 */

// Types this dialog can create. Email rows come from Email Alert Recipients
// and ntfy rows from the ntfy form in AlertMethodsSection.
const TYPE_OPTIONS = [
  { value: 'discord', label: 'Discord webhook' },
  { value: 'telegram', label: 'Telegram bot' },
];

const FILTER_MODE_OPTIONS = [
  { value: 'all', label: 'All' },
  { value: 'only_selected', label: 'Only the selected' },
  { value: 'all_except', label: 'All except the selected' },
];

/** Picker wording for the two categories filtered by entity. */
const ENTITY_PICKER_TEXT: Record<string, {
  label: string; placeholder: string; empty: string; singular: string; plural: string;
}> = {
  epg_refresh: {
    label: 'EPG sources', placeholder: 'Select EPG sources', empty: 'No EPG sources',
    singular: 'source', plural: 'sources',
  },
  m3u_refresh: {
    label: 'M3U accounts', placeholder: 'Select M3U accounts', empty: 'No M3U accounts',
    singular: 'account', plural: 'accounts',
  },
};

// Client-side copies of backend notification_validation, for an immediate
// message. The backend re-checks on save.
const DISCORD_WEBHOOK_PATTERN = /^https:\/\/(discord\.com|discordapp\.com|canary\.discord\.com|ptb\.discord\.com)\/api\/webhooks\//;
const TELEGRAM_TOKEN_PATTERN = /^\d+:[A-Za-z0-9_-]+$/;

const MASKED = '********';
const KEEP_PLACEHOLDER = 'Saved — leave blank to keep';

interface AlertMethodModalProps {
  /** The method to edit, or null to create a Discord / Telegram destination. */
  method: api.AlertMethod | null;
  onClose: () => void;
  onSaved: () => void;
}

export function AlertMethodModal({ method, onClose, onSaved }: AlertMethodModalProps) {
  const notifications = useNotifications();
  const { titleId, containerRef } = useOwnedDialog();
  const isEditing = method !== null;

  const [name, setName] = useState(method?.name ?? '');
  const [methodType, setMethodType] = useState<string>(method?.method_type ?? 'discord');
  const [enabled, setEnabled] = useState(method?.enabled ?? true);
  const [severities, setSeverities] = useState<Record<SeverityFlag, boolean>>({
    notify_error: method?.notify_error ?? true,
    notify_warning: method?.notify_warning ?? true,
    notify_success: method?.notify_success ?? true,
    notify_info: method?.notify_info ?? false,
  });
  const [draft, setDraft] = useState<AlertSourcesDraft>(() => draftFromAlertSources(method?.alert_sources));

  // Credentials. Secrets start empty when editing (see the component docs).
  const storedUsername = typeof method?.config.username === 'string' ? method.config.username : '';
  const [webhookUrl, setWebhookUrl] = useState('');
  const [username, setUsername] = useState(isEditing ? storedUsername : 'ECM Alerts');
  const [botToken, setBotToken] = useState('');
  const [chatId, setChatId] = useState('');

  const [epgSources, setEpgSources] = useState<GroupMultiSelectOption[]>([]);
  const [m3uAccounts, setM3uAccounts] = useState<GroupMultiSelectOption[]>([]);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const hasCredentials = methodType === 'discord' || methodType === 'telegram';

  useEffect(() => {
    let cancelled = false;
    const toOptions = (items: { id: number; name: string }[]) => items.map(({ id, name: itemName }) => ({ id, name: itemName }));
    const loadFailed = (what: string) => (err: unknown) => {
      if (cancelled) return;
      notifications.error(
        `Could not load ${what} for the filters: ${err instanceof Error ? err.message : 'request failed'}`,
        'Alert Methods',
      );
    };
    api.getEPGSources()
      .then((sources) => { if (!cancelled) setEpgSources(toOptions(sources)); })
      .catch(loadFailed('EPG sources'));
    api.getM3UAccounts()
      .then((accounts) => { if (!cancelled) setM3uAccounts(toOptions(accounts)); })
      .catch(loadFailed('M3U accounts'));
    return () => { cancelled = true; };
  }, [notifications]);

  const updateCategory = (key: string, patch: Partial<CategoryDraft>) => {
    setDraft((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  };

  const entityOptions: Record<string, GroupMultiSelectOption[]> = {
    epg_refresh: epgSources,
    m3u_refresh: m3uAccounts,
  };

  /** A client-side problem with the typed credentials, or null. */
  const credentialProblem = (): string | null => {
    if (methodType === 'discord') {
      if (!webhookUrl && !isEditing) return 'Webhook URL is required';
      if (webhookUrl && !DISCORD_WEBHOOK_PATTERN.test(webhookUrl)) {
        return 'Webhook URL must be a discord.com webhook (https://discord.com/api/webhooks/...)';
      }
    }
    if (methodType === 'telegram') {
      if (!isEditing && (!botToken || !chatId)) return 'Bot token and chat ID are required';
      if (botToken && !TELEGRAM_TOKEN_PATTERN.test(botToken)) return 'Bot token must look like 123456789:ABC...';
    }
    return null;
  };

  const buildConfig = (): Record<string, unknown> => {
    // Start from the stored config (secrets arrive masked) so keys this form
    // does not show, such as a Telegram parse_mode, survive the replace.
    const base: Record<string, unknown> = method ? { ...method.config } : {};
    if (methodType === 'discord') {
      base.webhook_url = webhookUrl || (isEditing ? MASKED : '');
      if (username.trim()) base.username = username.trim();
      else delete base.username;
    } else {
      base.bot_token = botToken || (isEditing ? MASKED : '');
      base.chat_id = chatId.trim() || (isEditing ? MASKED : '');
    }
    return base;
  };

  const handleTest = async () => {
    const problem = credentialProblem();
    if (problem) {
      notifications.error(problem, 'Alert Methods');
      return;
    }
    setTesting(true);
    try {
      let result: { success: boolean; message: string };
      if (methodType === 'discord' && webhookUrl) {
        result = await api.testDiscordWebhook(webhookUrl);
      } else if (methodType === 'telegram' && (botToken || chatId)) {
        if (!botToken || !chatId) {
          notifications.error('Enter both the bot token and the chat ID to test them, or leave both blank to test the saved bot.', 'Alert Methods');
          return;
        }
        result = await api.testTelegramBot(botToken, chatId.trim());
      } else if (method) {
        result = await api.testAlertMethod(method.id);
      } else {
        notifications.error('Enter the destination details first', 'Alert Methods');
        return;
      }
      if (result.success) {
        notifications.success(result.message || 'Test message sent', 'Alert Methods');
      } else {
        notifications.error(result.message || 'Test failed', 'Alert Methods');
      }
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Test failed', 'Alert Methods');
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    if (!name.trim()) {
      notifications.error('Name is required', 'Alert Methods');
      return;
    }
    const problem = credentialProblem();
    if (problem) {
      notifications.error(problem, 'Alert Methods');
      return;
    }
    const common = {
      name: name.trim(),
      enabled,
      ...severities,
      alert_sources: alertSourcesFromDraft(draft, method?.alert_sources),
    };
    setSaving(true);
    try {
      if (method) {
        await api.updateAlertMethod(method.id, hasCredentials ? { ...common, config: buildConfig() } : common);
        notifications.success(`Saved "${common.name}"`, 'Alert Methods');
      } else {
        await api.createAlertMethod({ ...common, method_type: methodType, config: buildConfig() });
        notifications.success(`Added "${common.name}"`, 'Alert Methods');
      }
      onSaved();
      onClose();
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Save failed', 'Alert Methods');
    } finally {
      setSaving(false);
    }
  };

  const busy = saving || testing;
  const closeModal = () => { if (!busy) onClose(); };
  const title = method ? `Edit ${method.name}` : 'Add alert destination';

  return (
    <ModalOverlay onClose={closeModal} role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="modal-container modal-lg alert-method-modal" ref={containerRef}>
        <div className="modal-header">
          <h3 id={titleId}>{title}</h3>
          <button className="modal-close-btn" onClick={closeModal} disabled={busy} aria-label="Close" title="Close">
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </div>
        <div className="modal-body">
          <div className="modal-form-group">
            <label htmlFor="alert-method-name">Name</label>
            <input
              id="alert-method-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Errors to #alerts"
            />
          </div>

          {!isEditing && (
            <div className="modal-form-group">
              <label htmlFor="alert-method-type">Type</label>
              <CustomSelect
                id="alert-method-type"
                value={methodType}
                onChange={setMethodType}
                options={TYPE_OPTIONS}
              />
            </div>
          )}

          {methodType === 'discord' && (
            <div className="modal-section">
              <h4 className="modal-section-title">Discord webhook</h4>
              <div className="modal-form-group">
                <label htmlFor="alert-method-webhook">Webhook URL</label>
                <input
                  id="alert-method-webhook"
                  type="password"
                  autoComplete="off"
                  value={webhookUrl}
                  onChange={(e) => setWebhookUrl(e.target.value.trim())}
                  placeholder={isEditing ? KEEP_PLACEHOLDER : 'https://discord.com/api/webhooks/...'}
                />
                <p className="form-hint">
                  In Discord: Server Settings → Integrations → Webhooks → New Webhook, then copy its URL.
                </p>
              </div>
              <div className="modal-form-group">
                <label htmlFor="alert-method-username">Post as</label>
                <input
                  id="alert-method-username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="The webhook's own name"
                />
              </div>
            </div>
          )}

          {methodType === 'telegram' && (
            <div className="modal-section">
              <h4 className="modal-section-title">Telegram bot</h4>
              <div className="modal-form-group">
                <label htmlFor="alert-method-bot-token">Bot token</label>
                <input
                  id="alert-method-bot-token"
                  type="password"
                  autoComplete="off"
                  value={botToken}
                  onChange={(e) => setBotToken(e.target.value.trim())}
                  placeholder={isEditing ? KEEP_PLACEHOLDER : '123456789:ABC...'}
                />
                <p className="form-hint">Create a bot with @BotFather and copy its token.</p>
              </div>
              <div className="modal-form-group">
                <label htmlFor="alert-method-chat-id">Chat ID</label>
                <input
                  id="alert-method-chat-id"
                  type="text"
                  autoComplete="off"
                  value={chatId}
                  onChange={(e) => setChatId(e.target.value)}
                  placeholder={isEditing ? KEEP_PLACEHOLDER : '-1001234567890'}
                />
                <p className="form-hint">Use @userinfobot or @RawDataBot to find it. Group chat IDs are negative.</p>
              </div>
            </div>
          )}

          {method && !hasCredentials && (
            <p className="form-hint alert-method-modal-note">
              {method.method_type === 'smtp'
                ? 'Email recipients are set under Email Alert Recipients on this page.'
                : 'To change where this destination sends, delete it and add it again.'}
            </p>
          )}

          <div className="modal-section">
            <h4 className="modal-section-title">Severities</h4>
            <div className="alert-method-severity-grid">
              {SEVERITIES.map(([flag, label]) => (
                <label key={flag} className="modal-checkbox-label">
                  <input
                    type="checkbox"
                    checked={severities[flag]}
                    onChange={(e) => setSeverities((prev) => ({ ...prev, [flag]: e.target.checked }))}
                  />
                  {label}
                </label>
              ))}
            </div>
          </div>

          <div className="modal-section">
            <h4 className="modal-section-title">Alert categories</h4>
            <p className="form-hint alert-method-modal-note">
              Untick a category to stop it reaching this destination. Per-task Email / Discord / Telegram
              toggles still apply on top, to every destination of that type.
            </p>
            {ALERT_CATEGORIES.map((category) => {
              const entry = draft[category.key];
              const inputId = `alert-category-${category.key}`;
              return (
                <div key={category.key} className="alert-category-row">
                  <label className="modal-checkbox-label">
                    <input
                      id={inputId}
                      type="checkbox"
                      checked={entry.enabled}
                      onChange={(e) => updateCategory(category.key, { enabled: e.target.checked })}
                    />
                    {category.label}
                  </label>
                  <p className="form-hint alert-category-description">{category.description}</p>
                  {entry.enabled && category.kind === 'entities' && (
                    <div className="alert-category-filter">
                      <CustomSelect
                        value={entry.filterMode}
                        onChange={(value) => updateCategory(category.key, { filterMode: value as AlertSourceFilterMode })}
                        options={FILTER_MODE_OPTIONS}
                        ariaLabel={`Which ${category.label} alert`}
                      />
                      {entry.filterMode !== 'all' && (
                        <GroupMultiSelectDropdown
                          options={entityOptions[category.key] ?? []}
                          selectedIds={entry.ids}
                          onChange={(ids) => updateCategory(category.key, { ids })}
                          label={ENTITY_PICKER_TEXT[category.key].label}
                          placeholder={ENTITY_PICKER_TEXT[category.key].placeholder}
                          emptyMessage={ENTITY_PICKER_TEXT[category.key].empty}
                          itemLabelSingular={ENTITY_PICKER_TEXT[category.key].singular}
                          itemLabelPlural={ENTITY_PICKER_TEXT[category.key].plural}
                        />
                      )}
                    </div>
                  )}
                  {entry.enabled && category.kind === 'threshold' && (
                    <div className="alert-category-filter">
                      <label htmlFor={`${inputId}-min`} className="alert-category-threshold-label">
                        Alert on a probe with failures only when at least
                      </label>
                      {/* Whole numbers of at least 1: the backend requires an
                          integer, and 0 behaves exactly like 1 (a probe with
                          no failures always alerts). */}
                      <input
                        id={`${inputId}-min`}
                        type="number"
                        min={1}
                        step={1}
                        className="alert-category-threshold"
                        value={entry.minFailures}
                        onChange={(e) => updateCategory(category.key, {
                          minFailures: Math.max(1, Math.floor(Number(e.target.value)) || 1),
                        })}
                      />
                      <span className="alert-category-threshold-label">streams failed</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <label className="modal-checkbox-label">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            Enabled
          </label>
        </div>
        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={handleTest} disabled={busy}>
            <span className={`material-icons${testing ? ' spinning' : ''}`} aria-hidden="true">
              {testing ? 'sync' : 'send'}
            </span>
            Send test
          </button>
          <button className="modal-btn modal-btn-secondary" onClick={closeModal} disabled={busy}>Cancel</button>
          <button className="modal-btn modal-btn-primary" onClick={handleSave} disabled={busy}>
            {saving ? 'Saving...' : isEditing ? 'Save changes' : 'Add destination'}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
