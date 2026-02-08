/**
 * Alert Configuration Panel Component (v0.11.6-0010)
 *
 * Admin panel for configuring service alert rules.
 */
import { useState, useEffect, useMemo } from 'react';
import type { ServiceAlertRule, ServiceWithStatus } from '../../types';
import * as api from '../../services/api';
import { CustomSelect } from '../CustomSelect';
import './AlertConfigurationPanel.css';

export interface AlertConfigurationPanelProps {
  /** Refresh callback */
  onRefresh?: () => void;
}

// Notification method type for display
interface NotificationMethod {
  id: string;
  name: string;
  type: string;
}

// Condition options
const CONDITIONS = [
  { value: 'status_change', label: 'Status Change', description: 'Alert when status changes' },
  { value: 'consecutive_failures', label: 'Consecutive Failures', description: 'Alert after N failures' },
  { value: 'response_time', label: 'Response Time', description: 'Alert when response exceeds threshold' },
];

// Threshold options for status change
const STATUS_THRESHOLDS = [
  { value: 'any', label: 'Any change' },
  { value: 'unhealthy', label: 'Becomes unhealthy' },
  { value: 'degraded', label: 'Becomes degraded or worse' },
];

export function AlertConfigurationPanel({ onRefresh: _onRefresh }: AlertConfigurationPanelProps) {
  // State
  const [rules, setRules] = useState<ServiceAlertRule[]>([]);
  const [notificationMethods, setNotificationMethods] = useState<NotificationMethod[]>([]);
  const [services, setServices] = useState<ServiceWithStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Form state for new rule
  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState({
    name: '',
    service_id: '',
    condition: 'status_change',
    threshold: 'any',
    notify_method_ids: [] as string[],
  });

  // Fetch rules, settings, and services
  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const [rulesData, settingsData, servicesData] = await Promise.all([
          api.getServiceAlertRules(),
          api.getSettings(),
          api.getServices(),
        ]);
        setRules(rulesData);
        setServices(servicesData);

        // Build notification methods from configured settings
        const methods: NotificationMethod[] = [];
        if (settingsData.smtp_configured) {
          methods.push({ id: 'smtp', name: 'Email (SMTP)', type: 'email' });
        }
        if (settingsData.discord_configured) {
          methods.push({ id: 'discord', name: 'Discord', type: 'discord' });
        }
        if (settingsData.telegram_configured) {
          methods.push({ id: 'telegram', name: 'Telegram', type: 'telegram' });
        }
        setNotificationMethods(methods);
      } catch (err) {
        setError('Failed to load alert configuration');
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  // Handle toggle rule enabled
  const handleToggleEnabled = async (ruleId: number, enabled: boolean) => {
    try {
      await api.updateServiceAlertRule(ruleId, { enabled: !enabled });
      setRules(prev => prev.map(r =>
        r.id === ruleId ? { ...r, enabled: !enabled } : r
      ));
    } catch (err) {
      setError('Failed to update rule');
    }
  };

  // Handle delete rule
  const handleDeleteRule = async (ruleId: number) => {
    if (!confirm('Are you sure you want to delete this alert rule?')) return;

    try {
      await api.deleteServiceAlertRule(ruleId);
      setRules(prev => prev.filter(r => r.id !== ruleId));
    } catch (err) {
      setError('Failed to delete rule');
    }
  };

  // Handle create rule
  const handleCreateRule = async () => {
    if (!formData.name.trim()) {
      setError('Rule name is required');
      return;
    }
    if (formData.notify_method_ids.length === 0) {
      setError('Select at least one notification method');
      return;
    }

    setSaving(true);
    setError(null);

    try {
      const newRule = await api.createServiceAlertRule({
        name: formData.name,
        service_id: formData.service_id || null,
        condition: formData.condition,
        threshold: formData.threshold,
        notify_method_ids: formData.notify_method_ids.join(','),
        enabled: true,
      });
      setRules(prev => [...prev, newRule]);
      setShowForm(false);
      setFormData({
        name: '',
        service_id: '',
        condition: 'status_change',
        threshold: 'any',
        notify_method_ids: [],
      });
    } catch (err) {
      setError('Failed to create rule');
    } finally {
      setSaving(false);
    }
  };

  // Get condition label
  const getConditionLabel = (condition: string): string => {
    return CONDITIONS.find(c => c.value === condition)?.label || condition;
  };

  // Format threshold for display
  const formatThreshold = (condition: string, threshold: string): string => {
    if (condition === 'status_change') {
      return STATUS_THRESHOLDS.find(t => t.value === threshold)?.label || threshold;
    }
    if (condition === 'consecutive_failures') {
      return `${threshold} failures`;
    }
    if (condition === 'response_time') {
      return `>${threshold}ms`;
    }
    return threshold;
  };

  // Get service name by ID
  const getServiceName = (serviceId: string | null): string => {
    if (!serviceId) return 'All services';
    return services.find(s => s.id === serviceId)?.name || serviceId;
  };

  // Memoized options for CustomSelect
  const serviceOptions = useMemo(() => [
    { value: '', label: 'All Services' },
    ...services.map(s => ({ value: s.id, label: s.name }))
  ], [services]);

  const conditionOptions = useMemo(() =>
    CONDITIONS.map(c => ({ value: c.value, label: c.label }))
  , []);

  const thresholdOptions = useMemo(() =>
    STATUS_THRESHOLDS.map(t => ({ value: t.value, label: t.label }))
  , []);

  if (loading) {
    return (
      <div className="alert-config-panel">
        <div className="loading-state">
          <span className="material-icons spinning">sync</span>
          Loading...
        </div>
      </div>
    );
  }

  return (
    <div className="alert-config-panel">
      <div className="panel-header">
        <h3>
          <span className="material-icons">notifications_active</span>
          Alert Rules
        </h3>
        <button
          className="btn-primary"
          onClick={() => setShowForm(!showForm)}
        >
          <span className="material-icons">{showForm ? 'close' : 'add'}</span>
          {showForm ? 'Cancel' : 'Add Rule'}
        </button>
      </div>

      {error && (
        <div className="error-banner">
          <span className="material-icons">error</span>
          {error}
          <button onClick={() => setError(null)}>
            <span className="material-icons">close</span>
          </button>
        </div>
      )}

      {/* New Rule Form */}
      {showForm && (
        <div className="rule-form">
          <div className="form-group">
            <label>Rule Name</label>
            <input
              type="text"
              value={formData.name}
              onChange={e => setFormData(prev => ({ ...prev, name: e.target.value }))}
              placeholder="e.g., Backend Down Alert"
            />
          </div>

          <div className="form-row">
            <div className="form-group">
              <label>Service</label>
              <CustomSelect
                options={serviceOptions}
                value={formData.service_id}
                onChange={value => setFormData(prev => ({ ...prev, service_id: value }))}
                placeholder="All Services"
                searchable={services.length > 5}
              />
            </div>

            <div className="form-group">
              <label>Condition</label>
              <CustomSelect
                options={conditionOptions}
                value={formData.condition}
                onChange={value => setFormData(prev => ({
                  ...prev,
                  condition: value,
                  threshold: value === 'status_change' ? 'any' :
                             value === 'consecutive_failures' ? '3' : '5000'
                }))}
              />
            </div>
          </div>

          <div className="form-group">
            <label>Threshold</label>
            {formData.condition === 'status_change' ? (
              <CustomSelect
                options={thresholdOptions}
                value={formData.threshold}
                onChange={value => setFormData(prev => ({ ...prev, threshold: value }))}
              />
            ) : (
              <input
                type="number"
                value={formData.threshold}
                onChange={e => setFormData(prev => ({ ...prev, threshold: e.target.value }))}
                placeholder={formData.condition === 'consecutive_failures' ? 'Number of failures' : 'Response time in ms'}
                min={1}
              />
            )}
          </div>

          <div className="form-group">
            <label>Notification Methods</label>
            <div className="method-checkboxes">
              {notificationMethods.length === 0 ? (
                <span className="no-methods">No notification methods configured. Configure them in Settings → Notification Settings.</span>
              ) : (
                notificationMethods.map(method => (
                  <label key={method.id} className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={formData.notify_method_ids.includes(method.id)}
                      onChange={e => {
                        if (e.target.checked) {
                          setFormData(prev => ({
                            ...prev,
                            notify_method_ids: [...prev.notify_method_ids, method.id]
                          }));
                        } else {
                          setFormData(prev => ({
                            ...prev,
                            notify_method_ids: prev.notify_method_ids.filter(id => id !== method.id)
                          }));
                        }
                      }}
                    />
                    <span className="method-name">{method.name}</span>
                    <span className="method-type">{method.type}</span>
                  </label>
                ))
              )}
            </div>
          </div>

          <div className="form-actions">
            <button
              className="btn-primary"
              onClick={handleCreateRule}
              disabled={saving}
            >
              {saving ? (
                <>
                  <span className="material-icons spinning">sync</span>
                  Saving...
                </>
              ) : (
                <>
                  <span className="material-icons">check</span>
                  Create Rule
                </>
              )}
            </button>
          </div>
        </div>
      )}

      {/* Rules List */}
      <div className="rules-list">
        {rules.length === 0 ? (
          <div className="no-rules">
            <span className="material-icons">notifications_off</span>
            <span>No alert rules configured</span>
          </div>
        ) : (
          rules.map(rule => (
            <div key={rule.id} className={`rule-item ${rule.enabled ? '' : 'disabled'}`}>
              <div className="rule-info">
                <span className="rule-name">{rule.name}</span>
                <div className="rule-meta">
                  <span className="rule-condition">
                    {getConditionLabel(rule.condition)}: {formatThreshold(rule.condition, rule.threshold)}
                  </span>
                  <span className="rule-service">
                    {getServiceName(rule.service_id)}
                  </span>
                </div>
              </div>
              <div className="rule-actions">
                <button
                  className={`toggle-btn ${rule.enabled ? 'enabled' : ''}`}
                  onClick={() => handleToggleEnabled(rule.id, rule.enabled)}
                  title={rule.enabled ? 'Disable' : 'Enable'}
                >
                  <span className="toggle-track">
                    <span className="toggle-thumb" />
                  </span>
                </button>
                <button
                  className="btn-icon-small danger"
                  onClick={() => handleDeleteRule(rule.id)}
                  title="Delete Rule"
                >
                  <span className="material-icons">delete</span>
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default AlertConfigurationPanel;
