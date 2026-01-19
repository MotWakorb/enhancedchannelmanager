import { useState, useEffect, useCallback } from 'react';
import * as api from '../services/api';
import type { AlertChannel, AlertChannelType, AlertChannelCreate, AlertChannelUpdate } from '../services/api';
import './AlertChannelSettings.css';

interface AlertChannelSettingsProps {
  className?: string;
}

interface ChannelFormData {
  name: string;
  channel_type: string;
  enabled: boolean;
  notify_info: boolean;
  notify_success: boolean;
  notify_warning: boolean;
  notify_error: boolean;
  min_interval_seconds: number;
  config: Record<string, string>;
}

const EMPTY_FORM: ChannelFormData = {
  name: '',
  channel_type: '',
  enabled: true,
  notify_info: false,
  notify_success: true,
  notify_warning: true,
  notify_error: true,
  min_interval_seconds: 60,
  config: {},
};

export function AlertChannelSettings({ className }: AlertChannelSettingsProps) {
  const [channels, setChannels] = useState<AlertChannel[]>([]);
  const [channelTypes, setChannelTypes] = useState<AlertChannelType[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [editingChannel, setEditingChannel] = useState<AlertChannel | null>(null);
  const [formData, setFormData] = useState<ChannelFormData>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  const loadChannels = useCallback(async () => {
    try {
      setLoading(true);
      const [channelsData, typesData] = await Promise.all([
        api.getAlertChannels(),
        api.getAlertChannelTypes(),
      ]);
      setChannels(channelsData);
      setChannelTypes(typesData);
      setError(null);
    } catch (err) {
      setError('Failed to load alert channels');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadChannels();
  }, [loadChannels]);

  const handleAdd = () => {
    setEditingChannel(null);
    setFormData(EMPTY_FORM);
    setTestResult(null);
    setShowModal(true);
  };

  const handleEdit = (channel: AlertChannel) => {
    setEditingChannel(channel);
    setFormData({
      name: channel.name,
      channel_type: channel.channel_type,
      enabled: channel.enabled,
      notify_info: channel.notify_info,
      notify_success: channel.notify_success,
      notify_warning: channel.notify_warning,
      notify_error: channel.notify_error,
      min_interval_seconds: channel.min_interval_seconds,
      config: Object.fromEntries(
        Object.entries(channel.config).map(([k, v]) => [k, String(v)])
      ),
    });
    setTestResult(null);
    setShowModal(true);
  };

  const handleDelete = async (channel: AlertChannel) => {
    if (!confirm(`Are you sure you want to delete "${channel.name}"?`)) {
      return;
    }
    try {
      await api.deleteAlertChannel(channel.id);
      await loadChannels();
    } catch (err) {
      console.error('Failed to delete channel:', err);
      alert('Failed to delete channel');
    }
  };

  const handleToggleEnabled = async (channel: AlertChannel) => {
    try {
      await api.updateAlertChannel(channel.id, { enabled: !channel.enabled });
      await loadChannels();
    } catch (err) {
      console.error('Failed to toggle channel:', err);
    }
  };

  const handleTypeChange = (newType: string) => {
    const type = channelTypes.find(t => t.type === newType);
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
      channel_type: newType,
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
    if (!formData.channel_type) {
      alert('Please select a channel type');
      return;
    }

    setSaving(true);
    try {
      // Convert config values to appropriate types
      const config: Record<string, unknown> = {};
      const type = channelTypes.find(t => t.type === formData.channel_type);

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

      if (editingChannel) {
        const update: AlertChannelUpdate = {
          name: formData.name,
          config,
          enabled: formData.enabled,
          notify_info: formData.notify_info,
          notify_success: formData.notify_success,
          notify_warning: formData.notify_warning,
          notify_error: formData.notify_error,
          min_interval_seconds: formData.min_interval_seconds,
        };
        await api.updateAlertChannel(editingChannel.id, update);
      } else {
        const create: AlertChannelCreate = {
          name: formData.name,
          channel_type: formData.channel_type,
          config,
          enabled: formData.enabled,
          notify_info: formData.notify_info,
          notify_success: formData.notify_success,
          notify_warning: formData.notify_warning,
          notify_error: formData.notify_error,
          min_interval_seconds: formData.min_interval_seconds,
        };
        await api.createAlertChannel(create);
      }

      setShowModal(false);
      await loadChannels();
    } catch (err) {
      console.error('Failed to save channel:', err);
      const message = err instanceof Error ? err.message : 'Failed to save channel';
      alert(message);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!editingChannel) {
      // Need to save first for new channels
      alert('Please save the channel first before testing');
      return;
    }

    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testAlertChannel(editingChannel.id);
      setTestResult(result);
    } catch (err) {
      setTestResult({ success: false, message: 'Failed to test channel' });
    } finally {
      setTesting(false);
    }
  };

  const getChannelTypeIcon = (type: string): string => {
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

  const selectedType = channelTypes.find(t => t.type === formData.channel_type);

  if (loading) {
    return (
      <div className={`alert-channel-settings ${className || ''}`}>
        <div className="loading">Loading alert channels...</div>
      </div>
    );
  }

  return (
    <div className={`alert-channel-settings ${className || ''}`}>
      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">campaign</span>
          <h3>Alert Channels</h3>
        </div>

        {error && <div className="error-message">{error}</div>}

        <p className="form-hint">
          Configure external services to receive alerts and notifications from ECM.
          Supports Discord webhooks, Telegram bots, and email via SMTP.
        </p>

        <div className="channel-list">
          {channels.length === 0 ? (
            <div className="no-channels">
              <span className="material-icons">notifications_off</span>
              <p>No alert channels configured</p>
              <button className="btn-primary" onClick={handleAdd}>
                <span className="material-icons">add</span>
                Add Channel
              </button>
            </div>
          ) : (
            <>
              {channels.map(channel => (
                <div key={channel.id} className={`channel-item ${channel.enabled ? '' : 'disabled'}`}>
                  <div className="channel-icon">
                    <span className="material-icons">{getChannelTypeIcon(channel.channel_type)}</span>
                  </div>
                  <div className="channel-info">
                    <div className="channel-name">{channel.name}</div>
                    <div className="channel-type">
                      {channelTypes.find(t => t.type === channel.channel_type)?.display_name || channel.channel_type}
                    </div>
                    <div className="channel-notifications">
                      {channel.notify_error && <span className="notif-badge error">Errors</span>}
                      {channel.notify_warning && <span className="notif-badge warning">Warnings</span>}
                      {channel.notify_success && <span className="notif-badge success">Success</span>}
                      {channel.notify_info && <span className="notif-badge info">Info</span>}
                    </div>
                  </div>
                  <div className="channel-actions">
                    <button
                      className={`toggle-btn ${channel.enabled ? 'enabled' : ''}`}
                      onClick={() => handleToggleEnabled(channel)}
                      title={channel.enabled ? 'Disable' : 'Enable'}
                    >
                      <span className="material-icons">
                        {channel.enabled ? 'toggle_on' : 'toggle_off'}
                      </span>
                    </button>
                    <button className="edit-btn" onClick={() => handleEdit(channel)} title="Edit">
                      <span className="material-icons">edit</span>
                    </button>
                    <button className="delete-btn" onClick={() => handleDelete(channel)} title="Delete">
                      <span className="material-icons">delete</span>
                    </button>
                  </div>
                </div>
              ))}
              <button className="btn-add-channel" onClick={handleAdd}>
                <span className="material-icons">add</span>
                Add Channel
              </button>
            </>
          )}
        </div>
      </div>

      {/* Channel Editor Modal */}
      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-content channel-editor-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{editingChannel ? 'Edit Alert Channel' : 'Add Alert Channel'}</h2>
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
                <label>Channel Type <span className="required">*</span></label>
                <select
                  value={formData.channel_type}
                  onChange={(e) => handleTypeChange(e.target.value)}
                  disabled={!!editingChannel}
                >
                  <option value="">Select a channel type...</option>
                  {channelTypes.map(type => (
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
                    <p className="form-hint">Select which notification types this channel should receive.</p>
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
              {editingChannel && (
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
