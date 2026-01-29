import { useState, useEffect, useCallback, useMemo } from 'react';
import * as api from '../services/api';
import type { TaskStatus, TaskSchedule, TaskScheduleCreate, TaskScheduleUpdate, TaskParameterSchema, SettingsResponse } from '../services/api';
import type { EPGSource, M3UAccount, ChannelGroup } from '../types';
import { logger } from '../utils/logger';
import { ScheduleEditor } from './ScheduleEditor';
import { useNotifications } from '../contexts/NotificationContext';
import './ModalBase.css';
import './TaskEditorModal.css';

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

  // EPG/M3U/Channel Group data for task-specific config and schedule parameters
  const [epgSources, setEpgSources] = useState<EPGSource[]>([]);
  const [m3uAccounts, setM3uAccounts] = useState<M3UAccount[]>([]);
  const [channelGroups, setChannelGroups] = useState<ChannelGroup[]>([]);

  // Settings for default parameter values (stream_probe)
  const [settings, setSettings] = useState<SettingsResponse | null>(null);

  // Parameter schema for schedule parameters
  const [parameterSchema, setParameterSchema] = useState<TaskParameterSchema[]>([]);

  // UI state
  const [saving, setSaving] = useState(false);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [runningSchedules, setRunningSchedules] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const notifications = useNotifications();

  // Load data for task-specific config and schedule parameters
  useEffect(() => {
    async function loadData() {
      try {
        // Load parameter schema for this task
        const schemaResponse = await api.getTaskParameterSchema(task.task_id);
        setParameterSchema(schemaResponse.parameters || []);

        // Load task-specific data based on task type
        if (task.task_id === 'epg_refresh') {
          const sources = await api.getEPGSources();
          setEpgSources(sources);
        } else if (task.task_id === 'm3u_refresh') {
          const accounts = await api.getM3UAccounts();
          setM3uAccounts(accounts);
        } else if (task.task_id === 'stream_probe') {
          // Load channel groups and settings for defaults
          const [groups, settingsData] = await Promise.all([
            api.getChannelGroups(),
            api.getSettings(),
          ]);
          setChannelGroups(groups);
          setSettings(settingsData);
        }
      } catch (err) {
        logger.error('Failed to load data for task config', err);
      }
    }
    loadData();
  }, [task.task_id]);

  // Build parameter options for ScheduleEditor based on loaded data
  const parameterOptions = useMemo(() => {
    const options: Record<string, { value: string | number; label: string; badge?: string }[]> = {};

    // Channel groups (for stream_probe) - only groups with channels
    const groupsWithChannels = channelGroups.filter(g => g.channel_count > 0);
    if (groupsWithChannels.length > 0) {
      options['channel_groups'] = groupsWithChannels.map(g => ({
        value: g.name,
        label: `${g.name} (${g.channel_count})`,
        badge: g.is_auto_sync ? 'auto' : undefined,
      }));
    }

    // M3U accounts (for m3u_refresh) - exclude "custom" account
    const filteredM3uAccounts = m3uAccounts.filter(a => a.name.toLowerCase() !== 'custom');
    if (filteredM3uAccounts.length > 0) {
      options['m3u_accounts'] = filteredM3uAccounts.map(a => ({
        value: a.id,
        label: a.name,
      }));
    }

    // EPG sources (for epg_refresh)
    if (epgSources.length > 0) {
      options['epg_sources'] = epgSources.map(s => ({
        value: s.id,
        label: s.name,
      }));
    }

    return options;
  }, [channelGroups, m3uAccounts, epgSources]);

  // Compute default parameters for new schedules
  const defaultParameters = useMemo(() => {
    const defaults: Record<string, unknown> = {};

    // For stream_probe: default to all non-auto-sync groups with channels
    // and use settings for batch_size, timeout, max_concurrent
    if (task.task_id === 'stream_probe') {
      const nonAutoGroups = channelGroups
        .filter(g => g.channel_count > 0 && !g.is_auto_sync)
        .map(g => g.name);
      if (nonAutoGroups.length > 0) {
        defaults['channel_groups'] = nonAutoGroups;
      }

      // Use settings values as defaults for numeric parameters
      if (settings) {
        defaults['batch_size'] = settings.stream_probe_batch_size;
        defaults['timeout'] = settings.stream_probe_timeout;
        defaults['max_concurrent'] = settings.max_concurrent_probes;
      }
    }

    return defaults;
  }, [task.task_id, channelGroups, settings]);

  // Refresh schedules from server
  const refreshSchedules = useCallback(async () => {
    try {
      const result = await api.getTaskSchedules(task.task_id);
      setSchedules(result.schedules);
    } catch (err) {
      logger.error('Failed to refresh schedules', err);
    }
  }, [task.task_id]);

  // Load schedules when modal opens (component mounts)
  useEffect(() => {
    refreshSchedules();
  }, [refreshSchedules]);

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

  // Run schedule now (for stream_probe)
  const handleRunSchedule = async (schedule: TaskSchedule) => {
    const scheduleName = schedule.name || schedule.description;
    setRunningSchedules((prev) => new Set(prev).add(schedule.id));
    notifications.info(`Starting ${task.task_name} with "${scheduleName}" settings...`, 'Task Started');

    try {
      const result = await api.runTask(task.task_id, schedule.id);
      logger.info(`Task ${task.task_id} with schedule ${schedule.id} completed`, result);

      if (result.error === 'CANCELLED') {
        // Task was cancelled
        notifications.info(
          `${task.task_name} was cancelled. ${result.success_count} items completed before cancellation`,
          'Task Cancelled'
        );
      } else if (result.success) {
        notifications.success(
          `${task.task_name} completed: ${result.success_count} succeeded, ${result.failed_count} failed`,
          'Task Completed'
        );
      } else {
        notifications.error(
          result.message || `${task.task_name} failed`,
          'Task Failed'
        );
      }

      await refreshSchedules();
      onSaved();
    } catch (err) {
      logger.error(`Failed to run schedule ${schedule.id}`, err);
      notifications.error(
        `Failed to run ${task.task_name}: ${err instanceof Error ? err.message : 'Unknown error'}`,
        'Task Error'
      );
    } finally {
      setRunningSchedules((prev) => {
        const next = new Set(prev);
        next.delete(schedule.id);
        return next;
      });
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
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-container modal-md task-editor-modal" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="modal-header">
          <div>
            <h2>Configure Task</h2>
            <div className="modal-subtitle">{task.task_name}</div>
          </div>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        {/* Content */}
        <div className="modal-body">
          {/* Task Description */}
          <div className="task-description">
            {task.task_description}
          </div>

          {/* Enable/Disable Task */}
          <div className="enable-section">
            <label className="enable-label">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <span>Enable task</span>
            </label>
            <div className="enable-hint">
              When disabled, no schedules will run for this task.
            </div>
          </div>

          {/* Schedules Section */}
          <div className="schedules-section">
            <div className="schedules-header">
              <label>Schedules</label>
              <button className="add-schedule-btn" onClick={() => setIsAddingSchedule(true)}>
                <span className="material-icons">add</span>
                Add Schedule
              </button>
            </div>

            {schedules.length === 0 ? (
              <div className="empty-schedules">
                <span className="material-icons">event_busy</span>
                No schedules configured.
                <br />
                <span className="hint">
                  Click "Add Schedule" to create one, or run the task manually.
                </span>
              </div>
            ) : (
              <div className="schedule-list">
                {schedules.map((schedule) => (
                  <div
                    key={schedule.id}
                    className={`schedule-item ${!schedule.enabled ? 'disabled' : ''}`}
                  >
                    {/* Enable toggle */}
                    <input
                      type="checkbox"
                      checked={schedule.enabled}
                      onChange={() => handleToggleSchedule(schedule)}
                    />

                    {/* Schedule info */}
                    <div className="schedule-info">
                      <div className="schedule-name">
                        {schedule.name || schedule.description}
                      </div>
                      {schedule.name && (
                        <div className="schedule-description">
                          {schedule.description}
                        </div>
                      )}
                      {schedule.enabled && schedule.next_run_at && (
                        <div className="schedule-next-run">
                          Next: {formatNextRun(schedule.next_run_at)}
                        </div>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="schedule-actions">
                      {/* Run Now button - only for stream_probe */}
                      {task.task_id === 'stream_probe' && (
                        <button
                          className={`schedule-action-btn run ${runningSchedules.has(schedule.id) ? 'running' : ''}`}
                          onClick={() => handleRunSchedule(schedule)}
                          disabled={runningSchedules.has(schedule.id) || runningSchedules.size > 0}
                          title={runningSchedules.has(schedule.id) ? 'Running...' : 'Run now with this schedule\'s settings'}
                        >
                          <span className="material-icons" style={runningSchedules.has(schedule.id) ? { animation: 'spin 1s linear infinite reverse' } : undefined}>
                            {runningSchedules.has(schedule.id) ? 'sync' : 'play_arrow'}
                          </span>
                        </button>
                      )}
                      <button
                        className="schedule-action-btn"
                        onClick={() => setEditingSchedule(schedule)}
                        title="Edit schedule"
                      >
                        <span className="material-icons">edit</span>
                      </button>
                      <button
                        className="schedule-action-btn delete"
                        onClick={() => handleDeleteSchedule(schedule)}
                        title="Delete schedule"
                      >
                        <span className="material-icons">delete</span>
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Task-Specific Configuration: Cleanup */}
          {task.task_id === 'cleanup' && (
            <div className="config-section">
              <label className="section-label">Retention Settings</label>
              <div className="retention-grid">
                <div className="retention-item">
                  <label>Probe history retention (days)</label>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={(taskConfig.probe_history_days as number) || 30}
                    onChange={(e) => setTaskConfig({ ...taskConfig, probe_history_days: parseInt(e.target.value) || 30 })}
                  />
                </div>
                <div className="retention-item">
                  <label>Task history retention (days)</label>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={(taskConfig.task_history_days as number) || 30}
                    onChange={(e) => setTaskConfig({ ...taskConfig, task_history_days: parseInt(e.target.value) || 30 })}
                  />
                </div>
                <div className="retention-item">
                  <label>Journal retention (days)</label>
                  <input
                    type="number"
                    min={1}
                    max={365}
                    value={(taskConfig.journal_days as number) || 30}
                    onChange={(e) => setTaskConfig({ ...taskConfig, journal_days: parseInt(e.target.value) || 30 })}
                  />
                </div>
                <label className="config-checkbox">
                  <input
                    type="checkbox"
                    checked={taskConfig.vacuum_db !== false}
                    onChange={(e) => setTaskConfig({ ...taskConfig, vacuum_db: e.target.checked })}
                  />
                  <span>Compact database after cleanup</span>
                </label>
              </div>
            </div>
          )}

          {/* Error message */}
          {error && (
            <div className="modal-error-banner">
              <span className="material-icons">error</span>
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="modal-footer">
          <button
            className="modal-btn modal-btn-primary"
            onClick={handleSaveTask}
            disabled={saving}
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </div>

      {/* Schedule Editor Modal (Add) */}
      {isAddingSchedule && (
        <div
          className="modal-overlay schedule-editor-modal"
          style={{ zIndex: 1001 }}
          onClick={(e) => e.target === e.currentTarget && setIsAddingSchedule(false)}
        >
          <div className="modal-container modal-sm" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Add Schedule</h2>
              <button className="modal-close-btn" onClick={() => setIsAddingSchedule(false)}>
                <span className="material-icons">close</span>
              </button>
            </div>
            <ScheduleEditor
              onSave={handleAddSchedule}
              onCancel={() => setIsAddingSchedule(false)}
              saving={savingSchedule}
              taskId={task.task_id}
              parameterSchema={task.task_id === 'cleanup' ? [] : parameterSchema}
              parameterOptions={parameterOptions}
              defaultParameters={defaultParameters}
            />
          </div>
        </div>
      )}

      {/* Schedule Editor Modal (Edit) */}
      {editingSchedule && (
        <div
          className="modal-overlay schedule-editor-modal"
          style={{ zIndex: 1001 }}
          onClick={(e) => e.target === e.currentTarget && setEditingSchedule(null)}
        >
          <div className="modal-container modal-sm" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Edit Schedule</h2>
              <button className="modal-close-btn" onClick={() => setEditingSchedule(null)}>
                <span className="material-icons">close</span>
              </button>
            </div>
            <ScheduleEditor
              schedule={editingSchedule}
              onSave={handleUpdateSchedule}
              onCancel={() => setEditingSchedule(null)}
              saving={savingSchedule}
              taskId={task.task_id}
              parameterSchema={task.task_id === 'cleanup' ? [] : parameterSchema}
              parameterOptions={parameterOptions}
            />
          </div>
        </div>
      )}
    </div>
  );
}
