import { useState, useEffect, useCallback } from 'react';
import * as api from '../services/api';
import type { TaskStatus } from '../services/api';
import type { ChannelGroup } from '../types';
import { logger } from '../utils/logger';
import { TaskEditorModal } from './TaskEditorModal';
import { TaskHistoryPanel } from './TaskHistoryPanel';
import { TaskStatusPill } from './TaskStatusPill';
import { getTaskPillState } from '../utils/taskPillState';
import { useNotifications } from '../contexts/NotificationContext';
import { formatDateTime } from '../utils/formatting';
import '../components/ModalBase.css';

interface ScheduledTasksSectionProps {
  userTimezone?: string;
}

function formatSchedule(task: TaskStatus): { summary: string; details: string[] } {
  // Use new multi-schedule system if schedules are available
  if (task.schedules && task.schedules.length > 0) {
    const enabledSchedules = task.schedules.filter(s => s.enabled);
    if (enabledSchedules.length === 0) {
      return { summary: 'No active schedules', details: [] };
    }
    if (enabledSchedules.length === 1) {
      return {
        summary: enabledSchedules[0].description,
        details: [],
      };
    }
    return {
      summary: `${enabledSchedules.length} schedules active`,
      details: enabledSchedules.map(s => s.description),
    };
  }

  // Fallback to legacy schedule
  const { schedule } = task;
  if (schedule.schedule_type === 'manual') {
    return { summary: 'Manual only', details: [] };
  }
  if (schedule.schedule_type === 'interval' && schedule.interval_seconds > 0) {
    const hours = schedule.interval_seconds / 3600;
    if (hours >= 1) {
      return { summary: `Every ${hours} hour${hours !== 1 ? 's' : ''}`, details: [] };
    }
    const minutes = schedule.interval_seconds / 60;
    return { summary: `Every ${minutes} minute${minutes !== 1 ? 's' : ''}`, details: [] };
  }
  if (schedule.schedule_type === 'cron' && schedule.cron_expression) {
    return { summary: `Cron: ${schedule.cron_expression}`, details: [] };
  }
  if (schedule.schedule_time) {
    return { summary: `Daily at ${schedule.schedule_time}`, details: [] };
  }
  return { summary: 'Not scheduled', details: [] };
}

