import { describe, expect, it, vi } from 'vitest';
import { aggregateWorkspaceSources, retryFailedSources, type WorkspaceSource } from './workspaceLoadState';

const source = (
  key: string,
  state: WorkspaceSource['state'],
  hasSnapshot = false,
): WorkspaceSource => ({ key, label: key, state, hasSnapshot, retry: vi.fn() });

describe('workspaceLoadState', () => {
  it.each([
    [
      'channels 403 completed before groups success',
      [source('channels', 'permission'), source('groups', 'success', true)],
      'permission',
    ],
    [
      'groups success completed before channels 403',
      [source('groups', 'success', true), source('channels', 'permission')],
      'permission',
    ],
    [
      'groups failure with channels success',
      [source('groups', 'error'), source('channels', 'success', true)],
      'error',
    ],
    [
      'both successful',
      [source('groups', 'success', true), source('channels', 'success', true)],
      'success',
    ],
  ] as const)('%s derives a deterministic aggregate', (_name, sources, expected) => {
    expect(aggregateWorkspaceSources(sources).state).toBe(expected);
  });

  it('labels an error stale only when every required source has a prior snapshot', () => {
    expect(aggregateWorkspaceSources([
      source('groups', 'error', true),
      source('channels', 'success', true),
    ])).toMatchObject({ state: 'error', stale: true });
    expect(aggregateWorkspaceSources([
      source('groups', 'error'),
      source('channels', 'success', true),
    ])).toMatchObject({ state: 'error', stale: false });
  });

  it('retries every failed operation together and no successful operation', async () => {
    const failedA = source('groups', 'error');
    const failedB = source('channels', 'error');
    const successful = source('other', 'success', true);
    await retryFailedSources([failedA, successful, failedB]);
    expect(failedA.retry).toHaveBeenCalledOnce();
    expect(failedB.retry).toHaveBeenCalledOnce();
    expect(successful.retry).not.toHaveBeenCalled();
  });
});
