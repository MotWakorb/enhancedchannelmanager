import { useState, useEffect, useCallback } from 'react';
import * as api from '../services/api';
import type { TaskStatus, CronPreset, CronValidationResult, TaskConfigUpdate } from '../services/api';
import type { EPGSource, M3UAccount } from '../types';
import { logger } from '../utils/logger';

interface TaskEditorModalProps {
  task: TaskStatus;
  onClose: () => void;
  onSaved: () => void;
}

// Common interval presets in seconds
const INTERVAL_PRESETS = [
  { label: '5 minutes', value: 300 },
  { label: '15 minutes', value: 900 },
  { label: '30 minutes', value: 1800 },
  { label: '1 hour', value: 3600 },
  { label: '2 hours', value: 7200 },
  { label: '6 hours', value: 21600 },
  { label: '12 hours', value: 43200 },
  { label: '24 hours', value: 86400 },
];

export function TaskEditorModal({ task, onClose, onSaved }: TaskEditorModalProps) {
  // Form state
  const [scheduleType, setScheduleType] = useState<'interval' | 'cron' | 'manual'>(task.schedule.schedule_type);
  const [intervalSeconds, setIntervalSeconds] = useState(task.schedule.interval_seconds || 3600);
  const [cronExpression, setCronExpression] = useState(task.schedule.cron_expression || '');
  const [scheduleTime, setScheduleTime] = useState(task.schedule.schedule_time || '');
  const [timezone, setTimezone] = useState(task.schedule.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [enabled, setEnabled] = useState(task.enabled);

  // Task-specific config state
  const [taskConfig, setTaskConfig] = useState<Record<string, unknown>>(task.config || {});

  // EPG/M3U data for selection
  const [epgSources, setEpgSources] = useState<EPGSource[]>([]);
  const [m3uAccounts, setM3uAccounts] = useState<M3UAccount[]>([]);

  // UI state
  const [cronPresets, setCronPresets] = useState<CronPreset[]>([]);
  const [cronValidation, setCronValidation] = useState<CronValidationResult | null>(null);
  const [validatingCron, setValidatingCron] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [useCustomInterval, setUseCustomInterval] = useState(false);

  // Check if the current interval matches a preset
  useEffect(() => {
    const isPreset = INTERVAL_PRESETS.some(p => p.value === intervalSeconds);
    setUseCustomInterval(!isPreset);
  }, []);

  // Load cron presets
  useEffect(() => {
    async function loadPresets() {
      try {
        const result = await api.getCronPresets();
        setCronPresets(result.presets);
      } catch (err) {
        logger.error('Failed to load cron presets', err);
      }
    }
    loadPresets();
  }, []);

  // Load EPG sources and M3U accounts for task-specific config
  useEffect(() => {
    async function loadData() {
      try {
        if (task.task_id === 'epg_refresh') {
          const sources = await api.getEPGSources();
          setEpgSources(sources);
        } else if (task.task_id === 'm3u_refresh') {
          const accounts = await api.getM3UAccounts();
          setM3uAccounts(accounts);
        }
      } catch (err) {
        logger.error('Failed to load data for task config', err);
      }
    }
    loadData();
  }, [task.task_id]);

  // Validate cron expression with debounce
  const validateCron = useCallback(async (expression: string) => {
    if (!expression.trim()) {
      setCronValidation(null);
      return;
    }
    setValidatingCron(true);
    try {
      const result = await api.validateCronExpression(expression);
      setCronValidation(result);
    } catch (err) {
      logger.error('Failed to validate cron expression', err);
      setCronValidation({ valid: false, error: 'Validation failed' });
    } finally {
      setValidatingCron(false);
    }
  }, []);

  // Debounced cron validation
  useEffect(() => {
    if (scheduleType !== 'cron') return;
    const timeout = setTimeout(() => validateCron(cronExpression), 500);
    return () => clearTimeout(timeout);
  }, [cronExpression, scheduleType, validateCron]);

  const handleSave = async () => {
    setError(null);
    setSaving(true);

    try {
      const config: TaskConfigUpdate = {
        enabled,
        schedule_type: scheduleType,
      };

      if (scheduleType === 'interval') {
        config.interval_seconds = intervalSeconds;
        if (scheduleTime) {
          config.schedule_time = scheduleTime;
        }
      } else if (scheduleType === 'cron') {
        if (!cronValidation?.valid) {
          setError('Please enter a valid cron expression');
          setSaving(false);
          return;
        }
        config.cron_expression = cronExpression;
      }

      if (timezone) {
        config.timezone = timezone;
      }

      // Include task-specific configuration
      if (Object.keys(taskConfig).length > 0) {
        config.config = taskConfig;
      }

      await api.updateTask(task.task_id, config);
      onSaved();
      onClose();
    } catch (err) {
      logger.error('Failed to save task configuration', err);
      setError('Failed to save configuration');
    } finally {
      setSaving(false);
    }
  };

  const handleIntervalPresetChange = (value: number) => {
    setIntervalSeconds(value);
    setUseCustomInterval(false);
  };

  const handleCustomIntervalChange = (hours: number, minutes: number) => {
    setIntervalSeconds(hours * 3600 + minutes * 60);
  };

  const customIntervalHours = Math.floor(intervalSeconds / 3600);
  const customIntervalMinutes = Math.floor((intervalSeconds % 3600) / 60);

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      backgroundColor: 'rgba(0, 0, 0, 0.6)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000,
    }} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div style={{
        backgroundColor: 'var(--bg-primary)',
        border: '1px solid var(--border-color)',
        borderRadius: '8px',
        width: '100%',
        maxWidth: '550px',
        maxHeight: '90vh',
        overflow: 'auto',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '1rem 1.5rem',
          borderBottom: '1px solid var(--border-color)',
        }}>
          <div>
            <h2 style={{ margin: 0, fontSize: '1.25rem' }}>Edit Task Schedule</h2>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginTop: '0.25rem' }}>
              {task.task_name}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-secondary)',
              padding: '0.5rem',
            }}
          >
            <span className="material-icons">close</span>
          </button>
        </div>

        {/* Content */}
        <div style={{ padding: '1.5rem' }}>
          {/* Task Description */}
          <div style={{
            backgroundColor: 'var(--bg-secondary)',
            padding: '1rem',
            borderRadius: '6px',
            marginBottom: '1.5rem',
            fontSize: '0.9rem',
            color: 'var(--text-secondary)',
          }}>
            {task.task_description}
          </div>

          {/* Enable/Disable */}
          <div style={{ marginBottom: '1.5rem' }}>
            <label style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              cursor: 'pointer',
            }}>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                style={{
                  width: '18px',
                  height: '18px',
                  accentColor: 'var(--accent-primary)',
                }}
              />
              <span style={{ fontWeight: 500 }}>Enable scheduled execution</span>
            </label>
          </div>

          {/* Schedule Type */}
          <div style={{ marginBottom: '1.5rem' }}>
            <label style={{
              display: 'block',
              marginBottom: '0.5rem',
              fontWeight: 500,
            }}>
              Schedule Type
            </label>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              {(['interval', 'cron', 'manual'] as const).map((type) => (
                <button
                  key={type}
                  onClick={() => setScheduleType(type)}
                  style={{
                    flex: 1,
                    padding: '0.75rem',
                    border: `1px solid ${scheduleType === type ? 'var(--success)' : 'var(--border-color)'}`,
                    borderRadius: '6px',
                    backgroundColor: scheduleType === type ? 'var(--success)' : 'var(--bg-secondary)',
                    color: scheduleType === type ? 'white' : 'var(--text-primary)',
                    cursor: 'pointer',
                    fontWeight: 500,
                    textTransform: 'capitalize',
                  }}
                >
                  {type === 'interval' ? 'Interval' : type === 'cron' ? 'Cron' : 'Manual Only'}
                </button>
              ))}
            </div>
          </div>

          {/* Interval Configuration */}
          {scheduleType === 'interval' && (
            <div style={{
              backgroundColor: 'var(--bg-secondary)',
              padding: '1rem',
              borderRadius: '6px',
              marginBottom: '1.5rem',
            }}>
              <label style={{
                display: 'block',
                marginBottom: '0.75rem',
                fontWeight: 500,
              }}>
                Run Interval
              </label>

              {/* Preset buttons */}
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(4, 1fr)',
                gap: '0.5rem',
                marginBottom: '1rem',
              }}>
                {INTERVAL_PRESETS.map((preset) => (
                  <button
                    key={preset.value}
                    onClick={() => handleIntervalPresetChange(preset.value)}
                    style={{
                      padding: '0.5rem',
                      border: `1px solid ${!useCustomInterval && intervalSeconds === preset.value ? 'var(--success)' : 'var(--border-color)'}`,
                      borderRadius: '4px',
                      backgroundColor: !useCustomInterval && intervalSeconds === preset.value ? 'var(--success)' : 'var(--bg-tertiary)',
                      color: !useCustomInterval && intervalSeconds === preset.value ? 'white' : 'var(--text-primary)',
                      cursor: 'pointer',
                      fontSize: '0.8rem',
                    }}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>

              {/* Custom interval */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <label style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  cursor: 'pointer',
                }}>
                  <input
                    type="checkbox"
                    checked={useCustomInterval}
                    onChange={(e) => setUseCustomInterval(e.target.checked)}
                    style={{ accentColor: 'var(--accent-primary)' }}
                  />
                  Custom:
                </label>
                <input
                  type="number"
                  min={0}
                  max={23}
                  value={customIntervalHours}
                  onChange={(e) => {
                    setUseCustomInterval(true);
                    handleCustomIntervalChange(parseInt(e.target.value) || 0, customIntervalMinutes);
                  }}
                  disabled={!useCustomInterval}
                  style={{
                    width: '60px',
                    padding: '0.5rem',
                    border: '1px solid var(--border-color)',
                    borderRadius: '4px',
                    backgroundColor: 'var(--bg-primary)',
                    color: 'var(--text-primary)',
                  }}
                />
                <span>hours</span>
                <input
                  type="number"
                  min={0}
                  max={59}
                  value={customIntervalMinutes}
                  onChange={(e) => {
                    setUseCustomInterval(true);
                    handleCustomIntervalChange(customIntervalHours, parseInt(e.target.value) || 0);
                  }}
                  disabled={!useCustomInterval}
                  style={{
                    width: '60px',
                    padding: '0.5rem',
                    border: '1px solid var(--border-color)',
                    borderRadius: '4px',
                    backgroundColor: 'var(--bg-primary)',
                    color: 'var(--text-primary)',
                  }}
                />
                <span>minutes</span>
              </div>

              {/* Optional: Start time */}
              <div style={{ marginTop: '1rem' }}>
                <label style={{
                  display: 'block',
                  marginBottom: '0.5rem',
                  fontSize: '0.9rem',
                  color: 'var(--text-secondary)',
                }}>
                  First run time (optional)
                </label>
                <input
                  type="time"
                  value={scheduleTime}
                  onChange={(e) => setScheduleTime(e.target.value)}
                  style={{
                    padding: '0.5rem',
                    border: '1px solid var(--border-color)',
                    borderRadius: '4px',
                    backgroundColor: 'var(--bg-primary)',
                    color: 'var(--text-primary)',
                  }}
                />
                <span style={{
                  marginLeft: '0.5rem',
                  fontSize: '0.8rem',
                  color: 'var(--text-muted)',
                }}>
                  Leave empty for immediate start
                </span>
              </div>
            </div>
          )}

          {/* Cron Configuration */}
          {scheduleType === 'cron' && (
            <div style={{
              backgroundColor: 'var(--bg-secondary)',
              padding: '1rem',
              borderRadius: '6px',
              marginBottom: '1.5rem',
            }}>
              <label style={{
                display: 'block',
                marginBottom: '0.75rem',
                fontWeight: 500,
              }}>
                Cron Expression
              </label>

              {/* Preset dropdown */}
              {cronPresets.length > 0 && (
                <div style={{ marginBottom: '1rem' }}>
                  <select
                    onChange={(e) => {
                      if (e.target.value) {
                        setCronExpression(e.target.value);
                      }
                    }}
                    style={{
                      width: '100%',
                      padding: '0.5rem',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      backgroundColor: 'var(--bg-primary)',
                      color: 'var(--text-primary)',
                    }}
                  >
                    <option value="">Select a preset...</option>
                    {cronPresets.map((preset) => (
                      <option key={preset.name} value={preset.expression}>
                        {preset.description} ({preset.expression})
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {/* Cron input */}
              <input
                type="text"
                value={cronExpression}
                onChange={(e) => setCronExpression(e.target.value)}
                placeholder="* * * * * (minute hour day month weekday)"
                style={{
                  width: '100%',
                  padding: '0.75rem',
                  border: `1px solid ${cronValidation ? (cronValidation.valid ? '#2ecc71' : '#e74c3c') : 'var(--border-color)'}`,
                  borderRadius: '4px',
                  backgroundColor: 'var(--bg-primary)',
                  color: 'var(--text-primary)',
                  fontFamily: 'monospace',
                  boxSizing: 'border-box',
                }}
              />

              {/* Validation result */}
              {validatingCron && (
                <div style={{ marginTop: '0.5rem', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                  Validating...
                </div>
              )}
              {!validatingCron && cronValidation && (
                <div style={{ marginTop: '0.5rem' }}>
                  {cronValidation.valid ? (
                    <>
                      <div style={{ color: '#2ecc71', fontSize: '0.85rem', marginBottom: '0.5rem' }}>
                        ✓ {cronValidation.description}
                      </div>
                      {cronValidation.next_runs && cronValidation.next_runs.length > 0 && (
                        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                          <div style={{ marginBottom: '0.25rem' }}>Next runs:</div>
                          {cronValidation.next_runs.slice(0, 3).map((run, i) => (
                            <div key={i}>{new Date(run).toLocaleString()}</div>
                          ))}
                        </div>
                      )}
                    </>
                  ) : (
                    <div style={{ color: '#e74c3c', fontSize: '0.85rem' }}>
                      ✗ {cronValidation.error}
                    </div>
                  )}
                </div>
              )}

              {/* Cron syntax help */}
              <div style={{
                marginTop: '1rem',
                padding: '0.75rem',
                backgroundColor: 'var(--bg-tertiary)',
                borderRadius: '4px',
                fontSize: '0.8rem',
                color: 'var(--text-muted)',
              }}>
                <div style={{ fontWeight: 500, marginBottom: '0.5rem' }}>Cron Format: minute hour day month weekday</div>
                <div>* = any value, */n = every n units</div>
                <div>Examples: 0 3 * * * (daily at 3 AM), 0 */6 * * * (every 6 hours)</div>
              </div>
            </div>
          )}

          {/* Manual mode info */}
          {scheduleType === 'manual' && (
            <div style={{
              backgroundColor: 'var(--bg-secondary)',
              padding: '1rem',
              borderRadius: '6px',
              marginBottom: '1.5rem',
              textAlign: 'center',
              color: 'var(--text-secondary)',
            }}>
              <span className="material-icons" style={{ fontSize: '32px', marginBottom: '0.5rem', display: 'block' }}>
                touch_app
              </span>
              This task will only run when manually triggered using the "Run Now" button.
            </div>
          )}

          {/* Timezone */}
          {scheduleType !== 'manual' && (
            <div style={{ marginBottom: '1.5rem' }}>
              <label style={{
                display: 'block',
                marginBottom: '0.5rem',
                fontWeight: 500,
              }}>
                Timezone
              </label>
              <select
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                style={{
                  width: '100%',
                  padding: '0.5rem',
                  border: '1px solid var(--border-color)',
                  borderRadius: '4px',
                  backgroundColor: 'var(--bg-primary)',
                  color: 'var(--text-primary)',
                }}
              >
                <option value="UTC">UTC</option>
                <option value="America/New_York">America/New_York (Eastern)</option>
                <option value="America/Chicago">America/Chicago (Central)</option>
                <option value="America/Denver">America/Denver (Mountain)</option>
                <option value="America/Los_Angeles">America/Los_Angeles (Pacific)</option>
                <option value="Europe/London">Europe/London</option>
                <option value="Europe/Paris">Europe/Paris</option>
                <option value="Asia/Tokyo">Asia/Tokyo</option>
                <option value="Australia/Sydney">Australia/Sydney</option>
                {/* Add browser timezone if not in list */}
                {!['UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
                   'Europe/London', 'Europe/Paris', 'Asia/Tokyo', 'Australia/Sydney'].includes(Intl.DateTimeFormat().resolvedOptions().timeZone) && (
                  <option value={Intl.DateTimeFormat().resolvedOptions().timeZone}>
                    {Intl.DateTimeFormat().resolvedOptions().timeZone} (Local)
                  </option>
                )}
              </select>
            </div>
          )}

          {/* Task-Specific Configuration */}
          {task.task_id === 'epg_refresh' && epgSources.length > 0 && (
            <div style={{
              backgroundColor: 'var(--bg-secondary)',
              padding: '1rem',
              borderRadius: '6px',
              marginBottom: '1.5rem',
            }}>
              <label style={{
                display: 'block',
                marginBottom: '0.75rem',
                fontWeight: 500,
              }}>
                EPG Sources to Refresh
              </label>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                Select specific sources or leave empty to refresh all active sources.
              </div>
              <div style={{ maxHeight: '200px', overflowY: 'auto' }}>
                {epgSources.map((source) => (
                  <label
                    key={source.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      padding: '0.5rem',
                      cursor: 'pointer',
                      borderRadius: '4px',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={((taskConfig.source_ids as number[]) || []).includes(source.id)}
                      onChange={(e) => {
                        const currentIds = (taskConfig.source_ids as number[]) || [];
                        if (e.target.checked) {
                          setTaskConfig({ ...taskConfig, source_ids: [...currentIds, source.id] });
                        } else {
                          setTaskConfig({ ...taskConfig, source_ids: currentIds.filter(id => id !== source.id) });
                        }
                      }}
                      style={{ accentColor: 'var(--accent-primary)' }}
                    />
                    <span>{source.name}</span>
                    {source.source_type === 'dummy' && (
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>(dummy)</span>
                    )}
                  </label>
                ))}
              </div>
              <label style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                marginTop: '0.75rem',
                cursor: 'pointer',
              }}>
                <input
                  type="checkbox"
                  checked={taskConfig.skip_dummy !== false}
                  onChange={(e) => setTaskConfig({ ...taskConfig, skip_dummy: e.target.checked })}
                  style={{ accentColor: 'var(--accent-primary)' }}
                />
                <span>Skip dummy EPG sources</span>
              </label>
            </div>
          )}

          {task.task_id === 'm3u_refresh' && m3uAccounts.length > 0 && (
            <div style={{
              backgroundColor: 'var(--bg-secondary)',
              padding: '1rem',
              borderRadius: '6px',
              marginBottom: '1.5rem',
            }}>
              <label style={{
                display: 'block',
                marginBottom: '0.75rem',
                fontWeight: 500,
              }}>
                M3U Accounts to Refresh
              </label>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                Select specific accounts or leave empty to refresh all active accounts.
              </div>
              <div style={{ maxHeight: '200px', overflowY: 'auto' }}>
                {m3uAccounts.map((account) => (
                  <label
                    key={account.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      padding: '0.5rem',
                      cursor: 'pointer',
                      borderRadius: '4px',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={((taskConfig.account_ids as number[]) || []).includes(account.id)}
                      onChange={(e) => {
                        const currentIds = (taskConfig.account_ids as number[]) || [];
                        if (e.target.checked) {
                          setTaskConfig({ ...taskConfig, account_ids: [...currentIds, account.id] });
                        } else {
                          setTaskConfig({ ...taskConfig, account_ids: currentIds.filter(id => id !== account.id) });
                        }
                      }}
                      style={{ accentColor: 'var(--accent-primary)' }}
                    />
                    <span>{account.name}</span>
                    {!account.is_active && (
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>(inactive)</span>
                    )}
                  </label>
                ))}
              </div>
              <label style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                marginTop: '0.75rem',
                cursor: 'pointer',
              }}>
                <input
                  type="checkbox"
                  checked={taskConfig.skip_inactive !== false}
                  onChange={(e) => setTaskConfig({ ...taskConfig, skip_inactive: e.target.checked })}
                  style={{ accentColor: 'var(--accent-primary)' }}
                />
                <span>Skip inactive accounts</span>
              </label>
            </div>
          )}

          {task.task_id === 'cleanup' && (
            <div style={{
              backgroundColor: 'var(--bg-secondary)',
              padding: '1rem',
              borderRadius: '6px',
              marginBottom: '1.5rem',
            }}>
              <label style={{
                display: 'block',
                marginBottom: '0.75rem',
                fontWeight: 500,
              }}>
                Retention Settings
              </label>
              <div style={{ display: 'grid', gap: '0.75rem' }}>
                <div>
                  <label style={{ display: 'block', fontSize: '0.85rem', marginBottom: '0.25rem' }}>
                    Probe history retention (days)
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={(taskConfig.probe_history_days as number) || 30}
                    onChange={(e) => setTaskConfig({ ...taskConfig, probe_history_days: parseInt(e.target.value) || 30 })}
                    style={{
                      width: '100px',
                      padding: '0.5rem',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      backgroundColor: 'var(--bg-primary)',
                      color: 'var(--text-primary)',
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: '0.85rem', marginBottom: '0.25rem' }}>
                    Task history retention (days)
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={(taskConfig.task_history_days as number) || 30}
                    onChange={(e) => setTaskConfig({ ...taskConfig, task_history_days: parseInt(e.target.value) || 30 })}
                    style={{
                      width: '100px',
                      padding: '0.5rem',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      backgroundColor: 'var(--bg-primary)',
                      color: 'var(--text-primary)',
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: '0.85rem', marginBottom: '0.25rem' }}>
                    Journal retention (days)
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={(taskConfig.journal_days as number) || 30}
                    onChange={(e) => setTaskConfig({ ...taskConfig, journal_days: parseInt(e.target.value) || 30 })}
                    style={{
                      width: '100px',
                      padding: '0.5rem',
                      border: '1px solid var(--border-color)',
                      borderRadius: '4px',
                      backgroundColor: 'var(--bg-primary)',
                      color: 'var(--text-primary)',
                    }}
                  />
                </div>
                <label style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  cursor: 'pointer',
                }}>
                  <input
                    type="checkbox"
                    checked={taskConfig.vacuum_db !== false}
                    onChange={(e) => setTaskConfig({ ...taskConfig, vacuum_db: e.target.checked })}
                    style={{ accentColor: 'var(--accent-primary)' }}
                  />
                  <span>Run VACUUM after cleanup</span>
                </label>
              </div>
            </div>
          )}

          {/* Error message */}
          {error && (
            <div style={{
              padding: '0.75rem',
              backgroundColor: 'rgba(231, 76, 60, 0.1)',
              border: '1px solid #e74c3c',
              borderRadius: '4px',
              color: '#e74c3c',
              marginBottom: '1rem',
            }}>
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          display: 'flex',
          justifyContent: 'flex-end',
          gap: '0.75rem',
          padding: '1rem 1.5rem',
          borderTop: '1px solid var(--border-color)',
        }}>
          <button
            onClick={onClose}
            style={{
              padding: '0.75rem 1.5rem',
              border: '1px solid var(--border-color)',
              borderRadius: '6px',
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || (scheduleType === 'cron' && !!cronExpression && !cronValidation?.valid)}
            style={{
              padding: '0.75rem 1.5rem',
              border: 'none',
              borderRadius: '6px',
              backgroundColor: 'var(--success)',
              color: 'white',
              cursor: saving ? 'not-allowed' : 'pointer',
              opacity: saving ? 0.6 : 1,
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
            }}
          >
            {saving ? (
              <>
                <span className="material-icons" style={{ fontSize: '16px', animation: 'spin 1s linear infinite' }}>sync</span>
                Saving...
              </>
            ) : (
              <>
                <span className="material-icons" style={{ fontSize: '16px' }}>save</span>
                Save Changes
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