function TaskCard({ task, onRunNow, onCancel, /* onToggleEnabled - reserved for future use */ onEdit, onFixWontRun, isFixing, isRunning }: {
  task: TaskStatus;
  onRunNow: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  onToggleEnabled: (taskId: string, enabled: boolean) => void;
  onEdit: (task: TaskStatus) => void;
  onFixWontRun: (task: TaskStatus) => void;
  isFixing: boolean;
  isRunning: boolean;
}) {
  const [showHistory, setShowHistory] = useState(false);

  const statusIcon = () => {
    if (isRunning || task.status === 'running') {
      return <span className="material-icons" style={{ color: '#3498db', animation: 'spin 1s linear infinite reverse' }}>sync</span>;
    }
    if (!task.enabled) {
      return <span className="material-icons" style={{ color: 'var(--text-muted)' }}>pause_circle</span>;
    }
    // vkktd.4: keep the card icon coherent with the status pill — an
    // enabled-but-won't-run task must not show a reassuring green check.
    if (getTaskPillState(task) === 'wontRun') {
      return <span className="material-icons" style={{ color: 'var(--warning)' }}>warning</span>;
    }
    return <span className="material-icons" style={{ color: '#2ecc71' }}>check_circle</span>;
  };

  return (
    <div data-testid={`task-card-${task.task_id}`} style={{
      backgroundColor: 'var(--bg-secondary)',
      border: '1px solid var(--border-color)',
      borderRadius: '8px',
      marginBottom: '1rem',
      overflow: 'visible',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '1rem',
        borderBottom: showHistory ? '1px solid var(--border-color)' : 'none',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {statusIcon()}
          <div>
            <div style={{ fontWeight: 600, fontSize: '1rem' }}>{task.task_name}</div>
            <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{task.task_description}</div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {/* Task status pill (vkktd.4) — bound to effective_enabled so a task
              whose child schedules are all disabled reads "Enabled, won't run"
              (amber, with a one-click Fix it) instead of a misleading bare
              "Enabled". See TaskStatusPill for the state derivation. */}
          <TaskStatusPill
            task={task}
            running={isRunning}
            onFixIt={onFixWontRun}
            fixing={isFixing}
          />
          {/* Run Now / Cancel button - hidden for stream_probe */}
          {task.task_id !== 'stream_probe' && (
            (isRunning || task.status === 'running') ? (
              <button
                onClick={() => onCancel(task.task_id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.25rem',
                  padding: '0.5rem 0.75rem',
                  backgroundColor: '#e74c3c',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontSize: '0.85rem',
                }}
              >
                <span className="material-icons" style={{ fontSize: '16px' }}>stop</span>
                Cancel
              </button>
            ) : (
              <button
                onClick={() => onRunNow(task.task_id)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.25rem',
                  padding: '0.5rem 0.75rem',
                  backgroundColor: 'var(--success)',
                  color: 'var(--success-text)',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontSize: '0.85rem',
                }}
              >
                <span className="material-icons" style={{ fontSize: '16px' }}>play_arrow</span>
                Run Now
              </button>
            )
          )}
          {/* Edit button */}
          <button
            onClick={() => onEdit(task)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.25rem',
              padding: '0.5rem 0.75rem',
              backgroundColor: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-color)',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '0.85rem',
            }}
          >
            <span className="material-icons" style={{ fontSize: '16px' }}>edit</span>
            Edit
          </button>
          {/* History toggle */}
          <button
            onClick={() => setShowHistory(!showHistory)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.25rem',
              padding: '0.5rem 0.75rem',
              backgroundColor: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-color)',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '0.85rem',
            }}
          >
            <span className="material-icons" style={{ fontSize: '16px' }}>
              {showHistory ? 'expand_less' : 'expand_more'}
            </span>
            History
          </button>
        </div>
      </div>

      {/* Status info */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
        gap: '1rem',
        padding: '1rem',
        backgroundColor: 'var(--bg-tertiary)',
        fontSize: '0.85rem',
      }}>
        <div>
          <div style={{ color: 'var(--text-muted)', marginBottom: '0.25rem' }}>Schedule</div>
          <div>
            {(() => {
              const scheduleInfo = formatSchedule(task);
              return (
                <div>
                  <div>{scheduleInfo.summary}</div>
                  {scheduleInfo.details.length > 0 && (
                    <div style={{ marginTop: '0.25rem', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                      {scheduleInfo.details.map((detail, i) => (
                        <div key={i}>{detail}</div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })()}
          </div>
        </div>
        <div>
          <div style={{ color: 'var(--text-muted)', marginBottom: '0.25rem' }}>Last Run</div>
          <div>{formatDateTime(task.last_run)}</div>
        </div>
        <div>
          <div style={{ color: 'var(--text-muted)', marginBottom: '0.25rem' }}>Next Run</div>
          <div>{task.enabled ? formatDateTime(task.next_run) : 'Disabled'}</div>
        </div>
        <div>
          <div style={{ color: 'var(--text-muted)', marginBottom: '0.25rem' }}>Status</div>
          <div style={{
            color: task.status === 'running' ? '#3498db' :
                   task.status === 'failed' ? '#e74c3c' :
                   task.enabled ? '#2ecc71' : 'var(--text-muted)',
          }}>
            {task.status.charAt(0).toUpperCase() + task.status.slice(1)}
          </div>
        </div>
      </div>

      {/* History panel */}
      <TaskHistoryPanel taskId={task.task_id} visible={showHistory} />
    </div>
  );
}

// -------------------------------------------------------------------------
// Run Now Dialog — shown for tasks with channel_groups parameter
// -------------------------------------------------------------------------
function RunNowDialog({ task, onRun, onCancel }: {
  task: TaskStatus;
  onRun: (taskId: string, parameters: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const [groups, setGroups] = useState<ChannelGroup[]>([]);
  const [selectedGroups, setSelectedGroups] = useState<number[]>([]);
  const [loadingGroups, setLoadingGroups] = useState(true);

  useEffect(() => {
    api.getChannelGroups().then((allGroups) => {
      const withChannels = allGroups.filter(g => g.channel_count > 0);
      setGroups(withChannels);
      setSelectedGroups(withChannels.map(g => g.id));
      setLoadingGroups(false);
    }).catch(() => setLoadingGroups(false));
  }, []);

  const allSelected = groups.length > 0 && selectedGroups.length === groups.length;
  const noneSelected = selectedGroups.length === 0;

  const toggleGroup = (id: number) => {
    setSelectedGroups(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    );
  };

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-container modal-sm" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="modal-title">Run {task.task_name}</h2>
          <button className="modal-close-btn" onClick={onCancel} aria-label="Close" title="Close">
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </div>
        <div className="modal-body">
          <div className="modal-form-group">
            <label className="modal-form-label">Channel Groups</label>
            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
              <button
                type="button"
                className={`modal-btn modal-btn-${allSelected ? 'primary' : 'secondary'}`}
                style={{ padding: '0.25rem 0.625rem', fontSize: '0.8rem' }}
                onClick={() => setSelectedGroups(groups.map(g => g.id))}
              >
                Select All
              </button>
              <button
                type="button"
                className={`modal-btn modal-btn-${noneSelected ? 'primary' : 'secondary'}`}
                style={{ padding: '0.25rem 0.625rem', fontSize: '0.8rem' }}
                onClick={() => setSelectedGroups([])}
              >
                Select None
              </button>
            </div>
            {loadingGroups ? (
              <div className="modal-loading">Loading groups...</div>
            ) : groups.length === 0 ? (
              <div className="modal-empty-state">No channel groups with channels</div>
            ) : (
              <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
                {groups.map(group => (
                  <label
                    key={group.id}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      padding: '0.4rem 0.5rem',
                      borderRadius: 'var(--radius-md)',
                      cursor: 'pointer',
                      backgroundColor: selectedGroups.includes(group.id) ? 'rgba(100, 108, 255, 0.1)' : 'transparent',
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={selectedGroups.includes(group.id)}
                      onChange={() => toggleGroup(group.id)}
                      style={{ accentColor: 'var(--accent)' }}
                    />
                    <span style={{ flex: 1, fontSize: '0.9rem', color: 'var(--text-primary)' }}>{group.name}</span>
                    <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{group.channel_count} ch</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="modal-footer">
          <button type="button" className="modal-btn modal-btn-secondary" onClick={onCancel}>Cancel</button>
          <button
            type="button"
            className="modal-btn modal-btn-primary"
            disabled={noneSelected || loadingGroups}
            onClick={() => onRun(task.task_id, { channel_groups: selectedGroups })}
          >
            <span className="material-icons">play_arrow</span>
            Run ({selectedGroups.length} group{selectedGroups.length !== 1 ? 's' : ''})
          </button>
        </div>
      </div>
    </div>
  );
}

// Tasks that support the group picker for "Run Now"
const TASKS_WITH_GROUP_PICKER = new Set(['black_screen_scan', 'stream_probe']);

export function ScheduledTasksSection({ userTimezone: _userTimezone }: ScheduledTasksSectionProps) {
  // userTimezone can be used in future for display formatting
  void _userTimezone;
  const [tasks, setTasks] = useState<TaskStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [runningTasks, setRunningTasks] = useState<Set<string>>(new Set());
  const [fixingTasks, setFixingTasks] = useState<Set<string>>(new Set());
  const [editingTask, setEditingTask] = useState<TaskStatus | null>(null);
  // vkktd.4: when Fix-it finds no child schedule to enable, open the editor
  // straight at the Add Schedule sub-editor.
  const [editorOpensAddSchedule, setEditorOpensAddSchedule] = useState(false);
  const [runNowTask, setRunNowTask] = useState<TaskStatus | null>(null);
  const notifications = useNotifications();

  const loadTasks = useCallback(async (showLoading = false) => {
    try {
      if (showLoading) setLoading(true);
      const result = await api.getTasks();
      setTasks(result.tasks);
    } catch (err) {
      logger.error('Failed to load tasks', err);
      notifications.error('Failed to load scheduled tasks', 'Tasks');
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [notifications]);

  useEffect(() => {
    loadTasks(true); // Show loading on initial load only
    // Poll for updates every 5 seconds
    const interval = setInterval(() => loadTasks(false), 5000);
    return () => clearInterval(interval);
  }, [loadTasks]);

  // Check for pending task editor navigation (from NotificationCenter)
  useEffect(() => {
    const pending = sessionStorage.getItem('ecm:open-task-editor');
    if (pending && tasks.length > 0) {
      try {
        const { taskId } = JSON.parse(pending);
        const task = tasks.find(t => t.task_id === taskId);
        if (task) {
          sessionStorage.removeItem('ecm:open-task-editor');
          setEditingTask(task);
        }
      } catch {
        sessionStorage.removeItem('ecm:open-task-editor');
      }
    }
  }, [tasks]);

  const handleRunNow = async (taskId: string) => {
    const task = tasks.find(t => t.task_id === taskId);

    // Show group picker dialog for tasks that support it
    if (task && TASKS_WITH_GROUP_PICKER.has(taskId)) {
      setRunNowTask(task);
      return;
    }

    await executeRunNow(taskId);
  };

  const executeRunNow = async (taskId: string, parameters?: Record<string, unknown>) => {
    const task = tasks.find(t => t.task_id === taskId);
    const taskName = task?.task_name || taskId;

    setRunNowTask(null);
    setRunningTasks((prev) => new Set(prev).add(taskId));
    notifications.info(`Starting ${taskName}...`, 'Task Started');

    try {
      const result = await api.runTask(taskId, undefined, parameters);
      logger.info(`Task ${taskId} completed`, result);

      if (result.error === 'CANCELLED') {
        logger.info(`${taskName} was cancelled (notification handled by cancel handler)`);
      } else if (task?.show_notifications !== false) {
        if (result.success) {
          notifications.success(
            `${taskName} completed: ${result.success_count} succeeded, ${result.failed_count} failed`,
            'Task Completed'
          );
        } else {
          notifications.error(
            result.message || `${taskName} failed`,
            'Task Failed'
          );
        }
      }

      await loadTasks();
    } catch (err) {
      logger.error(`Failed to run task ${taskId}`, err);
      notifications.error(
        `Failed to run ${taskName}: ${err instanceof Error ? err.message : 'Unknown error'}`,
        'Task Error'
      );
    } finally {
      setRunningTasks((prev) => {
        const next = new Set(prev);
        next.delete(taskId);
        return next;
      });
    }
  };

  const handleCancel = async (taskId: string) => {
    const task = tasks.find(t => t.task_id === taskId);
    const taskName = task?.task_name || taskId;

    try {
      const result = await api.cancelTask(taskId);
      logger.info(`Task ${taskId} cancel requested`, result);

      if (result.status === 'cancelling') {
        // Poll for task completion to show detailed result
        // Don't show initial toast - wait for the detailed result
        let attempts = 0;
        const maxAttempts = 30; // 30 seconds max wait
        const pollInterval = 1000; // 1 second
        let notificationShown = false; // Prevent duplicate notifications

        const pollForCompletion = async () => {
          if (notificationShown) return; // Already showed notification
          attempts++;
          try {
            const taskStatus = await api.getTask(taskId);
            if (taskStatus.status !== 'running' && taskStatus.status !== 'scheduled') {
              // Task has stopped - check history for the result
              const history = await api.getTaskHistory(taskId, 1);
              if (history.history.length > 0 && !notificationShown) {
                const lastExecution = history.history[0];
                if (lastExecution.status === 'cancelled' || lastExecution.error === 'CANCELLED') {
                  notificationShown = true;
                  notifications.info(
                    `${taskName} was cancelled. ${lastExecution.success_count} items completed before cancellation` +
                    (lastExecution.failed_count > 0 ? `, ${lastExecution.failed_count} failed` : '') +
                    ` (out of ${lastExecution.total_items} total)`,
                    'Task Cancelled'
                  );
                }
              }
              await loadTasks();
              return;
            }
            // Still running, poll again
            if (attempts < maxAttempts) {
              setTimeout(pollForCompletion, pollInterval);
            } else {
              if (!notificationShown) {
                notificationShown = true;
                notifications.info(`${taskName} cancellation in progress`, 'Task Cancelling');
              }
              await loadTasks();
            }
          } catch (pollErr) {
            logger.error('Error polling for task completion', pollErr);
            await loadTasks();
          }
        };

        // Start polling after a brief delay
        setTimeout(pollForCompletion, pollInterval);
      } else {
        // Task wasn't running or other status
        notifications.info(result.message || `${taskName} cancelled`, 'Task Cancelled');
        await loadTasks();
      }
    } catch (err) {
      logger.error(`Failed to cancel task ${taskId}`, err);
      notifications.error(
        `Failed to cancel ${taskName}: ${err instanceof Error ? err.message : 'Unknown error'}`,
        'Cancel Error'
      );
    } finally {
      // Remove from running tasks set since it's cancelled
      setRunningTasks((prev) => {
        const next = new Set(prev);
        next.delete(taskId);
        return next;
      });
    }
  };

  const handleToggleEnabled = async (taskId: string, enabled: boolean) => {
    try {
      await api.updateTask(taskId, { enabled });
      await loadTasks();
    } catch (err) {
      logger.error(`Failed to update task ${taskId}`, err);
    }
  };

  // vkktd.4: one-click fix for the "Enabled, won't run" state — enable the
  // disabled child schedule (newest first, mirroring the backend reconcile's
  // determinism). With no schedule to enable, open the editor at Add Schedule.
  const handleFixWontRun = async (task: TaskStatus) => {
    const disabledSchedules = (task.schedules || []).filter(s => !s.enabled);
    if (disabledSchedules.length === 0) {
      // Nothing to flip (zero schedules, or enabled ones that can't compute a
      // next run) — send the operator to the editor to sort it out.
      setEditorOpensAddSchedule((task.schedules || []).length === 0);
      setEditingTask(task);
      return;
    }

    const target = disabledSchedules.reduce((newest, s) => {
      const a = newest.created_at || '';
      const b = s.created_at || '';
      if (b > a) return s;
      if (b === a && s.id > newest.id) return s;
      return newest;
    });

    setFixingTasks(prev => new Set(prev).add(task.task_id));
    try {
      await api.updateTaskSchedule(task.task_id, target.id, { enabled: true });
      notifications.success(
        `Enabled the "${target.name || target.description}" schedule — ${task.task_name} will now run automatically`,
        'Schedule Enabled'
      );
      await loadTasks();
    } catch (err) {
      logger.error(`Failed to enable schedule for task ${task.task_id}`, err);
      notifications.error(
        `Failed to enable schedule for ${task.task_name}: ${err instanceof Error ? err.message : 'Unknown error'}`,
        'Fix Failed'
      );
    } finally {
      setFixingTasks(prev => {
        const next = new Set(prev);
        next.delete(task.task_id);
        return next;
      });
    }
  };

  if (loading) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
        Loading scheduled tasks...
      </div>
    );
  }

  return (
    <div>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: '1.5rem',
      }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.5rem', fontWeight: 600, color: 'var(--text-primary)' }}>Scheduled Tasks</h2>
          <p style={{ margin: '0.5rem 0 0 0', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
            Manage automated tasks like EPG refresh, M3U refresh, and database cleanup
          </p>
        </div>
        <button
          onClick={() => { loadTasks(); }}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.25rem',
            padding: '0.5rem 0.75rem',
            backgroundColor: 'var(--bg-tertiary)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-color)',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '0.85rem',
          }}
        >
          <span className="material-icons" style={{ fontSize: '16px' }}>refresh</span>
          Refresh
        </button>
      </div>

      {tasks.length === 0 ? (
        <div style={{
          padding: '3rem',
          textAlign: 'center',
          color: 'var(--text-secondary)',
          backgroundColor: 'var(--bg-secondary)',
          borderRadius: '8px',
        }}>
          <span className="material-icons" style={{ fontSize: '48px', marginBottom: '1rem', display: 'block' }}>
            schedule
          </span>
          No scheduled tasks available
        </div>
      ) : (
        tasks.map((task) => (
          <TaskCard
            key={task.task_id}
            task={task}
            onRunNow={handleRunNow}
            onCancel={handleCancel}
            onToggleEnabled={handleToggleEnabled}
            onEdit={setEditingTask}
            onFixWontRun={handleFixWontRun}
            isFixing={fixingTasks.has(task.task_id)}
            isRunning={runningTasks.has(task.task_id)}
          />
        ))
      )}

      {/* Task Editor Modal */}
      {editingTask && (
        <TaskEditorModal
          task={editingTask}
          openAddSchedule={editorOpensAddSchedule}
          onClose={() => { setEditingTask(null); setEditorOpensAddSchedule(false); }}
          onSaved={loadTasks}
        />
      )}

      {/* Run Now Group Picker Dialog */}
      {runNowTask && (
        <RunNowDialog
          task={runNowTask}
          onRun={executeRunNow}
          onCancel={() => setRunNowTask(null)}
        />
      )}
    </div>
  );
}
