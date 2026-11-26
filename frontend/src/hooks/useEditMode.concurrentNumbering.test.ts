/**
 * Apply reads the server BEFORE it writes, and refuses to overwrite a channel
 * number somebody else changed since this session's baseline was captured
 * (bead `enhancedchannelmanager-ic884.4`).
 *
 * The invariants under test, stated as properties:
 *
 *   1. A staged change never overwrites a server-side change made after the
 *      baseline was captured, without the operator being shown it and choosing.
 *   2. What the operator is shown before Apply is what Apply produces —
 *      including after a reconcile.
 *   6. A restored session re-establishes its baseline; it cannot apply against
 *      one captured before it died.
 *   7. Existing behaviour is unchanged when nothing has moved.
 *
 * "Nothing was written" is asserted the only way it can be: `api.bulkCommit` is
 * never called at all.
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
let getChannels: ReturnType<typeof vi.spyOn>;

/** What the server returns from here on. Every page in one response. */
function serverHolds(channels: Channel[]) {
  getChannels.mockResolvedValue({
    results: channels,
    next: null,
    count: channels.length,
  } as unknown as Awaited<ReturnType<typeof api.getChannels>>);
}

beforeEach(() => {
  getChannels = vi.spyOn(api, 'getChannels');
  serverHolds(CHANNELS);
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

function setup(channels: Channel[] = CHANNELS) {
  const onError = vi.fn();
  const view = renderHook(() =>
    useEditMode({
      channels,
      onChannelsChange: vi.fn(),
      onError,
      operatorKey: 'test#1',
    }),
  );
  act(() => view.result.current.enterEditMode());
  return { view, onError };
}

async function commit(view: ReturnType<typeof setup>['view']) {
  let outcome: Awaited<ReturnType<typeof view.result.current.commit>> | undefined;
  await act(async () => {
    outcome = await view.result.current.commit();
  });
  return outcome!;
}

describe('invariant 7 — nothing has moved, nothing changes', () => {
  it('applies normally when the server still holds the baseline numbers', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    const outcome = await commit(view);
    expect(outcome.numberingConflicts).toBeUndefined();
    expect(outcome.success).toBe(true);
    expect(bulkCommit).toHaveBeenCalled();
  });

  it('sends the baseline number as the expectation so the server can check too', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    await commit(view);
    const operations = (bulkCommit.mock.calls[0][0] as { operations: Record<string, unknown>[] }).operations;
    expect(operations[0]).toMatchObject({
      channelId: 1,
      expectedNumber: { number: 5 },
    });
  });
});

describe('invariant 1 — a change nobody has seen is never written over', () => {
  it('reports a conflict and writes nothing', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    serverHolds([makeChannel(1, 'ESPN', 88), CHANNELS[1], CHANNELS[2]]);

    const outcome = await commit(view);
    expect(bulkCommit).not.toHaveBeenCalled();
    expect(outcome.success).toBe(false);
    expect(outcome.numberingConflicts).toHaveLength(1);
    expect(outcome.numberingConflicts![0]).toMatchObject({
      channelId: 1,
      baselineNumber: 5,
      serverNumber: 88,
      proposedNumber: 100,
    });
  });

  it('carries the server read the conflicts were found in', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    const moved = [makeChannel(1, 'ESPN', 88), CHANNELS[1], CHANNELS[2]];
    serverHolds(moved);
    const outcome = await commit(view);
    expect(outcome.serverChannels?.map((channel) => channel.channel_number)).toEqual([88, 6, 7]);
  });

  it('does not block over a channel this session does not renumber', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    serverHolds([CHANNELS[0], makeChannel(2, 'TNT', 900), CHANNELS[2]]);
    const outcome = await commit(view);
    expect(outcome.numberingConflicts).toBeUndefined();
    expect(bulkCommit).toHaveBeenCalled();
  });

  it('refuses rather than guessing when the lineup cannot be read', async () => {
    const { view, onError } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    getChannels.mockRejectedValue(new Error('Dispatcharr unreachable'));
    const outcome = await commit(view);
    expect(bulkCommit).not.toHaveBeenCalled();
    expect(outcome.success).toBe(false);
    expect(outcome.validationIssues![0].message).toContain('could not be read');
    expect(onError).toHaveBeenCalled();
  });

  it('catches a channel that appeared on a number the plan proposes', async () => {
    // The preflight runs against the FRESH lineup, not the prop this session
    // loaded — a channel another operator created is invisible in the prop.
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    serverHolds([...CHANNELS, makeChannel(9, 'Newcomer', 100)]);
    const outcome = await commit(view);
    expect(bulkCommit).not.toHaveBeenCalled();
    expect(outcome.validationIssues?.[0].type).toBe('duplicate_channel_number');
  });
});

