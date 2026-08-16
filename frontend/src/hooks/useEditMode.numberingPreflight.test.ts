/**
 * Apply is refused before anything is mutated when the COMBINED final state of
 * the staged operations violates the channel-number contract (bead
 * `enhancedchannelmanager-ic884.2`), and a numbering-driven rename is visible
 * in the change preview before Apply (bead `enhancedchannelmanager-ic884.5`).
 *
 * The property under test is about the final state, not about any one
 * operation: each edit below is individually legal and the plan is only wrong
 * once all of them are applied. "Before mutation" is asserted the only way it
 * can be — `api.bulkCommit` is never called at all.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useEditMode } from './useEditMode';
import * as api from '../services/api';
import type { Channel } from '../types';

function makeChannel(id: number, name: string, channel_number: number | null): Channel {
  return {
    id,
    channel_number,
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

const CHANNELS = [
  makeChannel(1, 'ESPN', 5),
  makeChannel(2, 'TNT', 6),
  makeChannel(3, 'AMC', 7),
];

let bulkCommit: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.spyOn(api, 'getChannels').mockResolvedValue({
    results: CHANNELS,
    next: null,
    count: CHANNELS.length,
  } as unknown as Awaited<ReturnType<typeof api.getChannels>>);
  bulkCommit = vi.spyOn(api, 'bulkCommit').mockResolvedValue({
    success: true,
    operationsApplied: 1,
    operationsFailed: 0,
    errors: [],
    tempIdMap: {},
    groupIdMap: {},
  } as unknown as Awaited<ReturnType<typeof api.bulkCommit>>);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function setup(onError = vi.fn()) {
  const view = renderHook(() =>
    useEditMode({
      channels: CHANNELS,
      onChannelsChange: vi.fn(),
      onError,
      operatorKey: 'test#1',
    }),
  );
  act(() => view.result.current.enterEditMode());
  return { view, onError };
}

describe('final-state numbering preflight', () => {
  it('applies a plan whose combined final state is clean', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
      view.result.current.stageUpdateChannel(2, { channel_number: 5 }, 'TNT to 5');
    });
    let outcome: Awaited<ReturnType<typeof view.result.current.commit>> | undefined;
    await act(async () => {
      outcome = await view.result.current.commit();
    });
    expect(outcome!.success).toBe(true);
    expect(bulkCommit).toHaveBeenCalled();
  });

  it('refuses, before any request, a collision only the combination creates', async () => {
    const { view, onError } = setup();
    act(() => {
      // Individually legal: 5 is vacated by ESPN, so either TNT or AMC could
      // take it. Both taking it is only visible in the final state.
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
      view.result.current.stageUpdateChannel(2, { channel_number: 5 }, 'TNT to 5');
      view.result.current.stageUpdateChannel(3, { channel_number: 5 }, 'AMC to 5');
    });
    let outcome: Awaited<ReturnType<typeof view.result.current.commit>> | undefined;
    await act(async () => {
      outcome = await view.result.current.commit();
    });
    expect(bulkCommit).not.toHaveBeenCalled();
    expect(outcome!.success).toBe(false);
    expect(outcome!.operationsApplied).toBe(0);
    expect(outcome!.validationPassed).toBe(false);
    expect(outcome!.validationIssues?.[0].message).toContain('5');
    expect(onError).toHaveBeenCalled();
  });

  it('names the staged operations responsible, not merely the number', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
      view.result.current.stageUpdateChannel(2, { channel_number: 5 }, 'Moved TNT onto 5');
      view.result.current.stageUpdateChannel(3, { channel_number: 5 }, 'Moved AMC onto 5');
    });
    let outcome: Awaited<ReturnType<typeof view.result.current.commit>> | undefined;
    await act(async () => {
      outcome = await view.result.current.commit();
    });
    const message = outcome!.validationIssues!.map((issue) => issue.message).join(' ');
    expect(message).toContain('TNT');
    expect(message).toContain('AMC');
  });

  it('leaves the staged work intact so the operator can fix it', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(2, { channel_number: 5 }, 'TNT to 5');
    });
    await act(async () => {
      await view.result.current.commit();
    });
    expect(view.result.current.isEditMode).toBe(true);
    expect(view.result.current.stagedOperationCount).toBe(1);
  });

  it('does not re-litigate a duplicate the operator confirmed', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(2, { channel_number: 5 }, 'TNT to 5', {
        acknowledgedDuplicateNumber: 5,
      });
    });
    let outcome: Awaited<ReturnType<typeof view.result.current.commit>> | undefined;
    await act(async () => {
      outcome = await view.result.current.commit();
    });
    expect(outcome!.success).toBe(true);
    expect(bulkCommit).toHaveBeenCalled();
  });

  it('still refuses when an unconfirmed operation joins a confirmed duplicate', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(2, { channel_number: 5 }, 'TNT to 5', {
        acknowledgedDuplicateNumber: 5,
      });
      view.result.current.stageUpdateChannel(3, { channel_number: 5 }, 'AMC to 5');
    });
    let outcome: Awaited<ReturnType<typeof view.result.current.commit>> | undefined;
    await act(async () => {
      outcome = await view.result.current.commit();
    });
    expect(bulkCommit).not.toHaveBeenCalled();
    expect(outcome!.validationIssues![0].message).toContain('AMC');
  });

  it('refuses a bulk range whose tail loses distinct numbers', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageBulkAssignNumbers([1, 2, 3], 2 ** 53 - 1, 'Renumber from the ceiling');
    });
    await act(async () => {
      await view.result.current.commit();
    });
    expect(bulkCommit).not.toHaveBeenCalled();
  });

  it('leaves a duplicate that already existed on the server alone', async () => {
    const existingDuplicates = [
      makeChannel(1, 'ESPN', 5),
      makeChannel(2, 'ESPN HD', 5),
    ];
    vi.spyOn(api, 'getChannels').mockResolvedValue({
      results: existingDuplicates,
      next: null,
      count: 2,
    } as unknown as Awaited<ReturnType<typeof api.getChannels>>);
    const view = renderHook(() =>
      useEditMode({
        channels: existingDuplicates,
        onChannelsChange: vi.fn(),
        operatorKey: 'test#1',
      }),
    );
    act(() => view.result.current.enterEditMode());
    act(() => {
      view.result.current.stageUpdateChannel(1, { name: 'ESPN Renamed' }, 'Rename ESPN');
    });
    let outcome: Awaited<ReturnType<typeof view.result.current.commit>> | undefined;
    await act(async () => {
      outcome = await view.result.current.commit();
    });
    expect(outcome!.success).toBe(true);
    expect(bulkCommit).toHaveBeenCalled();
  });
});

describe('automatic renames in the change preview', () => {
  it('shows the rename a numbering change caused', () => {
    const channels = [makeChannel(1, '5 | ESPN', 5)];
    const view = renderHook(() =>
      useEditMode({ channels, onChannelsChange: vi.fn(), operatorKey: 'test#1' }),
    );
    act(() => view.result.current.enterEditMode());
    act(() => {
      view.result.current.stageUpdateChannel(
        1,
        { channel_number: 9, name: '9 | ESPN' },
        'Changed "5 | ESPN" to "9 | ESPN"',
      );
    });
    expect(view.result.current.summary.automaticRenames).toEqual([
      { channelId: 1, from: '5 | ESPN', to: '9 | ESPN', fromNumber: 5, toNumber: 9 },
    ]);
  });

  it('shows nothing when auto-rename staged no name', () => {
    const channels = [makeChannel(1, '5 | ESPN', 5)];
    const view = renderHook(() =>
      useEditMode({ channels, onChannelsChange: vi.fn(), operatorKey: 'test#1' }),
    );
    act(() => view.result.current.enterEditMode());
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 9 }, 'ESPN to 9');
    });
    expect(view.result.current.summary.automaticRenames).toEqual([]);
  });

  it('drops the rename again when the numbering change is undone', () => {
    const channels = [makeChannel(1, '5 | ESPN', 5)];
    const view = renderHook(() =>
      useEditMode({ channels, onChannelsChange: vi.fn(), operatorKey: 'test#1' }),
    );
    act(() => view.result.current.enterEditMode());
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 9, name: '9 | ESPN' }, 'renumber');
    });
    act(() => view.result.current.localUndo());
    expect(view.result.current.summary.automaticRenames).toEqual([]);
  });
});
