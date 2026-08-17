/**
 * The seam that makes the backup-banner snooze invariant hold app-wide (bead
 * enhancedchannelmanager-pui76, review round 2).
 *
 * Invariant: whenever a `dbas_backup` schedule is observed enabled, any stored
 * snooze is void — regardless of what is mounted. The banner component cannot
 * enforce that, because the sequence that broke it (snooze the banner, leave
 * Backup & Restore, enable and then disable the schedule from Scheduled Tasks,
 * come back) never mounts the banner while the schedule is enabled. So the
 * enforcement sits on every API function through which schedules enter the
 * frontend, and this file pins each of them.
 *
 * Layer: consumer integration. Only `fetch` is stubbed, so the real
 * `fetchJson` and the real exported API functions run — a test that mocked
 * `services/api` would prove nothing about this seam, which IS
 * `services/api`.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { SNOOZE_UNTIL_KEY, startBackupBannerSnooze } from '../utils/backupBannerSnooze';
import * as api from './api';

const NOW = 1_770_000_000_000;

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => payload,
  } as unknown as Response;
}

function stubFetch(payload: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** Only the fields the observation reads; the rest of TaskStatus is irrelevant here. */
function task(taskId: string, enabled: boolean) {
  return { task_id: taskId, schedules: [{ id: 1, task_id: taskId, enabled }] };
}

function snoozeIsStored(): boolean {
  return localStorage.getItem(SNOOZE_UNTIL_KEY) !== null;
}

describe('services/api — an observed enabled dbas_backup schedule voids the snooze', () => {
  beforeEach(() => {
    localStorage.clear();
    startBackupBannerSnooze(NOW);
    expect(snoozeIsStored()).toBe(true);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('voids it on getTasks — the call the Scheduled Tasks page polls', async () => {
    stubFetch({ tasks: [task('epg_refresh', false), task('dbas_backup', true)] });
    await api.getTasks();
    expect(snoozeIsStored()).toBe(false);
  });

  it('voids it on getTask', async () => {
    stubFetch(task('dbas_backup', true));
    await api.getTask('dbas_backup');
    expect(snoozeIsStored()).toBe(false);
  });

  it('voids it on getTaskSchedules — the call the task editor refreshes with', async () => {
    stubFetch({ schedules: [{ id: 1, task_id: 'dbas_backup', enabled: true }] });
    await api.getTaskSchedules('dbas_backup');
    expect(snoozeIsStored()).toBe(false);
  });

  it('voids it the moment a schedule is created enabled', async () => {
    stubFetch({ id: 7, task_id: 'dbas_backup', enabled: true });
    await api.createTaskSchedule('dbas_backup', {
      name: 'Nightly backup',
      schedule_type: 'daily',
      schedule_time: '03:00',
    });
    expect(snoozeIsStored()).toBe(false);
  });

  it('voids it the moment an existing schedule is switched on', async () => {
    stubFetch({ id: 7, task_id: 'dbas_backup', enabled: true });
    await api.updateTaskSchedule('dbas_backup', 7, { enabled: true });
    expect(snoozeIsStored()).toBe(false);
  });

  it('leaves it alone when the backup task is observed with no enabled schedule', async () => {
    stubFetch({ tasks: [task('dbas_backup', false)] });
    await api.getTasks();
    expect(snoozeIsStored()).toBe(true);
  });

  it('leaves it alone when the enabled schedule belongs to a different task', async () => {
    stubFetch({ tasks: [task('epg_refresh', true)] });
    await api.getTasks();
    expect(snoozeIsStored()).toBe(true);
  });

  it('still returns the payload each caller expects', async () => {
    // The observation is a side effect bolted onto these functions; a silent
    // change to what they return would break every consumer of the task API.
    stubFetch({ tasks: [task('dbas_backup', true)] });
    const { tasks } = await api.getTasks();
    expect(tasks).toHaveLength(1);
    expect(tasks[0].task_id).toBe('dbas_backup');

    stubFetch({ schedules: [{ id: 3, task_id: 'dbas_backup', enabled: false }] });
    const { schedules } = await api.getTaskSchedules('dbas_backup');
    expect(schedules).toEqual([{ id: 3, task_id: 'dbas_backup', enabled: false }]);

    stubFetch({ id: 9, task_id: 'dbas_backup', enabled: true });
    await expect(api.updateTaskSchedule('dbas_backup', 9, { enabled: true })).resolves.toMatchObject(
      { id: 9 },
    );
  });
});
