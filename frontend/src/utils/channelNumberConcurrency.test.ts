/**
 * A stale edit session cannot silently overwrite a channel number somebody else
 * changed (bead `enhancedchannelmanager-ic884.4`).
 *
 * The invariant, which is what these tests are written against rather than the
 * scenario in the bead description:
 *
 *   A staged change never overwrites a server-side change made after the
 *   baseline was captured, without the operator being shown it and choosing.
 *
 * So the boundaries matter more than the happy path: canonical equivalents, one
 * decimal place, `null` on either side, values at and above `2**53`, a channel
 * deleted rather than moved, a decision that no longer describes the conflict
 * it was made about, and an unrelated change that must NOT block anything.
 */
import { describe, it, expect } from 'vitest';
import {
  applyReconcileDecisions,
  detectNumberingConflicts,
  expectedServerNumber,
  type ReconcileDecision,
} from './channelNumberConcurrency';
import type { ChannelSnapshot } from '../types';
import type { StagedOperation } from '../types/editMode';

function snapshot(id: number, name: string, channel_number: number | null): ChannelSnapshot {
  return { id, name, channel_number, channel_group_id: null, streams: [] };
}

function serverChannel(id: number, name: string, channel_number: number | null) {
  return { id, name, channel_number };
}

let operationCounter = 0;
function renumber(channelId: number, channel_number: number | null): StagedOperation {
  operationCounter += 1;
  return {
    id: `op-${operationCounter}`,
    timestamp: operationCounter,
    description: `Set channel ${channelId} to ${channel_number}`,
    apiCall: { type: 'updateChannel', channelId, data: { channel_number } },
    beforeSnapshot: [],
    afterSnapshot: [],
  };
}

function rename(channelId: number, name: string): StagedOperation {
  operationCounter += 1;
  return {
    id: `op-${operationCounter}`,
    timestamp: operationCounter,
    description: `Rename channel ${channelId}`,
    apiCall: { type: 'updateChannel', channelId, data: { name } },
    beforeSnapshot: [],
    afterSnapshot: [],
  };
}

function assignRange(channelIds: number[], startingNumber: number): StagedOperation {
  operationCounter += 1;
  return {
    id: `op-${operationCounter}`,
    timestamp: operationCounter,
    description: `Renumber ${channelIds.length} channels from ${startingNumber}`,
    apiCall: { type: 'bulkAssignChannelNumbers', channelIds, startingNumber },
    beforeSnapshot: [],
    afterSnapshot: [],
  };
}

function detect(
  baseline: ChannelSnapshot[],
  server: { id: number; name: string; channel_number: number | null }[],
  operations: StagedOperation[],
) {
  return detectNumberingConflicts({ baseline, server, operations });
}

