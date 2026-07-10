/**
 * Tests for display-level task notification pair collapsing (bd-ib2w3).
 */
import { describe, it, expect } from 'vitest';
import type { Notification } from '../services/api';
import {
  collapseTaskNotificationPairs,
  collapsedUnreadAdjustment,
  isEntryRead,
  isFinalizedProgressNotification,
} from './notificationGrouping';

let nextId = 1;

function makeNotification(overrides: Partial<Notification>): Notification {
  return {
    id: nextId++,
    type: 'info',
    title: null,
    message: 'msg',
    read: false,
    source: null,
    source_id: null,
    action_label: null,
    action_url: null,
    metadata: null,
    created_at: '2026-07-10T12:00:00Z',
    read_at: null,
    expires_at: null,
    ...overrides,
  };
}

function makeResult(taskId: string, createdAt: string, overrides: Partial<Notification> = {}): Notification {
  return makeNotification({
    type: 'success',
    title: `Task Completed: ${taskId}`,
    source: 'task',
    source_id: taskId,
    created_at: createdAt,
    ...overrides,
  });
}

function makeProgress(
  taskId: string,
  createdAt: string,
  status: string,
  overrides: Partial<Notification> = {},
): Notification {
  return makeNotification({
    title: taskId,
    source: `task_${taskId}`,
    source_id: `progress_1234`,
    metadata: { progress: { status, current: 0, total: 0 } },
    created_at: createdAt,
    ...overrides,
  });
}

describe('collapseTaskNotificationPairs', () => {
  it('collapses a completed result with its finalized progress entry', () => {
    const result = makeResult('epg_refresh', '2026-07-10T12:05:00Z');
    const progress = makeProgress('epg_refresh', '2026-07-10T12:00:00Z', 'completed');

    const entries = collapseTaskNotificationPairs([result, progress]);

    expect(entries).toHaveLength(1);
    expect(entries[0].primary).toBe(result);
    expect(entries[0].collapsed).toBe(progress);
  });

  it('does not collapse an active (running) progress notification', () => {
    const result = makeResult('epg_refresh', '2026-07-10T12:05:00Z');
    const running = makeProgress('epg_refresh', '2026-07-10T12:06:00Z', 'probing');

    // running progress is newer (new run started after previous completed)
    const entries = collapseTaskNotificationPairs([running, result]);

    expect(entries).toHaveLength(2);
    expect(entries[0].primary).toBe(running);
    expect(entries[0].collapsed).toBeNull();
    expect(entries[1].primary).toBe(result);
    expect(entries[1].collapsed).toBeNull();
  });

  it('pairs multiple runs of the same task with the nearest progress entry', () => {
    const result2 = makeResult('m3u_refresh', '2026-07-10T14:05:00Z');
    const progress2 = makeProgress('m3u_refresh', '2026-07-10T14:00:00Z', 'completed');
    const result1 = makeResult('m3u_refresh', '2026-07-10T12:05:00Z');
    const progress1 = makeProgress('m3u_refresh', '2026-07-10T12:00:00Z', 'completed');

    const entries = collapseTaskNotificationPairs([result2, progress2, result1, progress1]);

    expect(entries).toHaveLength(2);
    expect(entries[0].primary).toBe(result2);
    expect(entries[0].collapsed).toBe(progress2);
    expect(entries[1].primary).toBe(result1);
    expect(entries[1].collapsed).toBe(progress1);
  });

  it('does not pair progress entries from a different task', () => {
    const result = makeResult('epg_refresh', '2026-07-10T12:05:00Z');
    const otherProgress = makeProgress('m3u_refresh', '2026-07-10T12:00:00Z', 'completed');

    const entries = collapseTaskNotificationPairs([result, otherProgress]);

    expect(entries).toHaveLength(2);
    expect(entries[0].collapsed).toBeNull();
  });

  it('does not pair a progress entry older than the pairing window', () => {
    const result = makeResult('epg_refresh', '2026-07-10T12:00:00Z');
    const staleProgress = makeProgress('epg_refresh', '2026-07-08T12:00:00Z', 'completed');

    const entries = collapseTaskNotificationPairs([result, staleProgress]);

    expect(entries).toHaveLength(2);
    expect(entries[0].collapsed).toBeNull();
  });

  it('passes non-task notifications through unchanged', () => {
    const system = makeNotification({ source: 'system', message: 'Restart required' });
    const autoCreation = makeNotification({ source: 'auto_creation', message: 'Pipeline done' });

    const entries = collapseTaskNotificationPairs([system, autoCreation]);

    expect(entries).toHaveLength(2);
    expect(entries[0].primary).toBe(system);
    expect(entries[0].collapsed).toBeNull();
    expect(entries[1].primary).toBe(autoCreation);
    expect(entries[1].collapsed).toBeNull();
  });

  it('collapses the stream probe pair despite its legacy progress source', () => {
    // stream_prober creates the progress entry with source 'stream_probe'
    // (not 'task_stream_probe') — the one legacy alias we pair.
    const result = makeResult('stream_probe', '2026-07-10T08:52:31Z', {
      title: 'Task Completed with Warnings: Stream Probe',
      type: 'warning',
    });
    const progress = makeNotification({
      source: 'stream_probe',
      source_id: '1783670485',
      title: 'Stream Probe',
      metadata: { progress: { status: 'completed' } },
      created_at: '2026-07-10T08:01:25Z',
    });

    const entries = collapseTaskNotificationPairs([result, progress]);

    expect(entries).toHaveLength(1);
    expect(entries[0].primary).toBe(result);
    expect(entries[0].collapsed).toBe(progress);
  });

  it('does not pair a stream_probe-sourced notification without progress metadata', () => {
    const result = makeResult('stream_probe', '2026-07-10T08:52:31Z');
    const plain = makeNotification({
      source: 'stream_probe',
      message: 'Some other probe message',
      created_at: '2026-07-10T08:01:25Z',
    });

    const entries = collapseTaskNotificationPairs([result, plain]);

    expect(entries).toHaveLength(2);
    expect(entries[0].collapsed).toBeNull();
  });

  it('a progress entry is claimed by at most one result', () => {
    // Two results but only one progress entry (other was deleted by the user)
    const result2 = makeResult('epg_refresh', '2026-07-10T14:05:00Z');
    const result1 = makeResult('epg_refresh', '2026-07-10T12:05:00Z');
    const progress = makeProgress('epg_refresh', '2026-07-10T14:00:00Z', 'completed');

    const entries = collapseTaskNotificationPairs([result2, progress, result1]);

    expect(entries).toHaveLength(2);
    expect(entries[0].primary).toBe(result2);
    expect(entries[0].collapsed).toBe(progress);
    expect(entries[1].primary).toBe(result1);
    expect(entries[1].collapsed).toBeNull();
  });
});

