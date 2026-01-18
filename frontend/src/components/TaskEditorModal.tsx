import { useState, useEffect, useCallback } from 'react';
import * as api from '../services/api';
import type { TaskStatus, TaskSchedule, TaskScheduleCreate, TaskScheduleUpdate } from '../services/api';
import type { EPGSource, M3UAccount } from '../types';
import { logger } from '../utils/logger';
import { ScheduleEditor } from './ScheduleEditor';

interface TaskEditorModalProps {
  task: TaskStatus;
  onClose: () => void;
  onSaved: () => void;
}

export function TaskEditorModal({ task, onClose, onSaved }: TaskEditorModalProps) {
  // Task state
  const [enabled, setEnabled] = useState(task.enabled);
  const [taskConfig, setTaskConfig] = useState<Record<string, unknown>>(task.config || {});

  // Schedules state
  const [schedules, setSchedules] = useState<TaskSchedule[]>(task.schedules || []);
  const [editingSchedule, setEditingSchedule] = useState<TaskSchedule | null>(null);
  const [isAddingSchedule, setIsAddingSchedule] = useState(false);

  // EPG/M3U data for task-specific config
  const [epgSources, setEpgSources] = useState<EPGSource[]>([]);
  const [m3uAccounts, setM3uAccounts] = useState<M3UAccount[]>([]);

  // UI state
  const [saving, setSaving] = useState(false);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  // Refresh schedules from server
  const refreshSchedules = useCallback(async () => {
    try {
      const result = await api.getTaskSchedules(task.task_id);
      setSchedules(result.schedules);
    } catch (err) {
      logger.error('Failed to refresh schedules', err);
    }
  }, [task.task_id]);

  // Save task-level settings (enabled, config)
  const handleSaveTask = async () => {
    setError(null);
    setSaving(true);

    try {
      const config: api.TaskConfigUpdate = {
        enabled,
      };

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

  // Create new schedule
  const handleAddSchedule = async (data: TaskScheduleCreate) => {
    setSavingSchedule(true);
    try {
      await api.createTaskSchedule(task.task_id, data);
      await refreshSchedules();
      setIsAddingSchedule(false);
      onSaved();
    } catch (err) {
      logger.error('Failed to create schedule', err);
      throw err;
    } finally {
      setSavingSchedule(false);
    }
  };

  // Update existing schedule
  const handleUpdateSchedule = async (data: TaskScheduleUpdate) => {
    if (!editingSchedule) return;
    setSavingSchedule(true);
    try {
      await api.updateTaskSchedule(task.task_id, editingSchedule.id, data);
      await refreshSchedules();
      setEditingSchedule(null);
      onSaved();
    } catch (err) {
      logger.error('Failed to update schedule', err);
      throw err;
    } finally {
      setSavingSchedule(false);
    }
  };

  // Toggle schedule enabled/disabled
  const handleToggleSchedule = async (schedule: TaskSchedule) => {
    try {
      await api.updateTaskSchedule(task.task_id, schedule.id, {
        enabled: !schedule.enabled,
      });
      await refreshSchedules();
      onSaved();
    } catch (err) {
      logger.error('Failed to toggle schedule', err);
    }
  };

  // Delete schedule
  const handleDeleteSchedule = async (schedule: TaskSchedule) => {
    if (!confirm(`Delete schedule "${schedule.name || schedule.description}"?`)) return;
    try {
      await api.deleteTaskSchedule(task.task_id, schedule.id);
      await refreshSchedules();
      onSaved();
    } catch (err) {
      logger.error('Failed to delete schedule', err);
    }
  };

  // Format next run time
  const formatNextRun = (nextRunAt: string | null) => {
    if (!nextRunAt) return 'Not scheduled';
    const date = new Date(nextRunAt);
    const now = new Date();
    const diffMs = date.getTime() - now.getTime();
    const diffMins = Math.round(diffMs / 60000);
    const diffHours = Math.round(diffMs / 3600000);
    const diffDays = Math.round(diffMs / 86400000);

    if (diffMs < 0) return 'Overdue';
    if (diffMins < 60) return `in ${diffMins}m`;
    if (diffHours < 24) return `in ${diffHours}h`;
    return `in ${diffDays}d`;
  };

  return (
    <div
      style={{
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
      }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        style={{
          backgroundColor: 'var(--bg-primary)',
          border: '1px solid var(--border-color)',
          borderRadius: '8px',
          width: '100%',
          maxWidth: '600px',
          maxHeight: '90vh',
          overflow: 'auto',
        }}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '1rem 1.5rem',
            borderBottom: '1px solid var(--border-color)',
          }}
        >
          <div>
            <h2 style={{ margin: 0, fontSize: '1.25rem' }}>Configure Task</h2>
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
          <div
            style={{
              backgroundColor: 'var(--bg-secondary)',
              padding: '1rem',
              borderRadius: '6px',
              marginBottom: '1.5rem',
              fontSize: '0.9rem',
              color: 'var(--text-secondary)',
            }}
          >
            {task.task_description}
          </div>

          {/* Enable/Disable Task */}
          <div style={{ marginBottom: '1.5rem' }}>
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.75rem',
                cursor: 'pointer',
              }}
            >
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
              <span style={{ fontWeight: 500 }}>Enable task</span>
            </label>
            <div style={{ marginLeft: '2rem', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
              When disabled, no schedules will run for this task.
            </div>
          </div>

          {/* Schedules Section */}
          <div style={{ marginBottom: '1.5rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
              <label style={{ fontWeight: 500 }}>Schedules</label>
              <button
                onClick={() => setIsAddingSchedule(true)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.25rem',
                  padding: '0.5rem 0.75rem',
                  border: 'none',
                  borderRadius: '4px',
                  backgroundColor: 'var(--accent-primary)',
                  color: 'var(--button-primary-text)',
                  cursor: 'pointer',
                  fontSize: '0.85rem',
                }}
              >
                <span className="material-icons" style={{ fontSize: '16px' }}>add</span>
                Add Schedule
              </button>
            </div>

            {schedules.length === 0 ? (
              <div
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  padding: '2rem',
                  borderRadius: '6px',
                  textAlign: 'center',
                  color: 'var(--text-muted)',
                }}
              >
                <span className="material-icons" style={{ fontSize: '32px', marginBottom: '0.5rem', display: 'block' }}>
                  event_busy
                </span>
                No schedules configured.
                <br />
                <span style={{ fontSize: '0.85rem' }}>
                  Click "Add Schedule" to create one, or run the task manually.
                </span>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {schedules.map((schedule) => (
                  <div
                    key={schedule.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.75rem',
                      padding: '0.75rem 1rem',
                      backgroundColor: 'var(--bg-secondary)',
                      borderRadius: '6px',
                      border: `1px solid ${schedule.enabled ? 'transparent' : 'var(--border-color)'}`,
                      opacity: schedule.enabled ? 1 : 0.6,
                    }}
                  >
                    {/* Enable toggle */}
                    <input
                      type="checkbox"
                      checked={schedule.enabled}
                      onChange={() => handleToggleSchedule(schedule)}
                      style={{
                        width: '16px',
                        height: '16px',
                        accentColor: 'var(--success)',
                      }}
                    />

                    {/* Schedule info */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 500, fontSize: '0.9rem' }}>
                        {schedule.name || schedule.description}
                      </div>
                      {schedule.name && (
                        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                          {schedule.description}
                        </div>
                      )}
                      {schedule.enabled && schedule.next_run_at && (
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                          Next: {formatNextRun(schedule.next_run_at)}
                        </div>
                      )}
                    </div>

                    {/* Actions */}
                    <button
                      onClick={() => setEditingSchedule(schedule)}
                      style={{
                        background: 'none',
                        border: 'none',
                        cursor: 'pointer',
                        padding: '0.25rem',
                        color: 'var(--text-secondary)',
                      }}
                      title="Edit schedule"
                    >
                      <span className="material-icons" style={{ fontSize: '18px' }}>edit</span>
                    </button>
                    <button
                      onClick={() => handleDeleteSchedule(schedule)}
                      style={{
                        background: 'none',
                        border: 'none',
                        cursor: 'pointer',
                        padding: '0.25rem',
                        color: 'var(--error)',
                      }}
                      title="Delete schedule"
                    >
                      <span className="material-icons" style={{ fontSize: '18px' }}>delete</span>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Task-Specific Configuration */}
          {task.task_id === 'epg_refresh' && epgSources.length > 0 && (
            <div
              style={{
                backgroundColor: 'var(--bg-secondary)',
                padding: '1rem',
                borderRadius: '6px',
                marginBottom: '1.5rem',
              }}
            >
              <label style={{ display: 'block', marginBottom: '0.75rem', fontWeight: 500 }}>
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
                          setTaskConfig({ ...taskConfig, source_ids: currentIds.filter((id) => id !== source.id) });
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
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.75rem', cursor: 'pointer' }}>
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
            <div
              style={{
                backgroundColor: 'var(--bg-secondary)',
                padding: '1rem',
                borderRadius: '6px',
                marginBottom: '1.5rem',
              }}
            >
              <label style={{ display: 'block', marginBottom: '0.75rem', fontWeight: 500 }}>
                M3U Accounts to Refresh
              </label>
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                Select specific accounts or leave empty to refresh all active accounts.
              </div>
              <div style={{ maxHeight: '200px', overflowY: 'auto' }}>
                {m3uAccounts
                  .filter((account) => account.name !== 'Custom')
                  .map((account) => (
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
                            setTaskConfig({ ...taskConfig, account_ids: currentIds.filter((id) => id !== account.id) });
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
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.75rem', cursor: 'pointer' }}>
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
            <div
              style={{
                backgroundColor: 'var(--bg-secondary)',
                padding: '1rem',
                borderRadius: '6px',
                marginBottom: '1.5rem',
              }}
            >
              <label style={{ display: 'block', marginBottom: '0.75rem', fontWeight: 500 }}>
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
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={taskConfig.vacuum_db !== false}
                    onChange={(e) => setTaskConfig({ ...taskConfig, vacuum_db: e.target.checked })}
                    style={{ accentColor: 'var(--accent-primary)' }}
                  />
                  <span>Compact database after cleanup</span>
                </label>
              </div>
            </div>
          )}

          {/* Error message */}
          {error && (
            <div
              style={{
                padding: '0.75rem',
                backgroundColor: 'rgba(231, 76, 60, 0.1)',
                border: '1px solid #e74c3c',
                borderRadius: '4px',
                color: '#e74c3c',
                marginBottom: '1rem',
              }}
            >
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: '0.75rem',
            padding: '1rem 1.5rem',
            borderTop: '1px solid var(--border-color)',
          }}
        >
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
            onClick={handleSaveTask}
            disabled={saving}
            style={{
              padding: '0.75rem 1.5rem',
              border: 'none',
              borderRadius: '6px',
              backgroundColor: 'var(--success)',
              color: 'var(--success-text)',
              cursor: saving ? 'not-allowed' : 'pointer',
              opacity: saving ? 0.6 : 1,
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
            }}
          >
            {saving ? (
              <>
                <span className="material-icons" style={{ fontSize: '16px', animation: 'spin 1s linear infinite' }}>
                  sync
                </span>
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

      {/* Schedule Editor Modal (Add) */}
      {isAddingSchedule && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1001,
          }}
          onClick={(e) => e.target === e.currentTarget && setIsAddingSchedule(false)}
        >
          <div
            style={{
              backgroundColor: 'var(--bg-primary)',
              border: '1px solid var(--border-color)',
              borderRadius: '8px',
              width: '100%',
              maxWidth: '500px',
              maxHeight: '90vh',
              overflow: 'auto',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '1rem 1.5rem',
                borderBottom: '1px solid var(--border-color)',
              }}
            >
              <h3 style={{ margin: 0 }}>Add Schedule</h3>
              <button
                onClick={() => setIsAddingSchedule(false)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}
              >
                <span className="material-icons">close</span>
              </button>
            </div>
            <ScheduleEditor
              onSave={handleAddSchedule}
              onCancel={() => setIsAddingSchedule(false)}
              saving={savingSchedule}
            />
          </div>
        </div>
      )}

      {/* Schedule Editor Modal (Edit) */}
      {editingSchedule && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1001,
          }}
          onClick={(e) => e.target === e.currentTarget && setEditingSchedule(null)}
        >
          <div
            style={{
              backgroundColor: 'var(--bg-primary)',
              border: '1px solid var(--border-color)',
              borderRadius: '8px',
              width: '100%',
              maxWidth: '500px',
              maxHeight: '90vh',
              overflow: 'auto',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '1rem 1.5rem',
                borderBottom: '1px solid var(--border-color)',
              }}
            >
              <h3 style={{ margin: 0 }}>Edit Schedule</h3>
              <button
                onClick={() => setEditingSchedule(null)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}
              >
                <span className="material-icons">close</span>
              </button>
            </div>
            <ScheduleEditor
              schedule={editingSchedule}
              onSave={handleUpdateSchedule}
              onCancel={() => setEditingSchedule(null)}
              saving={savingSchedule}
            />
          </div>
        </div>
      )}
    </div>
  );
}
