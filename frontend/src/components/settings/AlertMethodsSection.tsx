import { useState, useEffect, useCallback } from 'react';
import * as api from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import { TypeToConfirmDialog } from '../TypeToConfirmDialog';
import './AlertMethodsSection.css';

const METHOD_TYPE_LABELS: Record<string, string> = {
  smtp: 'Email (SMTP)',
  discord: 'Discord',
  telegram: 'Telegram',
  webhook: 'Webhook',
  ntfy: 'ntfy',
};

function methodTypeLabel(type: string): string {
  return METHOD_TYPE_LABELS[type] ?? type;
}

/**
 * List configured alert methods with per-row Delete and Send Test actions
 * (enhancedchannelmanager-p4qt8).
 *
 * DISCOVERED SCOPE NOTE: no list UI for alert methods existed anywhere in the
 * frontend before this — SMTP/Discord/Telegram are each configured through
 * their own dedicated settings fields (which create/update a single
 * AlertMethod row behind the scenes), and GET /api/alert-methods was only
 * ever read to resolve the SMTP method's id. This component is the minimal
 * scaffold needed to attach the requested Delete/Send Test buttons. ntfy adds
 * a deliberately create-only form here because its masked access token cannot
 * safely round-trip through the generic whole-config update endpoint.
 *
 * ADMIN-ONLY (bead enhancedchannelmanager-9kwzp.10 item 4). Every backing
 * endpoint — the list, the per-method read, create, update and delete — is now
 * gated on the backend, because an alert method's `config` blob holds the
 * Discord webhook URL, the Telegram bot token and the SMTP password. Mirrors
 * BackupRestoreSection: render the lock notice rather than let a non-admin
 * watch the list 403.
 */
interface AlertMethodsSectionProps {
  /** Whether the signed-in user is an administrator. */
  isAdmin: boolean;
}

