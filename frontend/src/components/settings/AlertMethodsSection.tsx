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
 * scaffold needed to attach the requested Delete/Send Test buttons: a
 * read-only list + the two requested actions. It intentionally does NOT add
 * create/edit forms — those remain via the existing SMTP/Discord/Telegram
 * settings sections; building a full alert-method editor was out of scope
 * for this bead.
 */
export function AlertMethodsSection() {
  const notifications = useNotifications();
  const [methods, setMethods] = useState<api.AlertMethod[]>([]);
  const [loading, setLoading] = useState(true);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<api.AlertMethod | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadMethods = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.listAlertMethods();
      setMethods(result);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to load alert methods', 'Alert Methods');
    } finally {
      setLoading(false);
    }
  }, [notifications]);

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

  return (
    <div className="settings-section alert-methods-section">
      <div className="settings-section-header">
        <span className="material-icons">notifications_active</span>
        <h3>Alert Methods</h3>
      </div>
      <p className="section-description">
        Alert methods configured via SMTP, Discord, and Telegram settings above. Send a test
        message or remove a method you no longer use.
      </p>

      {loading ? (
        <div className="alert-methods-loading">
          <span className="material-icons spinning">sync</span>
          Loading alert methods...
        </div>
      ) : methods.length === 0 ? (
        <div className="alert-methods-empty empty-inline">
          No alert methods configured yet. Configure SMTP, Discord, or Telegram above to create one.
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