describe('isFinalizedProgressNotification', () => {
  it.each(['completed', 'failed', 'cancelled', 'idle'])('%s counts as finalized', (status) => {
    expect(isFinalizedProgressNotification(makeProgress('t', '2026-07-10T12:00:00Z', status))).toBe(true);
  });

  it.each(['probing', 'starting', 'fetching', 'paused'])('%s counts as active', (status) => {
    expect(isFinalizedProgressNotification(makeProgress('t', '2026-07-10T12:00:00Z', status))).toBe(false);
  });

  it('treats a notification without progress metadata as finalized', () => {
    expect(isFinalizedProgressNotification(makeNotification({}))).toBe(true);
  });
});

describe('isEntryRead', () => {
  it('entry is unread when either half is unread', () => {
    const result = makeResult('t', '2026-07-10T12:05:00Z', { read: true });
    const progress = makeProgress('t', '2026-07-10T12:00:00Z', 'completed', { read: false });
    const [entry] = collapseTaskNotificationPairs([result, progress]);
    expect(isEntryRead(entry)).toBe(false);
  });

  it('entry is read when both halves are read', () => {
    const result = makeResult('t', '2026-07-10T12:05:00Z', { read: true });
    const progress = makeProgress('t', '2026-07-10T12:00:00Z', 'completed', { read: true });
    const [entry] = collapseTaskNotificationPairs([result, progress]);
    expect(isEntryRead(entry)).toBe(true);
  });
});

describe('collapsedUnreadAdjustment', () => {
  it('subtracts one per pair where both halves are unread', () => {
    const result = makeResult('t', '2026-07-10T12:05:00Z', { read: false });
    const progress = makeProgress('t', '2026-07-10T12:00:00Z', 'completed', { read: false });
    const standalone = makeNotification({ source: 'system', read: false });

    const entries = collapseTaskNotificationPairs([result, progress, standalone]);

    expect(collapsedUnreadAdjustment(entries)).toBe(1);
  });

  it('no adjustment when only one half of a pair is unread', () => {
    const result = makeResult('t', '2026-07-10T12:05:00Z', { read: false });
    const progress = makeProgress('t', '2026-07-10T12:00:00Z', 'completed', { read: true });

    const entries = collapseTaskNotificationPairs([result, progress]);

    expect(collapsedUnreadAdjustment(entries)).toBe(0);
  });
});