export function AlertMethodsSection({ isAdmin }: AlertMethodsSectionProps) {
  const notifications = useNotifications();
  const [methods, setMethods] = useState<api.AlertMethod[]>([]);
  const [loading, setLoading] = useState(true);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<api.AlertMethod | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [creating, setCreating] = useState(false);
  const [ntfyName, setNtfyName] = useState('');
  const [ntfyServerUrl, setNtfyServerUrl] = useState('');
  const [ntfyTopic, setNtfyTopic] = useState('');
  const [ntfyAccessToken, setNtfyAccessToken] = useState('');

  const loadMethods = useCallback(async () => {
    if (!isAdmin) return;
    setLoading(true);
    try {
      const result = await api.listAlertMethods();
      setMethods(result);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to load alert methods', 'Alert Methods');
    } finally {
      setLoading(false);
    }
  }, [isAdmin, notifications]);

  useEffect(() => {
    loadMethods();
  }, [loadMethods]);

  const handleSendTest = async (method: api.AlertMethod) => {
    setTestingId(method.id);
    try {
      const result = await api.testAlertMethod(method.id);
      if (result.success) {
        notifications.success(result.message || `Test message sent via ${method.name}`, 'Alert Methods');
      } else {
        notifications.error(result.message || 'Test failed', 'Alert Methods');
      }
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Test failed', 'Alert Methods');
    } finally {
      setTestingId(null);
    }
  };

  const handleConfirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.deleteAlertMethod(deleteTarget.id);
      notifications.success(`Deleted alert method "${deleteTarget.name}"`, 'Alert Methods');
      setMethods((prev) => prev.filter((m) => m.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to delete alert method', 'Alert Methods');
    } finally {
      setDeleting(false);
    }
  };

  const handleCreateNtfy = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    let parsedUrl: URL;
    try {
      parsedUrl = new URL(ntfyServerUrl);
    } catch {
      notifications.error('Enter a valid absolute HTTP(S) server URL.', 'Alert Methods');
      return;
    }
    if (
      !['http:', 'https:'].includes(parsedUrl.protocol)
      || parsedUrl.username
      || parsedUrl.password
      || parsedUrl.search
      || parsedUrl.hash
    ) {
      notifications.error('Enter an HTTP(S) server URL without credentials, query, or fragment.', 'Alert Methods');
      return;
    }
    if (!/^[-_A-Za-z0-9]{1,64}$/.test(ntfyTopic)) {
      notifications.error('Topic must contain 1-64 letters, numbers, hyphens, or underscores.', 'Alert Methods');
      return;
    }

    setCreating(true);
    try {
      await api.createAlertMethod({
        name: ntfyName,
        method_type: 'ntfy',
        config: {
          server_url: ntfyServerUrl,
          topic: ntfyTopic,
          ...(ntfyAccessToken ? { access_token: ntfyAccessToken } : {}),
        },
        enabled: true,
        notify_info: false,
        notify_success: true,
        notify_warning: true,
        notify_error: true,
      });
      setNtfyName('');
      setNtfyServerUrl('');
      setNtfyTopic('');
      setNtfyAccessToken('');
      await loadMethods();
      notifications.success('ntfy target created', 'Alert Methods');
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to create ntfy target', 'Alert Methods');
    } finally {
      setCreating(false);
    }
  };

  if (!isAdmin) {
    return (
      <div className="settings-section alert-methods-section">
        <div className="settings-section-header">
          <span className="material-icons">notifications_active</span>
          <h3>Alert Methods</h3>
        </div>
        <div className="alert-methods-empty empty-inline">
          <span className="material-icons">lock</span>
          Only administrators can view or manage alert methods.
        </div>
      </div>
    );
  }

  return (
    <div className="settings-section alert-methods-section">
      <div className="settings-section-header">
        <span className="material-icons">notifications_active</span>
        <h3>Alert Methods</h3>
      </div>
      <p className="section-description">
        Send a test message or remove an alert method you no longer use.
      </p>

      <form className="ntfy-create-form" onSubmit={handleCreateNtfy}>
        <h4>Add ntfy target</h4>
        <div className="ntfy-form-grid">
          <label>
            <span>Name</span>
            <input value={ntfyName} onChange={(event) => setNtfyName(event.target.value)} required />
          </label>
          <label>
            <span>Server URL</span>
            <input
              type="url"
              value={ntfyServerUrl}
              onChange={(event) => setNtfyServerUrl(event.target.value)}
              placeholder="https://ntfy.sh"
              required
            />
          </label>
          <label>
            <span>Topic</span>
            <input value={ntfyTopic} onChange={(event) => setNtfyTopic(event.target.value)} required />
          </label>
          <label>
            <span>Access token (optional)</span>
            <input
              type="password"
              value={ntfyAccessToken}
              onChange={(event) => setNtfyAccessToken(event.target.value)}
              autoComplete="new-password"
            />
          </label>
        </div>
        <button className="btn-primary" type="submit" disabled={creating}>
          {creating ? 'Adding...' : 'Add ntfy target'}
        </button>
      </form>

      {loading ? (
        <div className="alert-methods-loading">
          <span className="material-icons spinning">sync</span>
          Loading alert methods...
        </div>
      ) : methods.length === 0 ? (
        <div className="alert-methods-empty empty-inline">
          No alert methods configured yet.
        </div>
      ) : (
        <div className="alert-methods-list">
          {methods.map((method) => (
            <div key={method.id} className="alert-method-row">
              <div className="alert-method-info">
                <span className="alert-method-name">{method.name}</span>
                <span className="alert-method-type-badge">{methodTypeLabel(method.method_type)}</span>
                <span className={`badge badge-sm ${method.enabled ? 'badge-success' : ''}`}>
                  {method.enabled ? 'Enabled' : 'Disabled'}
                </span>
              </div>
              <div className="alert-method-actions">
                <button
                  className="btn-secondary alert-method-btn"
                  onClick={() => handleSendTest(method)}
                  disabled={testingId === method.id}
                  aria-label={testingId === method.id ? `Sending test to ${method.name}` : `Send test to ${method.name}`}
                  title={testingId === method.id ? 'Sending test…' : 'Send test message'}
                >
                  <span className="material-icons" aria-hidden="true">
                    {testingId === method.id ? 'hourglass_empty' : 'send'}
                  </span>
                </button>
                <button
                  className="btn-secondary alert-method-btn alert-method-delete"
                  onClick={() => setDeleteTarget(method)}
                  aria-label={`Delete ${method.name}`}
                  title="Delete alert method"
                >
                  <span className="material-icons" aria-hidden="true">delete</span>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {deleteTarget && (
        <TypeToConfirmDialog
          title="Delete Alert Method"
          message={
            <>
              Delete alert method <strong>{deleteTarget.name}</strong>? Any scheduled tasks or
              rules that notify through it will stop sending notifications via this method.
            </>
          }
          confirmText={deleteTarget.name}
          confirmLabel="Delete"
          busy={deleting}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={handleConfirmDelete}
        />
      )}
    </div>
  );
}