describe('detectNumberingConflicts', () => {
  it('reports nothing when the server still holds the baseline numbers', () => {
    const conflicts = detect(
      [snapshot(1, 'ESPN', 5), snapshot(2, 'TNT', 6)],
      [serverChannel(1, 'ESPN', 5), serverChannel(2, 'TNT', 6)],
      [renumber(1, 20)],
    );
    expect(conflicts).toEqual([]);
  });

  it('reports a channel whose number moved under the session', () => {
    const conflicts = detect(
      [snapshot(1, 'ESPN', 5)],
      [serverChannel(1, 'ESPN', 88)],
      [renumber(1, 20)],
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]).toMatchObject({
      kind: 'number-changed',
      channelId: 1,
      baselineNumber: 5,
      serverNumber: 88,
      proposedNumber: 20,
    });
  });

  it('names the staged operations the operator can recognise', () => {
    const operation = renumber(1, 20);
    const conflicts = detect(
      [snapshot(1, 'ESPN', 5)],
      [serverChannel(1, 'ESPN', 88)],
      [operation],
    );
    expect(conflicts[0].operationIds).toEqual([operation.id]);
    expect(conflicts[0].operationDescriptions).toEqual([operation.description]);
  });

  it('does not report a channel this session does not renumber', () => {
    const conflicts = detect(
      [snapshot(1, 'ESPN', 5), snapshot(2, 'TNT', 6)],
      [serverChannel(1, 'ESPN', 5), serverChannel(2, 'TNT', 999)],
      [renumber(1, 20)],
    );
    expect(conflicts).toEqual([]);
  });

  it('does not report a channel this session only renames', () => {
    const conflicts = detect(
      [snapshot(1, 'ESPN', 5)],
      [serverChannel(1, 'ESPN', 88)],
      [rename(1, 'ESPN HD')],
    );
    expect(conflicts).toEqual([]);
  });

  it('reports a change even when the session would write the same number back', () => {
    // Agreeing by accident is not agreeing: somebody moved this channel and
    // the operator has not been told.
    const conflicts = detect(
      [snapshot(1, 'ESPN', 5)],
      [serverChannel(1, 'ESPN', 88)],
      [renumber(1, 5)],
    );
    expect(conflicts).toHaveLength(1);
  });

  it('treats canonical equivalents as no change at all', () => {
    const conflicts = detect(
      [snapshot(1, 'ESPN', 7)],
      [serverChannel(1, 'ESPN', 7.0)],
      [renumber(1, 20)],
    );
    expect(conflicts).toEqual([]);
  });

  it('treats a tenth of a channel apart as a change', () => {
    const conflicts = detect(
      [snapshot(1, 'ESPN', 7)],
      [serverChannel(1, 'ESPN', 7.1)],
      [renumber(1, 20)],
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0].serverNumber).toBe(7.1);
  });

  it('treats two unassigned numbers as no change', () => {
    const conflicts = detect(
      [snapshot(1, 'ESPN', null)],
      [serverChannel(1, 'ESPN', null)],
      [renumber(1, 20)],
    );
    expect(conflicts).toEqual([]);
  });

  it('treats a number appearing where there was none as a change', () => {
    const conflicts = detect(
      [snapshot(1, 'ESPN', null)],
      [serverChannel(1, 'ESPN', 4)],
      [renumber(1, 20)],
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0].baselineNumber).toBeNull();
  });

  it('treats a number being cleared as a change', () => {
    const conflicts = detect(
      [snapshot(1, 'ESPN', 4)],
      [serverChannel(1, 'ESPN', null)],
      [renumber(1, 20)],
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0].serverNumber).toBeNull();
  });

  it('compares correctly at and above the exact-integer floor', () => {
    const floor = 2 ** 53;
    expect(
      detect(
        [snapshot(1, 'ESPN', floor)],
        [serverChannel(1, 'ESPN', floor)],
        [renumber(1, 20)],
      ),
    ).toEqual([]);
    expect(
      detect(
        [snapshot(1, 'ESPN', floor)],
        [serverChannel(1, 'ESPN', floor + 2)],
        [renumber(1, 20)],
      ),
    ).toHaveLength(1);
  });

  it('reports a channel the plan renumbers that is no longer on the server', () => {
    const conflicts = detect(
      [snapshot(1, 'ESPN', 5)],
      [],
      [renumber(1, 20)],
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0].kind).toBe('channel-deleted');
    expect(conflicts[0].serverNumber).toBeNull();
  });

  it('never reports a channel this session is creating', () => {
    operationCounter += 1;
    const create: StagedOperation = {
      id: `op-${operationCounter}`,
      timestamp: operationCounter,
      description: 'Create "New"',
      apiCall: { type: 'createChannel', name: 'New', channelNumber: 42 },
      beforeSnapshot: [],
      afterSnapshot: [{ id: -1, name: 'New', channel_number: 42, channel_group_id: null, streams: [] }],
    };
    expect(detect([], [], [create])).toEqual([]);
  });

  it('reports every channel a range assignment moves that has drifted', () => {
    const conflicts = detect(
      [snapshot(1, 'A', 1), snapshot(2, 'B', 2), snapshot(3, 'C', 3)],
      [serverChannel(1, 'A', 1), serverChannel(2, 'B', 77), serverChannel(3, 'C', 78)],
      [assignRange([1, 2, 3], 10)],
    );
    expect(conflicts.map((conflict) => conflict.channelId)).toEqual([2, 3]);
    expect(conflicts.every((conflict) => conflict.fromRangeAssignment)).toBe(true);
  });
});

