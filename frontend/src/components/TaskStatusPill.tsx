/**
 * TaskStatusPill — the scheduled-task status chip (bead vkktd.4).
 *
 * Four variants, bound to the backend's `effective_enabled` (parent gate AND
 * >=1 enabled child schedule) so the UI can never present a bare "Enabled" on
 * a task that is structurally unable to fire (the vkktd trap: parent on,
 * every child schedule off, next_run=null, zero feedback):
 *
 *   - running   — task is executing right now
 *   - enabled   — will actually fire (or is MANUAL-only by design)
 *   - wontRun   — "Enabled, won't run": parent enabled but no enabled child
 *                 schedule / no computable next run. Amber warning + one-click
 *                 "Fix it" affordance.
 *   - disabled  — parent gate off
 *
 * Per bd-9n08a this is a STATUS pill, not an action — outline/ghost chip
 * (transparent fill, colored border+text). The "Fix it" button IS an action,
 * so it gets a solid fill.
 */
import type { TaskStatus } from '../services/api';
import { getTaskPillState } from '../utils/taskPillState';
import type { TaskPillState } from '../utils/taskPillState';
import './TaskStatusPill.css';

const PILL_CONTENT: Record<TaskPillState, { icon: string; label: string }> = {
  running: { icon: 'sync', label: 'Running' },
  enabled: { icon: 'check_circle', label: 'Enabled' },
  wontRun: { icon: 'warning', label: "Enabled, won't run" },
  disabled: { icon: 'pause_circle', label: 'Disabled' },
};

interface TaskStatusPillProps {
  task: TaskStatus;
  /** Frontend-tracked "run now" in flight — overrides derived state. */
  running?: boolean;
  /** One-click fix for the wontRun state. Button renders only when provided. */
  onFixIt?: (task: TaskStatus) => void;
  /** Fix-it request in flight — disables the button. */
  fixing?: boolean;
}

export function TaskStatusPill({ task, running, onFixIt, fixing }: TaskStatusPillProps) {
  const state: TaskPillState = running ? 'running' : getTaskPillState(task);
  const { icon, label } = PILL_CONTENT[state];

  return (
    <span className="task-status-pill-group">
      {/* aria-live so state transitions (e.g. Enabled -> Enabled, won't run)
          are announced without moving focus (vkktd.4 a11y). */}
      <span
        className={`task-status-pill ${state}`}
        role="status"
        aria-live="polite"
        data-testid={`task-status-pill-${task.task_id}`}
        title={
          state === 'wontRun'
            ? 'This task is enabled, but no schedule is enabled for it, so it will never run automatically.'
            : undefined
        }
      >
        <span
          className="material-icons"
          aria-hidden="true"
          style={state === 'running' ? { animation: 'spin 1s linear infinite reverse' } : undefined}
        >
          {icon}
        </span>
        {label}
      </span>
      {state === 'wontRun' && onFixIt && (
        <button
          type="button"
          className="task-status-pill-fix-btn"
          data-testid={`task-fix-it-${task.task_id}`}
          onClick={() => onFixIt(task)}
          disabled={fixing}
          aria-label={`Enable schedule for ${task.task_name}`}
          title={`Enable schedule for ${task.task_name}`}
        >
          {fixing ? 'Fixing…' : 'Fix it'}
        </button>
      )}
    </span>
  );
}