describe('invariant 2 — the reconcile produces what the operator chose', () => {
  it('keep-mine applies the staged number and stops asking', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    const moved = [makeChannel(1, 'ESPN', 88), CHANNELS[1], CHANNELS[2]];
    serverHolds(moved);
    const first = await commit(view);

    act(() => {
      view.result.current.reconcileNumberingConflicts(
        [{ channelId: 1, choice: 'keep-mine', baselineNumber: 5, serverNumber: 88 }],
        first.serverChannels!,
      );
    });
    const second = await commit(view);
    expect(second.numberingConflicts).toBeUndefined();
    expect(second.success).toBe(true);
    const operations = (bulkCommit.mock.calls[0][0] as { operations: Record<string, unknown>[] }).operations;
    expect(operations[0]).toMatchObject({
      channelId: 1,
      data: { channel_number: 100 },
      // The expectation is now the value the operator agreed to overwrite.
      expectedNumber: { number: 88 },
    });
  });

  it('take-theirs drops the staged number entirely', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    const moved = [makeChannel(1, 'ESPN', 88), CHANNELS[1], CHANNELS[2]];
    serverHolds(moved);
    const first = await commit(view);

    act(() => {
      view.result.current.reconcileNumberingConflicts(
        [{ channelId: 1, choice: 'take-theirs', baselineNumber: 5, serverNumber: 88 }],
        first.serverChannels!,
      );
    });
    expect(view.result.current.stagedOperationCount).toBe(0);
  });

  it('take-theirs leaves the working copy showing the server number', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    const moved = [makeChannel(1, 'ESPN', 88), CHANNELS[1], CHANNELS[2]];
    serverHolds(moved);
    const first = await commit(view);
    act(() => {
      view.result.current.reconcileNumberingConflicts(
        [{ channelId: 1, choice: 'take-theirs', baselineNumber: 5, serverNumber: 88 }],
        first.serverChannels!,
      );
    });
    const espn = view.result.current.displayChannels.find((channel) => channel.id === 1);
    expect(espn?.channel_number).toBe(88);
  });

  it('reports a change that moved AGAIN after the decision', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    const moved = [makeChannel(1, 'ESPN', 88), CHANNELS[1], CHANNELS[2]];
    serverHolds(moved);
    const first = await commit(view);
    act(() => {
      view.result.current.reconcileNumberingConflicts(
        [{ channelId: 1, choice: 'keep-mine', baselineNumber: 5, serverNumber: 88 }],
        first.serverChannels!,
      );
    });
    // Somebody moves it a third time before the operator presses Apply.
    serverHolds([makeChannel(1, 'ESPN', 99), CHANNELS[1], CHANNELS[2]]);
    const second = await commit(view);
    expect(bulkCommit).not.toHaveBeenCalled();
    expect(second.numberingConflicts).toHaveLength(1);
    expect(second.numberingConflicts![0]).toMatchObject({
      baselineNumber: 88,
      serverNumber: 99,
    });
  });

  it('leaves an unrelated staged change alone through a reconcile', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
      view.result.current.stageUpdateChannel(3, { name: 'AMC HD' }, 'Rename AMC');
    });
    const moved = [makeChannel(1, 'ESPN', 88), CHANNELS[1], CHANNELS[2]];
    serverHolds(moved);
    const first = await commit(view);
    act(() => {
      view.result.current.reconcileNumberingConflicts(
        [{ channelId: 1, choice: 'take-theirs', baselineNumber: 5, serverNumber: 88 }],
        first.serverChannels!,
      );
    });
    const second = await commit(view);
    expect(second.success).toBe(true);
    const operations = (bulkCommit.mock.calls[0][0] as { operations: Record<string, unknown>[] }).operations;
    expect(operations).toHaveLength(1);
    expect(operations[0]).toMatchObject({ channelId: 3, data: { name: 'AMC HD' } });
  });
});