describe('a reconcile decision is bound to the conflict it resolved', () => {
  it('stops reporting a conflict the operator agreed to overwrite', () => {
    const operation = renumber(1, 20);
    const baseline = [snapshot(1, 'ESPN', 5)];
    const server = [serverChannel(1, 'ESPN', 88)];
    const decision: ReconcileDecision = {
      channelId: 1,
      choice: 'keep-mine',
      baselineNumber: 5,
      serverNumber: 88,
    };
    const { operations } = applyReconcileDecisions([operation], [decision]);
    expect(detect(baseline, server, operations)).toEqual([]);
  });

  it('reports again when the server value moves after the decision', () => {
    const operation = renumber(1, 20);
    const { operations } = applyReconcileDecisions(
      [operation],
      [{ channelId: 1, choice: 'keep-mine', baselineNumber: 5, serverNumber: 88 }],
    );
    const conflicts = detect(
      [snapshot(1, 'ESPN', 5)],
      [serverChannel(1, 'ESPN', 99)],
      operations,
    );
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0].serverNumber).toBe(99);
  });

  it('does not let a decision about one channel authorise another', () => {
    const first = renumber(1, 20);
    const second = renumber(2, 21);
    const { operations } = applyReconcileDecisions(
      [first, second],
      [{ channelId: 1, choice: 'keep-mine', baselineNumber: 5, serverNumber: 88 }],
    );
    const conflicts = detect(
      [snapshot(1, 'ESPN', 5), snapshot(2, 'TNT', 6)],
      [serverChannel(1, 'ESPN', 88), serverChannel(2, 'TNT', 89)],
      operations,
    );
    expect(conflicts.map((conflict) => conflict.channelId)).toEqual([2]);
  });

  it('carries one acknowledgement per channel on a range assignment', () => {
    const range = assignRange([1, 2], 10);
    const { operations } = applyReconcileDecisions(
      [range],
      [
        { channelId: 1, choice: 'keep-mine', baselineNumber: 1, serverNumber: 71 },
        { channelId: 2, choice: 'keep-mine', baselineNumber: 2, serverNumber: 72 },
      ],
    );
    const apiCall = operations[0].apiCall;
    expect(apiCall.type).toBe('bulkAssignChannelNumbers');
    if (apiCall.type !== 'bulkAssignChannelNumbers') throw new Error('unreachable');
    expect(apiCall.acknowledgedConcurrentChanges).toEqual([
      { channelId: 1, baselineNumber: 1, serverNumber: 71 },
      { channelId: 2, baselineNumber: 2, serverNumber: 72 },
    ]);
  });
});

describe('applyReconcileDecisions', () => {
  it('take-theirs removes the number from an update that carried more', () => {
    operationCounter += 1;
    const operation: StagedOperation = {
      id: `op-${operationCounter}`,
      timestamp: operationCounter,
      description: 'Edit ESPN',
      apiCall: { type: 'updateChannel', channelId: 1, data: { channel_number: 20, name: 'ESPN HD' } },
      beforeSnapshot: [],
      afterSnapshot: [],
    };
    const { operations, removed } = applyReconcileDecisions(
      [operation],
      [{ channelId: 1, choice: 'take-theirs', baselineNumber: 5, serverNumber: 88 }],
    );
    expect(removed).toEqual([]);
    expect(operations).toHaveLength(1);
    expect(operations[0].apiCall).toMatchObject({ data: { name: 'ESPN HD' } });
    expect((operations[0].apiCall as { data: Record<string, unknown> }).data)
      .not.toHaveProperty('channel_number');
  });

  it('take-theirs removes an update that carried only the number', () => {
    const operation = renumber(1, 20);
    const { operations, removed } = applyReconcileDecisions(
      [operation],
      [{ channelId: 1, choice: 'take-theirs', baselineNumber: 5, serverNumber: 88 }],
    );
    expect(operations).toEqual([]);
    expect(removed).toHaveLength(1);
    expect(removed[0].id).toBe(operation.id);
  });

  it('take-theirs on a range drops the whole range and says why', () => {
    const range = assignRange([1, 2, 3], 10);
    const { operations, removed } = applyReconcileDecisions(
      [range],
      [{ channelId: 2, choice: 'take-theirs', baselineNumber: 2, serverNumber: 77 }],
    );
    expect(operations).toEqual([]);
    expect(removed).toHaveLength(1);
    expect(removed[0].detail).toContain('sequence');
  });

  it('leaves every operation it was given no decision about exactly as it was', () => {
    const untouched = rename(9, 'Nine');
    const { operations, removed } = applyReconcileDecisions([untouched], []);
    expect(operations).toEqual([untouched]);
    expect(removed).toEqual([]);
  });
});

