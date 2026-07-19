/**
 * Task status-pill state derivation (bead vkktd.4).
 *
 * Kept out of TaskStatusPill.tsx so the component file only exports a
 * component (react-refresh/only-export-components) and the derivation is
 * reusable/testable on its own.
 */
import type { TaskStatus } from '../services/api';

export type TaskPillState = 'running' | 'enabled' | 'wontRun' | 'disabled';

/**
 * MANUAL-only tasks (legacy schedule_type 'manual' with no child schedule
 * rows) never auto-fire by design — they must not be flagged wontRun.
 * The backend exposes this via the legacy `schedule.schedule_type` field.
 */
export function isManualOnly(task: TaskStatus): boolean {
  return (
    task.schedule?.schedule_type === 'manual' &&
    (!task.schedules || task.schedules.length === 0)
  );
}

/**
 * Derive the pill state for a task, bound to the backend's
 * `effective_enabled` (parent gate AND >=1 enabled child schedule) so a task
 * that is structurally unable to fire never presents as a bare "Enabled"
 * (the vkktd trap).
 */
export function getTaskPillState(task: TaskStatus): TaskPillState {
  if (task.status === 'running') return 'running';
  if (!task.enabled) return 'disabled';
  if (isManualOnly(task)) return 'enabled';
  // effective_enabled: parent AND >=1 enabled child schedule (backend build
  // 0091+). Fall back to parent `enabled` on older backends. Even when
  // effective, a null next_run means the schedule could not compute a firing
  // time — still "won't run".
  const effective = task.effective_enabled ?? task.enabled;
  if (!effective || !task.next_run) return 'wontRun';
  return 'enabled';
}
