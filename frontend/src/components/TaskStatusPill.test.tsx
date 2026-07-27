/**
 * Unit tests for TaskStatusPill (bead vkktd.4).
 *
 * Locks the four-variant state derivation — Running / Enabled / "Enabled,
 * won't run" / Disabled — bound to the backend's `effective_enabled` (parent
 * gate AND >=1 enabled child schedule) so a task that is structurally unable
 * to fire can never present as a bare "Enabled" (the vkktd trap). Also locks
 * the MANUAL-only guard (never flagged wontRun) and the Fix-it affordance's
 * accessibility contract.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TaskStatusPill } from './TaskStatusPill';
import { getTaskPillState } from '../utils/taskPillState';
import type { TaskStatus, TaskSchedule } from '../services/api';

function makeSchedule(overrides: Partial<TaskSchedule> = {}): TaskSchedule {
  return {
    id: 1,
    task_id: 'auto_creation',
    name: null,
    enabled: true,
    schedule_type: 'interval',
    interval_seconds: 3600,
    schedule_time: null,
    timezone: null,
    days_of_week: null,
    day_of_month: null,
    week_parity: null,
    parameters: {},
    next_run_at: '2026-07-19T12:00:00Z',
    last_run_at: null,
    description: 'Every hour',
    created_at: '2026-07-01T00:00:00Z',
    updated_at: null,
    ...overrides,
  };
}

function makeTask(overrides: Partial<TaskStatus> = {}): TaskStatus {
  return {
    task_id: 'auto_creation',
    task_name: 'Channel Pipeline',
    task_description: 'Runs pipeline rules',
    status: 'idle',
    enabled: true,
    effective_enabled: true,
    progress: {
      total: 0, current: 0, percentage: 0, status: 'idle', current_item: '',
      success_count: 0, failed_count: 0, skipped_count: 0, started_at: null,
    },
    schedule: {
      schedule_type: 'interval', interval_seconds: 3600,
      cron_expression: '', schedule_time: '', timezone: 'UTC',
    },
    schedules: [makeSchedule()],
    last_run: null,
    next_run: '2026-07-19T12:00:00Z',
    config: {},
    ...overrides,
  };
}

describe('getTaskPillState', () => {
  it('returns running when the task status is running', () => {
    expect(getTaskPillState(makeTask({ status: 'running' }))).toBe('running');
  });

  it('returns disabled when the parent gate is off', () => {
    expect(getTaskPillState(makeTask({ enabled: false, effective_enabled: false }))).toBe('disabled');
  });

  it('returns enabled when effective-enabled with a next run', () => {
    expect(getTaskPillState(makeTask())).toBe('enabled');
  });

  it('returns wontRun when enabled but not effective-enabled (child schedule off)', () => {
    const task = makeTask({
      effective_enabled: false,
      next_run: null,
      schedules: [makeSchedule({ enabled: false, next_run_at: null })],
    });
    expect(getTaskPillState(task)).toBe('wontRun');
  });

  it('returns wontRun when effective-enabled but no next run could be computed', () => {
    const task = makeTask({ next_run: null });
    expect(getTaskPillState(task)).toBe('wontRun');
  });

  it('never flags MANUAL-only tasks (no schedules) as wontRun', () => {
    const task = makeTask({
      schedule: {
        schedule_type: 'manual', interval_seconds: 0,
        cron_expression: '', schedule_time: '', timezone: 'UTC',
      },
      schedules: [],
      effective_enabled: true,
      next_run: null,
    });
    expect(getTaskPillState(task)).toBe('enabled');
  });

  it('falls back to parent enabled when effective_enabled is absent (older backend)', () => {
    const task = makeTask({ effective_enabled: undefined });
    expect(getTaskPillState(task)).toBe('enabled');
  });
});

describe('TaskStatusPill', () => {
  it('renders the amber "Enabled, won\'t run" label with aria-live', () => {
    const task = makeTask({
      effective_enabled: false,
      next_run: null,
      schedules: [makeSchedule({ enabled: false, next_run_at: null })],
    });
    render(<TaskStatusPill task={task} />);

    const pill = screen.getByTestId('task-status-pill-auto_creation');
    expect(pill).toHaveTextContent("Enabled, won't run");
    expect(pill).toHaveAttribute('aria-live', 'polite');
    expect(pill.className).toContain('wontRun');
  });

  it('renders a bare Enabled pill without a Fix it button when firing-capable', () => {
    render(<TaskStatusPill task={makeTask()} onFixIt={vi.fn()} />);

    expect(screen.getByTestId('task-status-pill-auto_creation')).toHaveTextContent('Enabled');
    expect(screen.queryByTestId('task-fix-it-auto_creation')).not.toBeInTheDocument();
  });

  it('shows Fix it on wontRun with the "Enable schedule for {task}" aria-label and fires the callback', () => {
    const onFixIt = vi.fn();
    const task = makeTask({
      effective_enabled: false,
      next_run: null,
      schedules: [makeSchedule({ enabled: false, next_run_at: null })],
    });
    render(<TaskStatusPill task={task} onFixIt={onFixIt} />);

    const fixBtn = screen.getByRole('button', { name: 'Enable schedule for Channel Pipeline' });
    fireEvent.click(fixBtn);
    expect(onFixIt).toHaveBeenCalledWith(task);
  });

  it('disables the Fix it button while fixing', () => {
    const task = makeTask({
      effective_enabled: false,
      next_run: null,
      schedules: [makeSchedule({ enabled: false, next_run_at: null })],
    });
    render(<TaskStatusPill task={task} onFixIt={vi.fn()} fixing />);

    expect(screen.getByTestId('task-fix-it-auto_creation')).toBeDisabled();
  });

  it('shows Running when the frontend running override is set', () => {
    render(<TaskStatusPill task={makeTask()} running />);
    expect(screen.getByTestId('task-status-pill-auto_creation')).toHaveTextContent('Running');
  });
});
