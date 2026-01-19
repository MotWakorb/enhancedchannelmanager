import { useState, useEffect, useCallback } from 'react';
import * as api from '../services/api';
import type { AlertMethod, AlertMethodType, AlertMethodCreate, AlertMethodUpdate } from '../services/api';
import './AlertMethodSettings.css';

interface AlertMethodSettingsProps {
  className?: string;
}

interface MethodFormData {
  name: string;
  method_type: string;
  enabled: boolean;
  notify_info: boolean;
  notify_success: boolean;
  notify_warning: boolean;
  notify_error: boolean;
  min_interval_seconds: number;
  config: Record<string, string>;
}

const EMPTY_FORM: MethodFormData = {
  name: '',
  method_type: '',
  enabled: true,
  notify_info: false,
  notify_success: true,
  notify_warning: true,
  notify_error: true,
  min_interval_seconds: 60,
  config: {},
};

export function AlertMethodSettings({ className }: AlertMethodSettingsProps) {
  const [methods, setMethods] = useState<AlertMethod[]>([]);
  const [methodTypes, setMethodTypes] = useState<AlertMethodType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [editingMethod, setEditingMethod] = useState<AlertMethod | null>(null);
  const [formData, setFormData] = useState<MethodFormData>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  const loadMethods = useCallback(async () => {
    try {
      setLoading(true);
      const [methodsData, typesData] = await Promise.all([
        api.getAlertMethods(),
        api.getAlertMethodTypes(),
      ]);
      setMethods(methodsData);
      setMethodTypes(typesData);
      setError(null);
    } catch (err) {
      setError('Failed to load alert methods');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMethods();
  }, [loadMethods]);

  const handleAdd = () => {
    setEditingMethod(null);
    setFormData(EMPTY_FORM);
    setTestResult(null);
    setShowModal(true);
  };

  const handleEdit = (method: AlertMethod) => {
    setEditingMethod(method);
    setFormData({
      name: method.name,
      method_type: method.method_type,
      enabled: method.enabled,
      notify_info: method.notify_info,
      notify_success: method.notify_success,
      notify_warning: method.notify_warning,
      notify_error: method.notify_error,
      min_interval_seconds: method.min_interval_seconds,
      config: Object.fromEntries(
        Object.entries(method.config).map(([k, v]) => [k, String(v)])
      ),
    });
    setTestResult(null);
    setShowModal(true);
  };

  const handleDelete = async (method: AlertMethod) => {
    if (!confirm(`Are you sure you want to delete "${method.name}"?`)) {
      return;
    }
    try {
      await api.deleteAlertMethod(method.id);
      await loadMethods();
    } catch (err) {
      console.error('Failed to delete method:', err);
      alert('Failed to delete method');
    }
  };

  const handleToggleEnabled = async (method: AlertMethod) => {
    try {
      await api.updateAlertMethod(method.id, { enabled: !method.enabled });
      await loadMethods();
    } catch (err) {
      console.error('Failed to toggle method:', err);
    }
  };

  const handleTypeChange = (newType: string) => {
    const type = methodTypes.find(t => t.type === newType);
    const defaultConfig: Record<string, string> = {};

    if (type) {
      // Initialize required fields with empty strings
      type.required_fields.forEach(field => {
        defaultConfig[field] = '';
      });
      // Initialize optional fields with their defaults
      Object.entries(type.optional_fields).forEach(([field, defaultVal]) => {
        defaultConfig[field] = String(defaultVal);
      });
    }

    setFormData(prev => ({
      ...prev,
      method_type: newType,
      config: defaultConfig,
    }));
  };

  const handleConfigChange = (field: string, value: string) => {
    setFormData(prev => ({
      ...prev,
      config: {
        ...prev.config,
        [field]: value,
      },
    }));
  };

  const handleSave = async () => {
    if (!formData.name.trim()) {
      alert('Please enter a name');
      return;
    }
    if (!formData.method_type) {
      alert('Please select a method type');
      return;
    }

    setSaving(true);
    try {
      // Convert config values to appropriate types
      const config: Record<string, unknown> = {};
      const type = methodTypes.find(t => t.type === formData.method_type);

      Object.entries(formData.config).forEach(([key, value]) => {
        // Check if it's a boolean field in optional_fields
        const optionalDefault = type?.optional_fields[key];
        if (typeof optionalDefault === 'boolean') {
          config[key] = value === 'true' || value === '1';
        } else if (typeof optionalDefault === 'number') {
          config[key] = Number(value) || 0;
        } else {
          config[key] = value;
        }
      });

      if (editingMethod) {
        const update: AlertMethodUpdate = {
          name: formData.name,
          config,
          enabled: formData.enabled,
          notify_info: formData.notify_info,
          notify_success: formData.notify_success,
          notify_warning: formData.notify_warning,
          notify_error: formData.notify_error,
          min_interval_seconds: formData.min_interval_seconds,
        };
        await api.updateAlertMethod(editingMethod.id, update);
      } else {
        const create: AlertMethodCreate = {
          name: formData.name,
          method_type: formData.method_type,
          config,
          enabled: formData.enabled,
          notify_info: formData.notify_info,
          notify_success: formData.notify_success,
          notify_warning: formData.notify_warning,
          notify_error: formData.notify_error,
          min_interval_seconds: formData.min_interval_seconds,
        };
        await api.createAlertMethod(create);
      }

      setShowModal(false);
      await loadMethods();
    } catch (err) {
      console.error('Failed to save method:', err);
      const message = err instanceof Error ? err.message : 'Failed to save method';
      alert(message);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!editingMethod) {
      // Need to save first for new methods
      alert('Please save the method first before testing');
      return;
    }

    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testAlertMethod(editingMethod.id);
      setTestResult(result);
    } catch (err) {
      setTestResult({ success: false, message: 'Failed to test method' });
    } finally {
      setTesting(false);
    }
  };

  const getMethodTypeIcon = (type: string): string => {
    switch (type) {
      case 'discord': return 'forum';
      case 'telegram': return 'send';
      case 'smtp': return 'email';
      default: return 'notifications';
    }
  };

  const getFieldLabel = (field: string): string => {
    const labels: Record<string, string> = {
      webhook_url: 'Webhook URL',
      username: 'Bot Username',
      avatar_url: 'Avatar URL',
      include_timestamp: 'Include Timestamp',
      bot_token: 'Bot Token',
      chat_id: 'Chat ID',
      parse_mode: 'Parse Mode',
      disable_notification: 'Silent Messages',
      disable_web_page_preview: 'Disable Link Previews',
      smtp_host: 'SMTP Host',
      smtp_port: 'SMTP Port',
      smtp_user: 'SMTP Username',
      smtp_password: 'SMTP Password',
      from_email: 'From Email',
      from_name: 'From Name',
      to_emails: 'To Emails (comma separated)',
      use_tls: 'Use TLS',
      use_ssl: 'Use SSL',
    };
    return labels[field] || field.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  };

  const renderConfigField = (field: string, required: boolean, defaultValue?: unknown) => {
    const value = formData.config[field] ?? '';
    const isBoolean = typeof defaultValue === 'boolean';
    const isPassword = field.toLowerCase().includes('password') || field.toLowerCase().includes('token');

    if (isBoolean) {
      return (
        <div className="form-group" key={field}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={value === 'true' || value === '1' || value === true}
              onChange={(e) => handleConfigChange(field, e.target.checked ? 'true' : 'false')}
            />
            {getFieldLabel(field)}
          </label>
        </div>
      );
    }

    return (
      <div className="form-group" key={field}>
        <label>
          {getFieldLabel(field)}
          {required && <span className="required">*</span>}
        </label>
        <input
          type={isPassword ? 'password' : 'text'}
          value={String(value)}
          onChange={(e) => handleConfigChange(field, e.target.value)}
          placeholder={defaultValue !== undefined ? `Default: ${defaultValue}` : ''}
        />
      </div>
    );
  };

  const selectedType = methodTypes.find(t => t.type === formData.method_type);

  if (loading) {
    return (
      <div className={`alert-method-settings ${className || ''}`}>
        <div className="loading">Loading alert methods...</div>
      </div>
    );
  }

  return (
    <div className={`alert-method-settings ${className || ''}`}>
      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">campaign</span>
          <h3>Alert Methods</h3>
        </div>

        {error && <div className="error-message">{error}</div>}

        <p className="form-hint">
          Configure external services to receive alerts and notifications from ECM.
          Supports Discord webhooks, Telegram bots, and email via SMTP.
        </p>

        <div className="method-list">
          {methods.length === 0 ? (
            <div className="no-methods">
              <span className="material-icons">notifications_off</span>
              <p>No alert methods configured</p>
              <button className="btn-primary" onClick={handleAdd}>
                <span className="material-icons">add</span>
                Add Method
              </button>
            </div>
          ) : (
            <>
              {methods.map(method => (
                <div key={method.id} className={`method-item ${method.enabled ? '' : 'disabled'}`}>
                  <div className="method-icon">
                    <span className="material-icons">{getMethodTypeIcon(method.method_type)}</span>
                  </div>
                  <div className="method-info">
                    <div className="method-name">{method.name}</div>
                    <div className="method-type">
                      {methodTypes.find(t => t.type === method.method_type)?.display_name || method.method_type}
                    </div>
                    <div className="method-notifications">
                      {method.notify_error && <span className="notif-badge error">Errors</span>}
                      {method.notify_warning && <span className="notif-badge warning">Warnings</span>}
                      {method.notify_success && <span className="notif-badge success">Success</span>}
                      {method.notify_info && <span className="notif-badge info">Info</span>}
                    </div>
                  </div>
                  <div className="method-actions">
                    <button
                      className={`toggle-btn ${method.enabled ? 'enabled' : ''}`}
                      onClick={() => handleToggleEnabled(method)}
                      title={method.enabled ? 'Disable' : 'Enable'}
                    >
                      <span className="material-icons">
                        {method.enabled ? 'toggle_on' : 'toggle_off'}
                      </span>
                    </button>
                    <button className="edit-btn" onClick={() => handleEdit(method)} title="Edit">
                      <span className="material-icons">edit</span>
                    </button>
                    <button className="delete-btn" onClick={() => handleDelete(method)} title="Delete">
                      <span className="material-icons">delete</span>
                    </button>
                  </div>
                </div>
              ))}
              <button className="btn-add-method" onClick={handleAdd}>
                <span className="material-icons">add</span>
                Add Method
              </button>
            </>
          )}
        </div>
      </div>

      {/* Method Editor Modal */}
      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-content method-editor-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{editingMethod ? 'Edit Alert Method' : 'Add Alert Method'}</h2>
              <button className="close-btn" onClick={() => setShowModal(false)}>
                <span className="material-icons">close</span>
              </button>
            </div>

            <div className="modal-body">
              <div className="form-group">
                <label>Name <span className="required">*</span></label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData(prev => ({ ...prev, name: e.target.value }))}
                  placeholder="e.g., Discord Alerts"
                />
              </div>

              <div className="form-group">
                <label>Method Type <span className="required">*</span></label>
                <select
                  value={formData.method_type}
                  onChange={(e) => handleTypeChange(e.target.value)}
                  disabled={!!editingMethod}
                >
                  <option value="">Select a method type...</option>
                  {methodTypes.map(type => (
                    <option key={type.type} value={type.type}>
                      {type.display_name}
                    </option>
                  ))}
                </select>
              </div>

              {selectedType && (
                <>
                  <div className="config-section">
                    <h4>Configuration</h4>
                    {selectedType.required_fields.map(field =>
                      renderConfigField(field, true)
                    )}
                    {Object.entries(selectedType.optional_fields).map(([field, defaultVal]) =>
                      renderConfigField(field, false, defaultVal)
                    )}
                  </div>

                  <div className="notification-types-section">
                    <h4>Notification Types</h4>
                    <p className="form-hint">Select which notification types this method should receive.</p>
                    <div className="notification-checkboxes">
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={formData.notify_error}
                          onChange={(e) => setFormData(prev => ({ ...prev, notify_error: e.target.checked }))}
                        />
                        <span className="notif-badge error">Errors</span>
                      </label>
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={formData.notify_warning}
                          onChange={(e) => setFormData(prev => ({ ...prev, notify_warning: e.target.checked }))}
                        />
                        <span className="notif-badge warning">Warnings</span>
                      </label>
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={formData.notify_success}
                          onChange={(e) => setFormData(prev => ({ ...prev, notify_success: e.target.checked }))}
                        />
                        <span className="notif-badge success">Success</span>
                      </label>
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={formData.notify_info}
                          onChange={(e) => setFormData(prev => ({ ...prev, notify_info: e.target.checked }))}
                        />
                        <span className="notif-badge info">Info</span>
                      </label>
                    </div>
                  </div>

                  <div className="rate-limit-section">
                    <h4>Rate Limiting</h4>
                    <div className="form-group">
                      <label>Minimum interval between notifications (seconds)</label>
                      <input
                        type="number"
                        min="0"
                        max="86400"
                        value={formData.min_interval_seconds}
                        onChange={(e) => setFormData(prev => ({ ...prev, min_interval_seconds: parseInt(e.target.value) || 0 }))}
                      />
                      <p className="form-hint">Prevents flooding. Set to 0 for no limit.</p>
                    </div>
                  </div>
                </>
              )}

              {testResult && (
                <div className={`test-result ${testResult.success ? 'success' : 'error'}`}>
                  <span className="material-icons">
                    {testResult.success ? 'check_circle' : 'error'}
                  </span>
                  {testResult.message}
                </div>
              )}
            </div>

            <div className="modal-footer">
              {editingMethod && (
                <button
                  className="btn-secondary"
                  onClick={handleTest}
                  disabled={testing || saving}
                >
                  {testing ? (
                    <>
                      <span className="material-icons spinning">refresh</span>
                      Testing...
                    </>
                  ) : (
                    <>
                      <span className="material-icons">send</span>
                      Test
                    </>
                  )}
                </button>
              )}
              <div className="footer-right">
                <button className="btn-cancel" onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button
                  className="btn-primary"
                  onClick={handleSave}
                  disabled={saving}
                >
                  {saving ? 'Saving...' : 'Save'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
