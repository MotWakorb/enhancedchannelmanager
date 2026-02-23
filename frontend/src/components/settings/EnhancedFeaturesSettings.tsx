import { useState, useEffect } from 'react';
import { useNotifications } from '../../contexts/NotificationContext';
import './EnhancedFeaturesSettings.css';

interface ProviderDiversificationConfig {
  enabled: boolean;
  mode: 'round_robin' | 'priority_weighted';
}

interface AccountStreamLimitsConfig {
  enabled: boolean;
  global_limit: number;
  account_limits: Record<string, number>;
}

interface M3UPriorityConfig {
  mode: 'disabled' | 'same_resolution' | 'all_streams';
  account_priorities: Record<string, number>;
}

interface EnhancedFeaturesConfig {
  provider_diversification: ProviderDiversificationConfig;
  account_stream_limits: AccountStreamLimitsConfig;
  m3u_priority: M3UPriorityConfig;
}

interface M3UAccount {
  id: number;
  name: string;
}

export function EnhancedFeaturesSettings() {
  const notifications = useNotifications();
  const [config, setConfig] = useState<EnhancedFeaturesConfig | null>(null);
  const [m3uAccounts, setM3uAccounts] = useState<M3UAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadConfig();
    loadM3UAccounts();
  }, []);

  const loadConfig = async () => {
    try {
      const response = await fetch('/api/enhanced-features/config');
      const data = await response.json();
      setConfig(data);
    } catch (err) {
      console.error('Failed to load enhanced features config:', err);
      notifications.error('Failed to load configuration');
    } finally {
      setLoading(false);
    }
  };

  const loadM3UAccounts = async () => {
    try {
      const response = await fetch('/api/m3u-accounts');
      const data = await response.json();
      setM3uAccounts(data);
    } catch (err) {
      console.error('Failed to load M3U accounts:', err);
    }
  };

  const handleSave = async () => {
    if (!config) return;

    setSaving(true);
    try {
      const response = await fetch('/api/enhanced-features/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });

      if (!response.ok) throw new Error('Failed to save');

      notifications.success('Enhanced features configuration saved');
    } catch (err) {
      console.error('Failed to save config:', err);
      notifications.error('Failed to save configuration');
    } finally {
      setSaving(false);
    }
  };

  const updateProviderDiversification = (updates: Partial<ProviderDiversificationConfig>) => {
    if (!config) return;
    setConfig({
      ...config,
      provider_diversification: {
        ...config.provider_diversification,
        ...updates,
      },
    });
  };

  const updateAccountStreamLimits = (updates: Partial<AccountStreamLimitsConfig>) => {
    if (!config) return;
    setConfig({
      ...config,
      account_stream_limits: {
        ...config.account_stream_limits,
        ...updates,
      },
    });
  };

  const updateM3UPriority = (updates: Partial<M3UPriorityConfig>) => {
    if (!config) return;
    setConfig({
      ...config,
      m3u_priority: {
        ...config.m3u_priority,
        ...updates,
      },
    });
  };

  const setAccountLimit = (accountId: string, limit: number) => {
    if (!config) return;
    const newLimits = { ...config.account_stream_limits.account_limits };
    if (limit === 0) {
      delete newLimits[accountId];
    } else {
      newLimits[accountId] = limit;
    }
    updateAccountStreamLimits({ account_limits: newLimits });
  };

  const setAccountPriority = (accountId: string, priority: number) => {
    if (!config) return;
    const newPriorities = { ...config.m3u_priority.account_priorities };
    if (priority === 0) {
      delete newPriorities[accountId];
    } else {
      newPriorities[accountId] = priority;
    }
    updateM3UPriority({ account_priorities: newPriorities });
  };

  if (loading || !config) {
    return <div className="enhanced-settings-loading">Loading...</div>;
  }

  return (
    <div className="enhanced-settings">
      <div className="enhanced-settings-header">
        <h2>Enhanced Stream Features</h2>
        <p className="enhanced-settings-description">
          Advanced stream management features for optimal stream distribution
        </p>
      </div>

      {/* Provider Diversification */}
      <div className="enhanced-card">
        <div className="enhanced-card-header">
          <div className="enhanced-card-title">
            <span className="material-icons">shuffle</span>
            <h3>Provider Diversification</h3>
          </div>
          <label className="enhanced-toggle">
            <input
              type="checkbox"
              checked={config.provider_diversification.enabled}
              onChange={(e) => updateProviderDiversification({ enabled: e.target.checked })}
            />
            <span className="enhanced-toggle-slider"></span>
          </label>
        </div>
        <div className="enhanced-card-body">
          <p className="enhanced-card-description">
            Distribute streams across different providers to avoid single points of failure
          </p>
          {config.provider_diversification.enabled && (
            <div className="enhanced-form-group">
              <label>Diversification Mode</label>
              <select
                value={config.provider_diversification.mode}
                onChange={(e) =>
                  updateProviderDiversification({
                    mode: e.target.value as 'round_robin' | 'priority_weighted',
                  })
                }
              >
                <option value="round_robin">Round Robin (Alphabetical)</option>
                <option value="priority_weighted">Priority Weighted</option>
              </select>
              <div className="enhanced-help-text">
                {config.provider_diversification.mode === 'round_robin' ? (
                  <>Providers ordered alphabetically: A → B → C → A → B → C...</>
                ) : (
                  <>Providers ordered by M3U priority: Premium(100) → Basic(10) → Premium(100)...</>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Account Stream Limits */}
      <div className="enhanced-card">
        <div className="enhanced-card-header">
          <div className="enhanced-card-title">
            <span className="material-icons">speed</span>
            <h3>Account Stream Limits</h3>
          </div>
          <label className="enhanced-toggle">
            <input
              type="checkbox"
              checked={config.account_stream_limits.enabled}
              onChange={(e) => updateAccountStreamLimits({ enabled: e.target.checked })}
            />
            <span className="enhanced-toggle-slider"></span>
          </label>
        </div>
        <div className="enhanced-card-body">
          <p className="enhanced-card-description">
            Limit the number of streams per M3U account per channel
          </p>
          {config.account_stream_limits.enabled && (
            <>
              <div className="enhanced-form-group">
                <label>Global Limit per Account (per channel)</label>
                <input
                  type="number"
                  min="0"
                  value={config.account_stream_limits.global_limit}
                  onChange={(e) =>
                    updateAccountStreamLimits({ global_limit: parseInt(e.target.value) || 0 })
                  }
                />
                <div className="enhanced-help-text">
                  Maximum streams per M3U account per channel (0 = unlimited)
                </div>
              </div>

              <div className="enhanced-form-group">
                <label>Per-Account Limits</label>
                <div className="enhanced-account-limits-table">
                  <table>
                    <thead>
                      <tr>
                        <th>M3U Account</th>
                        <th>Limit (per channel)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {m3uAccounts.map((account) => (
                        <tr key={account.id}>
                          <td>{account.name}</td>
                          <td>
                            <input
                              type="number"
                              min="0"
                              placeholder={`${config.account_stream_limits.global_limit} (global)`}
                              value={
                                config.account_stream_limits.account_limits[account.id.toString()] || ''
                              }
                              onChange={(e) =>
                                setAccountLimit(account.id.toString(), parseInt(e.target.value) || 0)
                              }
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="enhanced-help-text">
                  Override global limit for specific accounts (0 = use global limit)
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* M3U Priority */}
      <div className="enhanced-card">
        <div className="enhanced-card-header">
          <div className="enhanced-card-title">
            <span className="material-icons">priority_high</span>
            <h3>M3U Account Priority</h3>
          </div>
        </div>
        <div className="enhanced-card-body">
          <p className="enhanced-card-description">
            Boost stream scores based on M3U account priority
          </p>
          <div className="enhanced-form-group">
            <label>Priority Mode</label>
            <select
              value={config.m3u_priority.mode}
              onChange={(e) =>
                updateM3UPriority({
                  mode: e.target.value as 'disabled' | 'same_resolution' | 'all_streams',
                })
              }
            >
              <option value="disabled">Disabled</option>
              <option value="same_resolution">Same Resolution Only</option>
              <option value="all_streams">All Streams</option>
            </select>
            <div className="enhanced-help-text">
              {config.m3u_priority.mode === 'disabled' && <>No priority boosting applied</>}
              {config.m3u_priority.mode === 'same_resolution' && (
                <>Priority boost only affects streams with the same resolution</>
              )}
              {config.m3u_priority.mode === 'all_streams' && (
                <>Priority boost can promote lower quality streams from premium accounts</>
              )}
            </div>
          </div>

          {config.m3u_priority.mode !== 'disabled' && (
            <div className="enhanced-form-group">
              <label>Account Priorities</label>
              <div className="enhanced-account-limits-table">
                <table>
                  <thead>
                    <tr>
                      <th>M3U Account</th>
                      <th>Priority</th>
                    </tr>
                  </thead>
                  <tbody>
                    {m3uAccounts.map((account) => (
                      <tr key={account.id}>
                        <td>{account.name}</td>
                        <td>
                          <input
                            type="number"
                            min="0"
                            placeholder="0"
                            value={config.m3u_priority.account_priorities[account.id.toString()] || ''}
                            onChange={(e) =>
                              setAccountPriority(account.id.toString(), parseInt(e.target.value) || 0)
                            }
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="enhanced-help-text">
                Higher priority = higher boost (e.g., Premium=100, Basic=10)
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Save Button */}
      <div className="enhanced-settings-footer">
        <button
          className="enhanced-btn enhanced-btn-primary"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? 'Saving...' : 'Save Configuration'}
        </button>
      </div>
    </div>
  );
}
