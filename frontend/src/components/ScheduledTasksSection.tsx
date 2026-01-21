import { useState, useEffect, useCallback } from 'react';
import * as api from '../services/api';
import type { TaskStatus } from '../services/api';
import { logger } from '../utils/logger';
import { TaskEditorModal } from './TaskEditorModal';
import { TaskHistoryPanel } from './TaskHistoryPanel';
import { useNotifications } from '../contexts/NotificationContext';

interface ScheduledTasksSectionProps {
  userTimezone?: string;
}

function formatDateTime(isoString: string | null): string {
  if (!isoString) return 'Never';
  const date = new Date(isoString);
  return date.toLocaleString();
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

function TaskCard({ task, onRunNow, onCancel, onToggleEnabled, onEdit, isRunning }: {
  task: TaskStatus;
  onRunNow: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  onToggleEnabled: (taskId: string, enabled: boolean) => void;
  onEdit: (task: TaskStatus) => void;
  isRunning: boolean;
}) {
  const [showHistory, setShowHistory] = useState(false);

  const statusIcon = () => {
    if (isRunning || task.status === 'running') {
      return <span className="material-icons" style={{ color: '#3498db', animation: 'spin 1s linear infinite' }}>sync</span>;
    }
    if (!task.enabled) {
      return <span className="material-icons" style={{ color: 'var(--text-muted)' }}>pause_circle</span>;
    }
    return <span className="material-icons" style={{ color: '#2ecc71' }}>check_circle</span>;
  };

  return (
    <div style={{
      backgroundColor: 'var(--bg-secondary)',
      border: '1px solid var(--border-color)',
      borderRadius: '8px',
      marginBottom: '1rem',
      overflow: 'hidden',
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
          {/* Enable/Disable toggle */}
          <label style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.5rem',
            cursor: 'pointer',
            fontSize: '0.85rem',
            color: 'var(--text-secondary)',
          }}>
            <input
              type="checkbox"
              checked={task.enabled}
              onChange={(e) => onToggleEnabled(task.task_id, e.target.checked)}
              style={{ accentColor: 'var(--accent-primary)' }}
            />
            Enabled
          </label>
          {/* Run Now / Cancel button */}
          {(isRunning || task.status === 'running') ? (
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
        gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
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

      {/* Progress bar when running */}
      {(isRunning || task.status === 'running') && task.progress.total > 0 && (
        <div style={{ padding: '0 1rem 1rem 1rem' }}>
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            marginBottom: '0.5rem',
            fontSize: '0.85rem',
          }}>
            <span>{task.progress.current_item || 'Processing...'}</span>
            <span>{task.progress.current} / {task.progress.total} ({task.progress.percentage}%)</span>
          </div>
          <div style={{
            height: '6px',
            backgroundColor: 'var(--bg-tertiary)',
            borderRadius: '3px',
            overflow: 'hidden',
          }}>
            <div style={{
              width: `${task.progress.percentage}%`,
              height: '100%',
              backgroundColor: '#3498db',
              transition: 'width 0.3s ease',
            }} />
          </div>
          <div style={{
            display: 'flex',
            gap: '1rem',
            marginTop: '0.5rem',
            fontSize: '0.8rem',
          }}>
            <span style={{ color: '#2ecc71' }}>Success: {task.progress.success_count}</span>
            <span style={{ color: '#e74c3c' }}>Failed: {task.progress.failed_count}</span>
            {task.progress.skipped_count > 0 && (
              <span style={{ color: '#f39c12' }}>Skipped: {task.progress.skipped_count}</span>
            )}
          </div>
        </div>
      )}

      {/* History panel */}
      <TaskHistoryPanel taskId={task.task_id} visible={showHistory} />
    </div>
  );
}

export function ScheduledTasksSection({ userTimezone: _userTimezone }: ScheduledTasksSectionProps) {
  // userTimezone can be used in future for display formatting
  void _userTimezone;
  const [tasks, setTasks] = useState<TaskStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [runningTasks, setRunningTasks] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [editingTask, setEditingTask] = useState<TaskStatus | null>(null);
  const notifications = useNotifications();

  const loadTasks = useCallback(async () => {
    try {
      const result = await api.getTasks();
      setTasks(result.tasks);
      setError(null);
    } catch (err) {
      logger.error('Failed to load tasks', err);
      setError('Failed to load scheduled tasks');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTasks();
    // Poll for updates every 5 seconds
    const interval = setInterval(loadTasks, 5000);
    return () => clearInterval(interval);
  }, [loadTasks]);

  const handleRunNow = async (taskId: string) => {
    // Find task name for better feedback
    const task = tasks.find(t => t.task_id === taskId);
    const taskName = task?.task_name || taskId;

    setRunningTasks((prev) => new Set(prev).add(taskId));
    notifications.info(`Starting ${taskName}...`, 'Task Started');

    try {
      const result = await api.runTask(taskId);
      logger.info(`Task ${taskId} completed`, result);

      // Show result notification
      // Note: Cancelled tasks don't show notification here - handleCancel shows it via polling
      if (result.error === 'CANCELLED') {
        // Task was cancelled - notification already shown by handleCancel
        logger.info(`${taskName} was cancelled (notification handled by cancel handler)`);
      } else if (result.success) {
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

      // Reload tasks to get updated status
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

  if (loading) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
        Loading scheduled tasks...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: '#e74c3c' }}>
        {error}
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
          onClick={loadTasks}
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
            isRunning={runningTasks.has(task.task_id)}
          />
        ))
      )}

      {/* Task Editor Modal */}
      {editingTask && (
        <TaskEditorModal
          task={editingTask}
          onClose={() => setEditingTask(null)}
          onSaved={loadTasks}
        />
      )}
    </div>
  );
}
