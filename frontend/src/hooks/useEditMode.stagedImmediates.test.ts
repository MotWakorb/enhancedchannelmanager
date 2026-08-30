/**
 * The three operations added so Edit Mode can stage what it used to write
 * immediately (bead enhancedchannelmanager-kz089).
 *
 * Edit Mode presents itself as a staging area: make changes, then Apply All or
 * Discard. That was true of eleven actions and false of ten others, with
 * nothing on screen distinguishing them. Profile visibility, hidden-group
 * restore and stream-stat clears sat in Edit Mode toolbars and PATCHed the
 * server the instant they were clicked: not counted in the change count, not
 * reverted by Cancel or Discard, not reachable by Undo.
 *
 * These tests hold them to the contract every other staged action already
 * meets: counted, undoable, discardable, and sent on the wire only at Apply
 * All.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { MockInstance } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useEditMode } from './useEditMode';
import * as api from '../services/api';
import type { Channel } from '../types';

function makeChannel(id: number, name: string): Channel {
  return {
    id,
    channel_number: id,
    name,
    channel_group_id: null,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: `uuid-${id}`,
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };
}

const CHANNELS = [makeChannel(1, 'Alpha'), makeChannel(2, 'Bravo')];

function setup() {
  const view = renderHook(() =>
    useEditMode({ channels: CHANNELS, onChannelsChange: vi.fn(), operatorKey: 'test#1' }),
  );
  act(() => view.result.current.enterEditMode());
  return view;
}

let bulkCommitSpy: MockInstance<typeof api.bulkCommit>;

beforeEach(() => {
  bulkCommitSpy = vi.spyOn(api, 'bulkCommit').mockResolvedValue({
    success: true,
    operationsApplied: 0,
    operationsFailed: 0,
    errors: [],
    tempIdMap: {},
    groupIdMap: {},
  });
  vi.spyOn(api, 'getChannels').mockResolvedValue({
    results: CHANNELS, next: null, count: CHANNELS.length,
  } as unknown as Awaited<ReturnType<typeof api.getChannels>>);
  vi.spyOn(api, 'updateProfileChannel').mockResolvedValue({ success: true });
  vi.spyOn(api, 'restoreChannelGroup').mockResolvedValue(undefined);
  vi.spyOn(api, 'clearStreamStats').mockResolvedValue({ cleared: 1, stream_ids: [11] });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('staging profile visibility', () => {
  it('stages one operation per channel and writes nothing yet', () => {
    const view = setup();

    act(() => {
      view.result.current.stageSetProfileMembership(5, [1, 2], false, 'Disable in "Kids"');
    });

    expect(view.result.current.stagedOperationCount).toBe(2);
    expect(api.updateProfileChannel).not.toHaveBeenCalled();
  });

  it('counts the change so the exit dialog can show it', () => {
    const view = setup();

    act(() => {
      view.result.current.stageSetProfileMembership(5, [1, 2], true, 'Enable in "Kids"');
    });

    expect(view.result.current.summary.profileVisibilityChanges).toBe(2);
    // The headline is the sum of the buckets, so a bucket the headline forgets
    // is a dialog that disagrees with itself (bead …-75k49).
    expect(view.result.current.summary.totalChanges).toBe(2);
  });

  it('is reversed by Discard', () => {
    const view = setup();

    act(() => {
      view.result.current.stageSetProfileMembership(5, [1], true, 'Enable in "Kids"');
    });
    act(() => view.result.current.discard());

    expect(view.result.current.stagedOperationCount).toBe(0);
    expect(api.updateProfileChannel).not.toHaveBeenCalled();
  });

  it('is reversed by Undo', () => {
    const view = setup();

    act(() => {
      view.result.current.startBatch('Disable in "Kids"');
      view.result.current.stageSetProfileMembership(5, [1, 2], false, 'Disable in "Kids"');
      view.result.current.endBatch();
    });
    expect(view.result.current.canLocalUndo).toBe(true);

    act(() => view.result.current.localUndo());

    expect(view.result.current.stagedOperationCount).toBe(0);
  });

  it('reaches the server only at Apply All, as a bulk operation', async () => {
    const view = setup();

    act(() => {
      view.result.current.stageSetProfileMembership(5, [1], false, 'Disable in "Kids"');
    });
    await act(async () => {
      await view.result.current.commit();
    });

    expect(api.updateProfileChannel).not.toHaveBeenCalled();
    const ops = bulkCommitSpy.mock.calls.flatMap(
      (call) => (call[0] as api.BulkCommitRequest).operations,
    );
    expect(ops).toContainEqual({
      type: 'setProfileMembership', profileId: 5, channelId: 1, enabled: false,
    });
  });
});

describe('staging hidden-group restore', () => {
  it('stages, counts, and writes nothing yet', () => {
    const view = setup();

    act(() => {
      view.result.current.stageRestoreChannelGroup(9, 'Restore hidden group "Locals"');
    });

    expect(view.result.current.stagedOperationCount).toBe(1);
    expect(view.result.current.summary.restoredGroups).toBe(1);
    expect(api.restoreChannelGroup).not.toHaveBeenCalled();
  });

  it('sends the restore in the bulk commit', async () => {
    const view = setup();

    act(() => {
      view.result.current.stageRestoreChannelGroup(9, 'Restore hidden group "Locals"');
    });
    await act(async () => {
      await view.result.current.commit();
    });

    const ops = bulkCommitSpy.mock.calls.flatMap(
      (call) => (call[0] as api.BulkCommitRequest).operations,
    );
    expect(ops).toContainEqual({ type: 'restoreChannelGroup', groupId: 9 });
  });
});

describe('staging stream-stat clears', () => {
  it('counts one change per stream and writes nothing yet', () => {
    const view = setup();

    act(() => {
      view.result.current.stageClearStreamStats([11, 12], 'Clear probe stats');
    });

    expect(view.result.current.summary.clearedStreamStats).toBe(2);
    expect(api.clearStreamStats).not.toHaveBeenCalled();
  });

  it('sends the clear in the bulk commit', async () => {
    const view = setup();

    act(() => {
      view.result.current.stageClearStreamStats([11], 'Clear probe stats');
    });
    await act(async () => {
      await view.result.current.commit();
    });

    const ops = bulkCommitSpy.mock.calls.flatMap(
      (call) => (call[0] as api.BulkCommitRequest).operations,
    );
    expect(ops).toContainEqual({ type: 'clearStreamStats', streamIds: [11] });
  });
});

describe('bulk-commit batch correlation (bead enhancedchannelmanager-r9py9)', () => {
  it('sends every request of one Apply All under the same correlation id', async () => {
    const view = setup();

    act(() => {
      view.result.current.stageCreateChannel('New One', 900);
      view.result.current.stageUpdateChannel(1, { name: 'Alpha HD' }, 'rename');
    });
    await act(async () => {
      await view.result.current.commit();
    });

    // Creates go in their own request, other ops in batches — at least two
    // calls, and they must share one id or the journal scatters one Edit Mode
    // session across unrelated batches.
    expect(bulkCommitSpy.mock.calls.length).toBeGreaterThan(1);
    const batchIds = new Set(bulkCommitSpy.mock.calls.map((call) => call[1]));
    expect(batchIds.size).toBe(1);
    expect([...batchIds][0]).toBeTruthy();
  });
});