describe('invariant 6 — a restored session re-establishes its baseline', () => {
  it('compares against the lineup as it stands now, not the one that died', async () => {
    // The dead session staged "ESPN to 100" while ESPN was on 5. It comes back
    // to a server where ESPN is on 88. The RESTORE re-captures the baseline, so
    // Apply is measured against 88 — and if the server still says 88 when Apply
    // runs, there is nothing to report.
    const restoredLineup = [makeChannel(1, 'ESPN', 88), CHANNELS[1], CHANNELS[2]];
    const view = renderHook(() =>
      useEditMode({
        channels: restoredLineup,
        onChannelsChange: vi.fn(),
        onError: vi.fn(),
        operatorKey: 'test#1',
      }),
    ).result;
    act(() => {
      view.current.restoreStagedLedger({
        operations: [{
          id: 'restored-1',
          timestamp: 1,
          description: 'ESPN to 100',
          apiCall: { type: 'updateChannel', channelId: 1, data: { channel_number: 100 } },
          beforeSnapshot: [],
          afterSnapshot: [],
        }],
        undoGroups: [['restored-1']],
        savedAt: Date.now(),
      });
    });
    serverHolds(restoredLineup);

    let outcome: Awaited<ReturnType<typeof view.current.commit>> | undefined;
    await act(async () => {
      outcome = await view.current.commit();
    });
    expect(outcome!.numberingConflicts).toBeUndefined();
    expect(bulkCommit).toHaveBeenCalled();
    const operations = (bulkCommit.mock.calls[0][0] as { operations: Record<string, unknown>[] }).operations;
    // The expectation is 88, the value the restore re-baselined on — never 5,
    // which is the value the dead session saw.
    expect(operations[0]).toMatchObject({ expectedNumber: { number: 88 } });
  });

  it('still reports a change made after the restore re-captured the baseline', async () => {
    const restoredLineup = [makeChannel(1, 'ESPN', 88), CHANNELS[1], CHANNELS[2]];
    const view = renderHook(() =>
      useEditMode({
        channels: restoredLineup,
        onChannelsChange: vi.fn(),
        onError: vi.fn(),
        operatorKey: 'test#1',
      }),
    ).result;
    act(() => {
      view.current.restoreStagedLedger({
        operations: [{
          id: 'restored-1',
          timestamp: 1,
          description: 'ESPN to 100',
          apiCall: { type: 'updateChannel', channelId: 1, data: { channel_number: 100 } },
          beforeSnapshot: [],
          afterSnapshot: [],
        }],
        undoGroups: [['restored-1']],
        savedAt: Date.now(),
      });
    });
    serverHolds([makeChannel(1, 'ESPN', 123), CHANNELS[1], CHANNELS[2]]);

    let outcome: Awaited<ReturnType<typeof view.current.commit>> | undefined;
    await act(async () => {
      outcome = await view.current.commit();
    });
    expect(bulkCommit).not.toHaveBeenCalled();
    expect(outcome!.numberingConflicts![0]).toMatchObject({
      baselineNumber: 88,
      serverNumber: 123,
    });
  });
});

describe('invariant 3 — the operator is given the exact remaining step', () => {
  it('renders a recovery step from the envelope as a readable error', async () => {
    // A numbering plan stopped part way AND the compensating write failed too,
    // so neither the previous numbering nor the proposed one is what the
    // operator is left with. The step travels in the envelope, the journal and
    // the container log; none of those is where the operator is standing.
    const { view } = setup();
    act(() => {
      view.result.current.stageUpdateChannel(1, { channel_number: 100 }, 'ESPN to 100');
    });
    bulkCommit.mockResolvedValue({
      success: false,
      partial: true,
      operationsApplied: 1,
      operationsFailed: 1,
      errors: [{
        operationId: 'bulk-commit-numbering-recovery',
        error: '1 channel(s) could not be put back on the channel number they had.',
      }],
      tempIdMap: {},
      groupIdMap: {},
      numberingRecovery: [{
        channelId: 1,
        channelName: 'ESPN',
        currentNumber: 100,
        targetNumber: 5,
        step: 'Set "ESPN" (channel 1) back to channel number 5; this run left it on 100.',
        error: 'upstream refused',
      }],
    } as unknown as Awaited<ReturnType<typeof api.bulkCommit>>);

    const outcome = await commit(view);
    const steps = outcome.errors.filter((error) => error.operationId.startsWith('numbering-recovery-'));
    expect(steps).toHaveLength(1);
    expect(steps[0].channelName).toBe('ESPN');
    expect(steps[0].error).toContain('back to channel number 5');
  });
});

