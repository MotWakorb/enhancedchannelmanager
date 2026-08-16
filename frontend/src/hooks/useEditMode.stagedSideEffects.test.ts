/**
 * A staged operation is VISIBLE in the working copy, and Discard, Undo and Redo
 * all reach it (bead enhancedchannelmanager-kz089, fix round 2).
 *
 * The sibling file `useEditMode.stagedImmediates.test.ts` proves the queue-level
 * contract: counted, summarised, sent only at Apply All. Round 1 stopped there,
 * and the review found the half it missed:
 *
 *  - `setProfileMembership` had NO working-copy representation at all. It was
 *    counted and summarised while the UI carried on showing the channel as it
 *    was, which is worse than the immediate write it replaced — the operator is
 *    told something is pending and shown no evidence of it.
 *  - `clearStreamStats` and `restoreChannelGroup` did show evidence, by mutating
 *    component-local state that `discard` and `localUndo` cannot reach. So
 *    Discard dropped the change count while the stats stayed visually gone.
 *
 * `stagedSideEffects` is the fix for both, and it is DERIVED from
 * `stagedOperations` rather than accumulated beside it — which is why one set of
 * assertions covers stage, undo, redo and discard.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useEditMode, deriveStagedSideEffects } from './useEditMode';
import { profileMembershipKey } from '../types/editMode';
import type { Channel, StagedOperation } from '../types';
import * as api from '../services/api';

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
    useEditMode({ channels: CHANNELS, onChannelsChange: vi.fn() }),
  );
  act(() => view.result.current.enterEditMode());
  return view;
}

beforeEach(() => {
  vi.spyOn(api, 'updateProfileChannel').mockResolvedValue({ success: true });
  vi.spyOn(api, 'restoreChannelGroup').mockResolvedValue(undefined);
  vi.spyOn(api, 'clearStreamStats').mockResolvedValue({ cleared: 1, stream_ids: [11] });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('deriveStagedSideEffects', () => {
  function op(apiCall: StagedOperation['apiCall']): StagedOperation {
    return {
      id: `op-${Math.random()}`,
      timestamp: 0,
      description: '',
      apiCall,
      beforeSnapshot: [],
      afterSnapshot: [],
    };
  }

  it('reads every operation type it represents', () => {
    const effects = deriveStagedSideEffects([
      op({ type: 'setProfileMembership', profileId: 5, channelId: 1, enabled: false }),
      op({ type: 'restoreChannelGroup', groupId: 77 }),
      op({ type: 'clearStreamStats', streamIds: [11, 12] }),
    ]);

    expect(effects.profileMembership.get(profileMembershipKey(5, 1))).toBe(false);
    expect(effects.restoredGroupIds.has(77)).toBe(true);
    expect([...effects.clearedStreamIds].sort()).toEqual([11, 12]);
  });

  it('lets the later membership operation win, as the server will', () => {
    const effects = deriveStagedSideEffects([
      op({ type: 'setProfileMembership', profileId: 5, channelId: 1, enabled: true }),
      op({ type: 'setProfileMembership', profileId: 5, channelId: 1, enabled: false }),
    ]);

    expect(effects.profileMembership.get(profileMembershipKey(5, 1))).toBe(false);
  });

  it('keeps profiles apart, so one profile does not answer for another', () => {
    const effects = deriveStagedSideEffects([
      op({ type: 'setProfileMembership', profileId: 5, channelId: 1, enabled: false }),
    ]);

    expect(effects.profileMembership.has(profileMembershipKey(6, 1))).toBe(false);
  });

  it('ignores operations that belong to the channel working copy', () => {
    const effects = deriveStagedSideEffects([
      op({ type: 'updateChannel', channelId: 1, data: { name: 'X' } }),
      op({ type: 'deleteChannel', channelId: 2 }),
    ]);

    expect(effects.profileMembership.size).toBe(0);
    expect(effects.restoredGroupIds.size).toBe(0);
    expect(effects.clearedStreamIds.size).toBe(0);
  });
});

describe('the staged side effects a pane renders', () => {
  it('shows a staged profile membership the moment it is staged', () => {
    const view = setup();

    act(() => {
      view.result.current.stageSetProfileMembership(5, [1, 2], false, 'Disable in "Kids"');
    });

    const { profileMembership } = view.result.current.stagedSideEffects;
    expect(profileMembership.get(profileMembershipKey(5, 1))).toBe(false);
    expect(profileMembership.get(profileMembershipKey(5, 2))).toBe(false);
  });

  it('shows a staged stats clear and a staged group restore', () => {
    const view = setup();

    act(() => {
      view.result.current.stageClearStreamStats([11], 'Clear stats');
      view.result.current.stageRestoreChannelGroup(77, 'Restore group');
    });

    expect(view.result.current.stagedSideEffects.clearedStreamIds.has(11)).toBe(true);
    expect(view.result.current.stagedSideEffects.restoredGroupIds.has(77)).toBe(true);
  });

  it.each([
    ['profile membership', (v: ReturnType<typeof setup>) =>
      v.result.current.stageSetProfileMembership(5, [1], false, 'Disable'),
      (v: ReturnType<typeof setup>) => v.result.current.stagedSideEffects.profileMembership.size],
    ['a stats clear', (v: ReturnType<typeof setup>) =>
      v.result.current.stageClearStreamStats([11], 'Clear'),
      (v: ReturnType<typeof setup>) => v.result.current.stagedSideEffects.clearedStreamIds.size],
    ['a group restore', (v: ReturnType<typeof setup>) =>
      v.result.current.stageRestoreChannelGroup(77, 'Restore'),
      (v: ReturnType<typeof setup>) => v.result.current.stagedSideEffects.restoredGroupIds.size],
  ])('Undo then Redo puts %s back exactly where it was', (_label, stage, size) => {
    const view = setup();

    act(() => {
      view.result.current.startBatch('batch');
      stage(view);
      view.result.current.endBatch();
    });
    expect(size(view)).toBe(1);

    act(() => view.result.current.localUndo());
    expect(size(view)).toBe(0);

    act(() => view.result.current.localRedo());
    expect(size(view)).toBe(1);
  });

  it('Discard clears every one of them at once', () => {
    const view = setup();

    act(() => {
      view.result.current.stageSetProfileMembership(5, [1], false, 'Disable');
      view.result.current.stageClearStreamStats([11], 'Clear');
      view.result.current.stageRestoreChannelGroup(77, 'Restore');
    });
    act(() => view.result.current.discard());

    const effects = view.result.current.stagedSideEffects;
    expect(effects.profileMembership.size).toBe(0);
    expect(effects.clearedStreamIds.size).toBe(0);
    expect(effects.restoredGroupIds.size).toBe(0);
  });

  it('is empty outside Edit Mode', () => {
    const view = renderHook(() =>
      useEditMode({ channels: CHANNELS, onChannelsChange: vi.fn() }),
    );

    const effects = view.result.current.stagedSideEffects;
    expect(effects.profileMembership.size).toBe(0);
    expect(effects.clearedStreamIds.size).toBe(0);
    expect(effects.restoredGroupIds.size).toBe(0);
  });
});