describe('expectedServerNumber', () => {
  it('is the baseline when the operator made no decision', () => {
    expect(expectedServerNumber(renumber(1, 20), 1, 5)).toBe(5);
  });

  it('is the value the operator agreed to overwrite once they decided', () => {
    const { operations } = applyReconcileDecisions(
      [renumber(1, 20)],
      [{ channelId: 1, choice: 'keep-mine', baselineNumber: 5, serverNumber: 88 }],
    );
    expect(expectedServerNumber(operations[0], 1, 5)).toBe(88);
  });
});

describe('take-theirs withdraws the rename the surrendered number caused', () => {
  function numberEditWithAutoRename(): StagedOperation {
    operationCounter += 1;
    return {
      id: `op-${operationCounter}`,
      timestamp: operationCounter,
      description: 'Changed "5 | ESPN" to "150 | ESPN"',
      apiCall: {
        type: 'updateChannel',
        channelId: 1,
        data: { channel_number: 150, name: '150 | ESPN' },
      },
      beforeSnapshot: [snapshot(1, '5 | ESPN', 5)],
      afterSnapshot: [snapshot(1, '150 | ESPN', 150)],
    };
  }

  it('drops the automatic name along with the number', () => {
    // Keeping it would leave the channel called "150 | ESPN" while it sits on
    // the server's 199 — a state nobody chose and nobody was shown.
    const { operations, removed } = applyReconcileDecisions(
      [numberEditWithAutoRename()],
      [{ channelId: 1, choice: 'take-theirs', baselineNumber: 5, serverNumber: 199 }],
    );
    expect(operations).toEqual([]);
    expect(removed).toHaveLength(1);
  });

  it('keeps a name the operator typed', () => {
    operationCounter += 1;
    const typed: StagedOperation = {
      id: `op-${operationCounter}`,
      timestamp: operationCounter,
      description: 'Edit ESPN',
      apiCall: {
        type: 'updateChannel',
        channelId: 1,
        data: { channel_number: 150, name: 'ESPN Deportes' },
      },
      beforeSnapshot: [snapshot(1, '5 | ESPN', 5)],
      afterSnapshot: [],
    };
    const { operations, removed } = applyReconcileDecisions(
      [typed],
      [{ channelId: 1, choice: 'take-theirs', baselineNumber: 5, serverNumber: 199 }],
    );
    expect(removed).toEqual([]);
    expect(operations[0].apiCall).toMatchObject({ data: { name: 'ESPN Deportes' } });
    expect((operations[0].apiCall as { data: Record<string, unknown> }).data)
      .not.toHaveProperty('channel_number');
  });

  it('keep-mine leaves the automatic name exactly where it was', () => {
    const { operations } = applyReconcileDecisions(
      [numberEditWithAutoRename()],
      [{ channelId: 1, choice: 'keep-mine', baselineNumber: 5, serverNumber: 199 }],
    );
    expect(operations[0].apiCall).toMatchObject({
      data: { channel_number: 150, name: '150 | ESPN' },
    });
  });
});