/** The operations of the `index`th bulk-commit request. */
function sentOperations(index = 0): Record<string, unknown>[] {
  return (bulkCommit.mock.calls[index][0] as { operations: Record<string, unknown>[] }).operations;
}

describe('a range assignment carries a baseline for every existing channel', () => {
  it('sends the session baseline for each channel in the range', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageBulkAssignNumbers([1, 2], 100, 'ESPN and TNT from 100');
    });
    await commit(view);
    expect(sentOperations()[0]).toMatchObject({
      type: 'bulkAssignChannelNumbers',
      channelIds: [1, 2],
      startingNumber: 100,
      expectedNumbers: { 1: 5, 2: 6 },
    });
  });

  it('keep-mine on a range sends the server number the operator agreed to overwrite', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageBulkAssignNumbers([1, 2], 100, 'ESPN and TNT from 100');
    });
    const moved = [makeChannel(1, 'ESPN', 88), CHANNELS[1], CHANNELS[2]];
    serverHolds(moved);
    const first = await commit(view);
    expect(first.numberingConflicts![0]).toMatchObject({ channelId: 1, fromRangeAssignment: true });

    act(() => {
      view.result.current.reconcileNumberingConflicts(
        [{ channelId: 1, choice: 'keep-mine', baselineNumber: 5, serverNumber: 88 }],
        first.serverChannels!,
      );
    });
    const second = await commit(view);
    expect(second.success).toBe(true);
    expect(sentOperations()[0]).toMatchObject({
      type: 'bulkAssignChannelNumbers',
      expectedNumbers: { 1: 88, 2: 6 },
    });
    // The acknowledgement is browser bookkeeping; the server gets the number.
    expect(sentOperations()[0]).not.toHaveProperty('acknowledgedConcurrentChanges');
  });

  it('take-theirs on a range drops the whole range, so nothing is sent', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageBulkAssignNumbers([1, 2], 100, 'ESPN and TNT from 100');
    });
    serverHolds([makeChannel(1, 'ESPN', 88), CHANNELS[1], CHANNELS[2]]);
    const first = await commit(view);
    act(() => {
      view.result.current.reconcileNumberingConflicts(
        [{ channelId: 1, choice: 'take-theirs', baselineNumber: 5, serverNumber: 88 }],
        first.serverChannels!,
      );
    });
    expect(view.result.current.stagedOperationCount).toBe(0);
  });

  it('reports a server-side range refusal and keeps the range staged', async () => {
    // The browser's read agreed with the baseline; the executor's later read
    // did not. The server refuses the range and writes nothing.
    const { view } = setup();
    act(() => {
      view.result.current.stageBulkAssignNumbers([1, 2], 100, 'ESPN and TNT from 100');
    });
    bulkCommit.mockResolvedValue({
      success: false,
      operationsApplied: 0,
      operationsFailed: 1,
      errors: [{
        operationId: 'op-0-bulkAssignChannelNumbers',
        operationType: 'bulkAssignChannelNumbers',
        error: 'Channel was not renumbered: channel 2 is on channel number 9, and this '
          + 'request expected 6. Somebody else changed it.',
        concurrentNumberChanges: [{ channelId: 2, baselineNumber: 6, serverNumber: 9 }],
      }],
      tempIdMap: {},
      groupIdMap: {},
    } as unknown as Awaited<ReturnType<typeof api.bulkCommit>>);

    const outcome = await commit(view);
    expect(outcome.success).toBe(false);
    expect(outcome.errors[0]).toMatchObject({
      operationType: 'bulkAssignChannelNumbers',
      concurrentNumberChanges: [{ channelId: 2, baselineNumber: 6, serverNumber: 9 }],
    });
    expect(view.result.current.stagedOperationCount).toBe(1);

    // The next Apply reads the server again and raises the change as a
    // conflict the operator can reconcile, instead of sending the range.
    bulkCommit.mockClear();
    serverHolds([CHANNELS[0], makeChannel(2, 'TNT', 9), CHANNELS[2]]);
    const retry = await commit(view);
    expect(bulkCommit).not.toHaveBeenCalled();
    expect(retry.numberingConflicts![0]).toMatchObject({
      channelId: 2, baselineNumber: 6, serverNumber: 9, fromRangeAssignment: true,
    });
  });

  it('validation sends the same baselines Apply would', async () => {
    const { view } = setup();
    act(() => {
      view.result.current.stageBulkAssignNumbers([1, 2], 100, 'ESPN and TNT from 100');
      view.result.current.stageUpdateChannel(3, { channel_number: 300 }, 'AMC to 300');
    });
    bulkCommit.mockResolvedValue({
      success: true, operationsApplied: 0, operationsFailed: 0, errors: [],
      tempIdMap: {}, groupIdMap: {}, validationPassed: true, validationIssues: [],
    } as unknown as Awaited<ReturnType<typeof api.bulkCommit>>);
    await act(async () => {
      await view.result.current.validate();
    });
    const request = bulkCommit.mock.calls[0][0] as { operations: Record<string, unknown>[]; validateOnly?: boolean };
    expect(request.validateOnly).toBe(true);
    expect(request.operations).toEqual(expect.arrayContaining([
      expect.objectContaining({ type: 'bulkAssignChannelNumbers', expectedNumbers: { 1: 5, 2: 6 } }),
      expect.objectContaining({ type: 'updateChannel', channelId: 3, expectedNumber: { number: 7 } }),
    ]));
  });

  it('a channel created earlier in the Apply takes the number it was created on', async () => {
    // Creates are committed in a first request; the range naming the new
    // channel goes in a later one, where the channel already exists.
    const { view } = setup();
    let tempId = 0;
    act(() => {
      tempId = view.result.current.stageCreateChannel('Newcomer', 12);
    });
    act(() => {
      view.result.current.stageBulkAssignNumbers([1, tempId], 100, 'ESPN and Newcomer from 100');
    });
    bulkCommit
      .mockResolvedValueOnce({
        success: true, operationsApplied: 1, operationsFailed: 0, errors: [],
        tempIdMap: { [tempId]: 77 }, createdChannelNumbers: { 77: 12 }, groupIdMap: {},
      } as unknown as Awaited<ReturnType<typeof api.bulkCommit>>)
      .mockResolvedValue({
        success: true, operationsApplied: 1, operationsFailed: 0, errors: [],
        tempIdMap: {}, groupIdMap: {},
      } as unknown as Awaited<ReturnType<typeof api.bulkCommit>>);

    await commit(view);
    expect(sentOperations(0)[0]).toMatchObject({ type: 'createChannel', tempId });
    expect(sentOperations(1)[0]).toEqual({
      type: 'bulkAssignChannelNumbers',
      channelIds: [1, 77],
      startingNumber: 100,
      expectedNumbers: { 1: 5, 77: 12 },
    });
  });

  it('a renumber of a channel created earlier in the Apply takes the number it was created on', async () => {
    const { view } = setup();
    let tempId = 0;
    act(() => {
      tempId = view.result.current.stageCreateChannel('Newcomer');
    });
    act(() => {
      view.result.current.stageUpdateChannel(tempId, { channel_number: 400 }, 'Newcomer to 400');
    });
    bulkCommit
      .mockResolvedValueOnce({
        success: true, operationsApplied: 1, operationsFailed: 0, errors: [],
        tempIdMap: { [tempId]: 78 }, createdChannelNumbers: { 78: 431 }, groupIdMap: {},
      } as unknown as Awaited<ReturnType<typeof api.bulkCommit>>)
      .mockResolvedValue({
        success: true, operationsApplied: 1, operationsFailed: 0, errors: [],
        tempIdMap: {}, groupIdMap: {},
      } as unknown as Awaited<ReturnType<typeof api.bulkCommit>>);

    await commit(view);
    expect(sentOperations(1)[0]).toMatchObject({
      type: 'updateChannel',
      channelId: 78,
      data: { channel_number: 400 },
      expectedNumber: { number: 431 },
    });
  });
});
