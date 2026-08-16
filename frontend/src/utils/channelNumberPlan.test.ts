import { describe, it, expect } from 'vitest';
import type { StagedOperation } from '../types/editMode';
import type { ChannelSnapshot } from '../types';
import {
  buildFinalNumberingPlan,
  channelNumberRangeError,
  channelsHoldingNumber,
  deriveAutomaticRenames,
  sameChannelNumber,
  validateFinalNumberingPlan,
  type NumberedChannel,
} from './channelNumberPlan';

function channel(id: number, channel_number: number | null, name = `Channel ${id}`): NumberedChannel {
  return { id, name, channel_number };
}

function snapshot(id: number, channel_number: number | null, name = `Channel ${id}`): ChannelSnapshot {
  return { id, channel_number, name, channel_group_id: null, streams: [] };
}

let opSeq = 0;
function op(
  apiCall: StagedOperation['apiCall'],
  before: ChannelSnapshot[] = [],
  description = 'staged change',
): StagedOperation {
  opSeq += 1;
  return {
    id: `op-${opSeq}`,
    timestamp: 1_700_000_000_000 + opSeq,
    description,
    apiCall,
    beforeSnapshot: before,
    afterSnapshot: [],
  };
}

describe('sameChannelNumber', () => {
  it('treats canonical equivalents as the same number', () => {
    expect(sameChannelNumber(7, 7.0)).toBe(true);
    expect(sameChannelNumber(1.1, 1.1)).toBe(true);
    // Arithmetic drift lands on the same tenth.
    expect(sameChannelNumber(0.7 + 0.1, 0.8)).toBe(true);
  });

  it('keeps distinct tenths distinct', () => {
    expect(sameChannelNumber(1.1, 1.2)).toBe(false);
    expect(sameChannelNumber(0, 0.1)).toBe(false);
  });

  it('never matches an unassigned number, not even another unassigned one', () => {
    // Two unnumbered channels do not collide: `null` is not a slot.
    expect(sameChannelNumber(null, null)).toBe(false);
    expect(sameChannelNumber(null, 5)).toBe(false);
    expect(sameChannelNumber(5, undefined)).toBe(false);
  });

  it('compares exactly at 2**53 and above, where floats are integers', () => {
    const floor = 2 ** 53;
    expect(sameChannelNumber(floor, floor)).toBe(true);
    expect(sameChannelNumber(floor, floor + 2)).toBe(false);
    expect(sameChannelNumber(1e308, 1e308)).toBe(true);
  });
});

describe('channelsHoldingNumber', () => {
  const lineup = [channel(1, 5, 'ESPN'), channel(2, 6), channel(3, 5, 'ESPN2'), channel(4, null)];

  it('finds every channel on the number, canonically', () => {
    expect(channelsHoldingNumber(lineup, 5.0).map((c) => c.id)).toEqual([1, 3]);
  });

  it('excludes the channel being edited so keeping its own number never conflicts', () => {
    expect(channelsHoldingNumber(lineup, 5, [1]).map((c) => c.id)).toEqual([3]);
  });

  it('returns nothing for an unassigned proposal', () => {
    expect(channelsHoldingNumber(lineup, null)).toEqual([]);
  });
});

describe('buildFinalNumberingPlan', () => {
  it('reflects staged edits rather than the server-loaded list', () => {
    const base = [channel(1, 5), channel(2, 6)];
    const plan = buildFinalNumberingPlan(base, [
      op({ type: 'updateChannel', channelId: 2, data: { channel_number: 9 } }, [snapshot(2, 6)]),
    ]);
    expect(plan.placements.find((p) => p.channelId === 2)?.number).toBe(9);
  });

  it('includes channels created in this session', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 5)],
      [op({ type: 'createChannel', name: 'New', channelNumber: 7 }, [])],
    );
    const created = plan.placements.filter((p) => p.channelId < 0);
    expect(created).toHaveLength(1);
    expect(created[0].number).toBe(7);
  });

  it('drops channels staged for deletion so they cannot cause a false conflict', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 5), channel(2, 6)],
      [op({ type: 'deleteChannel', channelId: 1 }, [snapshot(1, 5)])],
    );
    expect(plan.placements.map((p) => p.channelId)).toEqual([2]);
  });

  it('expands a bulk range into one placement per channel', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, null), channel(2, null), channel(3, null)],
      [op({ type: 'bulkAssignChannelNumbers', channelIds: [1, 2, 3], startingNumber: 10 })],
    );
    expect(plan.placements.map((p) => p.number)).toEqual([10, 11, 12]);
  });

  it('does not treat re-asserting a channel\'s own number as a placement', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 5), channel(2, 5)],
      [op({ type: 'updateChannel', channelId: 1, data: { channel_number: 5.0 } }, [snapshot(1, 5)])],
    );
    expect(plan.placements.find((p) => p.channelId === 1)?.placedByOperationIds).toEqual([]);
  });

  it('does not call an unassigned channel re-asserted as unassigned a placement', () => {
    // The `moved` clause's stated property is "the session put this channel
    // where it now is". `null` is unassigned, and unassigned is where this
    // channel already was — `sameChannelNumber(null, null)` is deliberately
    // false because two unnumbered channels do not collide, which is a
    // different question from whether this one moved.
    const plan = buildFinalNumberingPlan(
      [channel(1, null)],
      [op({ type: 'updateChannel', channelId: 1, data: { channel_number: null } }, [snapshot(1, null)])],
    );
    expect(plan.placements.find((p) => p.channelId === 1)?.placedByOperationIds).toEqual([]);
  });

  it('calls clearing an assigned number a placement, and assigning a cleared one too', () => {
    const cleared = buildFinalNumberingPlan(
      [channel(1, 5)],
      [op({ type: 'updateChannel', channelId: 1, data: { channel_number: null } }, [snapshot(1, 5)])],
    );
    expect(cleared.placements[0].placedByOperationIds).toHaveLength(1);
    const assigned = buildFinalNumberingPlan(
      [channel(1, null)],
      [op({ type: 'updateChannel', channelId: 1, data: { channel_number: 5 } }, [snapshot(1, null)])],
    );
    expect(assigned.placements[0].placedByOperationIds).toHaveLength(1);
  });

  it('records which operation placed a channel on its final number', () => {
    const move = op(
      { type: 'updateChannel', channelId: 2, data: { channel_number: 5 } },
      [snapshot(2, 6)],
      'Changed channel number from 6 to 5',
    );
    const plan = buildFinalNumberingPlan([channel(1, 5), channel(2, 6)], [move]);
    expect(plan.placements.find((p) => p.channelId === 2)?.placedByOperationIds).toEqual([move.id]);
  });
});

describe('validateFinalNumberingPlan', () => {
  it('passes a plan whose combined final state has no new collision', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 5), channel(2, 6)],
      [op({ type: 'updateChannel', channelId: 2, data: { channel_number: 7 } }, [snapshot(2, 6)])],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });

  it('blocks a collision only the COMBINED result creates', () => {
    // Each edit is individually fine: 1 vacates 5, 2 takes 5, 3 also takes 5.
    // Only the combination puts two channels on 5.
    const a = op({ type: 'updateChannel', channelId: 2, data: { channel_number: 5 } }, [snapshot(2, 6)]);
    const b = op({ type: 'updateChannel', channelId: 3, data: { channel_number: 5 } }, [snapshot(3, 7)]);
    const plan = buildFinalNumberingPlan([channel(1, 5), channel(2, 6), channel(3, 7)], [
      op({ type: 'updateChannel', channelId: 1, data: { channel_number: 100 } }, [snapshot(1, 5)]),
      a,
      b,
    ]);
    const issues = validateFinalNumberingPlan(plan);
    expect(issues).toHaveLength(1);
    expect(issues[0].type).toBe('duplicate_channel_number');
    expect(issues[0].channelNumber).toBe(5);
    expect(issues[0].channelIds.sort()).toEqual([2, 3]);
    // Only `b` is named. `a` landed on 5 while 1 was already vacating it, so
    // `a` collided with nobody and no dialog could have fired for it; `b` is
    // the arrival that created the duplicate and the one to change. Naming
    // both also produced a degenerate sentence — "\"2\", \"3\" would join ."
    // with nothing on the right-hand side.
    expect(issues[0].operationIds).toEqual([b.id]);
    expect(issues[0].message).toContain('would join');
  });

  it('leaves a duplicate that existed on the server alone', () => {
    // ic884.1 deliberately declined to enforce uniqueness. A pre-existing
    // duplicate is not this session's business.
    const plan = buildFinalNumberingPlan(
      [channel(1, 5), channel(2, 5), channel(3, 9)],
      [op({ type: 'updateChannel', channelId: 3, data: { name: 'Renamed' } }, [snapshot(3, 9)])],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });

  it('does not report a pre-existing duplicate just because one member was renamed onto its own number', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 5), channel(2, 5)],
      [op({ type: 'updateChannel', channelId: 1, data: { channel_number: 5 } }, [snapshot(1, 5)])],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });

  it('accepts a duplicate the operator acknowledged at staging time', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 5, 'ESPN'), channel(2, 6)],
      [
        op(
          {
            type: 'updateChannel',
            channelId: 2,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [1] },
          },
          [snapshot(2, 6)],
        ),
      ],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });

  it('re-litigates nothing: an acknowledgement holds at any canonical spelling', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 5, 'ESPN'), channel(2, 6)],
      [
        op(
          {
            type: 'updateChannel',
            channelId: 2,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5.0, occupantChannelIds: [1] },
          },
          [snapshot(2, 6)],
        ),
      ],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });

  it('still blocks when a LATER unacknowledged operation joins an acknowledged duplicate', () => {
    const acked = op(
      { type: 'updateChannel', channelId: 2, data: { channel_number: 5 }, acknowledgedDuplicate: { number: 5, occupantChannelIds: [1] } },
      [snapshot(2, 6)],
    );
    const accidental = op(
      { type: 'updateChannel', channelId: 3, data: { channel_number: 5 } },
      [snapshot(3, 7)],
      'Renumber group',
    );
    const plan = buildFinalNumberingPlan(
      [channel(1, 5, 'ESPN'), channel(2, 6), channel(3, 7)],
      [acked, accidental],
    );
    const issues = validateFinalNumberingPlan(plan);
    expect(issues).toHaveLength(1);
    // Only the unacknowledged operation is named — the acknowledged one is
    // a decision the operator already made.
    expect(issues[0].operationIds).toEqual([accidental.id]);
  });

  // ---------------------------------------------------------------- binding
  //
  // Fix round 2 of bead enhancedchannelmanager-vdxbx. An acknowledgement
  // authorises exactly one collision: this channel, this number, this set of
  // occupants. These four hold the ways it must NOT be reusable.

  it('does not let an earlier acknowledgement authorise a later unacknowledged placement', () => {
    // A is moved onto occupied 5 WITH consent, then to 6, then back onto 5
    // with nobody asked. The final placement is the unacknowledged one.
    const plan = buildFinalNumberingPlan(
      [channel(1, 5, 'ESPN'), channel(2, 6, 'TNT')],
      [
        op(
          {
            type: 'updateChannel',
            channelId: 2,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [1] },
          },
          [snapshot(2, 6)],
        ),
        op({ type: 'updateChannel', channelId: 2, data: { channel_number: 6 } }, [snapshot(2, 5)]),
        op({ type: 'updateChannel', channelId: 2, data: { channel_number: 5 } }, [snapshot(2, 6)]),
      ],
    );
    const issues = validateFinalNumberingPlan(plan);
    expect(issues).toHaveLength(1);
    expect(issues[0].type).toBe('duplicate_channel_number');
  });

  it('keeps an acknowledgement across a later edit that does not move the channel', () => {
    // The control for the arm above: only a re-PLACEMENT withdraws consent.
    const plan = buildFinalNumberingPlan(
      [channel(1, 5, 'ESPN'), channel(2, 6, 'TNT')],
      [
        op(
          {
            type: 'updateChannel',
            channelId: 2,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [1] },
          },
          [snapshot(2, 6)],
        ),
        op({ type: 'updateChannel', channelId: 2, data: { name: 'TNT HD' } }, [snapshot(2, 5)]),
      ],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });

  it('refuses an acknowledgement whose occupants are not the ones now on the number', () => {
    // The restore case, reached the way restore reaches it: the ledger was
    // staged against a lineup where 5 belonged to ESPN, and by the time it is
    // planned again 5 belongs to somebody else entirely. Consent to join ESPN
    // is not consent to join AMC.
    const plan = buildFinalNumberingPlan(
      [channel(3, 5, 'AMC'), channel(2, 6, 'TNT')],
      [
        op(
          {
            type: 'updateChannel',
            channelId: 2,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [1] },
          },
          [snapshot(2, 6)],
        ),
      ],
    );
    const issues = validateFinalNumberingPlan(plan);
    expect(issues).toHaveLength(1);
    expect(issues[0].channelIds.sort()).toEqual([2, 3]);
  });

  it('accepts a pile-up in which each newcomer consented to everyone already there', () => {
    // Not every multi-way duplicate is accidental. B joins A on 5 having been
    // shown A; C then joins having been shown A and B. Nobody is surprised.
    const plan = buildFinalNumberingPlan(
      [channel(1, 5, 'ESPN'), channel(2, 6, 'TNT'), channel(3, 7, 'AMC')],
      [
        op(
          {
            type: 'updateChannel',
            channelId: 2,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [1] },
          },
          [snapshot(2, 6)],
        ),
        op(
          {
            type: 'updateChannel',
            channelId: 3,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [1, 2] },
          },
          [snapshot(3, 7)],
        ),
      ],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });

  it('names the conflicting channels well enough to act on', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 5, 'ESPN'), channel(2, 6, 'TNT')],
      [op({ type: 'updateChannel', channelId: 2, data: { channel_number: 5 } }, [snapshot(2, 6, 'TNT')], 'Moved TNT')],
    );
    const [issue] = validateFinalNumberingPlan(plan);
    expect(issue.message).toContain('5');
    expect(issue.message).toContain('ESPN');
    expect(issue.message).toContain('TNT');
    expect(issue.operationDescriptions).toEqual(['Moved TNT']);
  });

  it('rejects a final number outside the canonical contract, whatever produced it', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 5)],
      [op({ type: 'updateChannel', channelId: 1, data: { channel_number: 1.05 } }, [snapshot(1, 5)])],
    );
    const issues = validateFinalNumberingPlan(plan);
    expect(issues).toHaveLength(1);
    expect(issues[0].type).toBe('invalid_channel_number');
  });

  it('rejects a range whose tail runs past exact-integer representability', () => {
    const start = 2 ** 53 - 1;
    const plan = buildFinalNumberingPlan(
      [channel(1, null), channel(2, null), channel(3, null)],
      [op({ type: 'bulkAssignChannelNumbers', channelIds: [1, 2, 3], startingNumber: start })],
    );
    const issues = validateFinalNumberingPlan(plan);
    // Beyond 2**53 consecutive integers stop being distinct, so the range
    // silently lands several channels on one number.
    expect(issues.length).toBeGreaterThan(0);
  });

  it('holds at large in-contract magnitudes without inventing a conflict', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 10000000.1), channel(2, 10000000.2)],
      [op({ type: 'updateChannel', channelId: 2, data: { channel_number: 10000000.3 } }, [snapshot(2, 10000000.2)])],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });

  it('detects a conflict at large in-contract magnitudes', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 10000000.1), channel(2, 10000000.2)],
      [op({ type: 'updateChannel', channelId: 2, data: { channel_number: 10000000.1 } }, [snapshot(2, 10000000.2)])],
    );
    expect(validateFinalNumberingPlan(plan)).toHaveLength(1);
  });

  it('treats clearing a number as vacating a slot, never as occupying one', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 5), channel(2, null)],
      [op({ type: 'updateChannel', channelId: 1, data: { channel_number: null } }, [snapshot(1, 5)])],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });

  it('terminates and reports once per number, however many channels pile onto it', () => {
    const ops = [2, 3, 4, 5].map((id) =>
      op({ type: 'updateChannel', channelId: id, data: { channel_number: 5 } }, [snapshot(id, id + 10)]),
    );
    const plan = buildFinalNumberingPlan(
      [channel(1, 5), channel(2, 12), channel(3, 13), channel(4, 14), channel(5, 15)],
      ops,
    );
    const issues = validateFinalNumberingPlan(plan);
    expect(issues).toHaveLength(1);
    expect(issues[0].channelIds).toHaveLength(5);
  });
});

describe('channelNumberRangeError', () => {
  it('accepts an ordinary range', () => {
    expect(channelNumberRangeError(100, 50)).toBeNull();
  });

  it('accepts a single-channel range', () => {
    expect(channelNumberRangeError(1, 1)).toBeNull();
  });

  it('refuses a range whose last number is not exactly representable', () => {
    expect(channelNumberRangeError(2 ** 53 - 1, 5)).not.toBeNull();
  });

  it('refuses a start outside the canonical contract', () => {
    expect(channelNumberRangeError(1.05, 3)).not.toBeNull();
    expect(channelNumberRangeError(-1, 3)).not.toBeNull();
    expect(channelNumberRangeError(Number.NaN, 3)).not.toBeNull();
  });

  it('refuses a non-positive count', () => {
    expect(channelNumberRangeError(1, 0)).not.toBeNull();
  });

  it('accepts a tenth-step range and refuses one that leaves the tenths grid', () => {
    expect(channelNumberRangeError(1, 5, 0.1)).toBeNull();
    expect(channelNumberRangeError(1, 5, 0.05)).not.toBeNull();
  });
});

describe('deriveAutomaticRenames', () => {
  it('reports a rename the numbering change caused', () => {
    const renames = deriveAutomaticRenames(
      buildFinalNumberingPlan(
        [channel(1, 5, '5 | ESPN')],
        [op(
          { type: 'updateChannel', channelId: 1, data: { channel_number: 9, name: '9 | ESPN' } },
          [snapshot(1, 5, '5 | ESPN')],
        )],
      ),
    );
    expect(renames).toEqual([
      { channelId: 1, from: '5 | ESPN', to: '9 | ESPN', fromNumber: 5, toNumber: 9 },
    ]);
  });

  it('ignores a rename the operator typed themselves', () => {
    const renames = deriveAutomaticRenames(
      buildFinalNumberingPlan(
        [channel(1, 5, '5 | ESPN')],
        [op(
          { type: 'updateChannel', channelId: 1, data: { channel_number: 9, name: 'Something Else' } },
          [snapshot(1, 5, '5 | ESPN')],
        )],
      ),
    );
    expect(renames).toEqual([]);
  });

  it('ignores a plain rename with no numbering change', () => {
    const renames = deriveAutomaticRenames(
      buildFinalNumberingPlan(
        [channel(1, 5, '5 | ESPN')],
        [op({ type: 'updateChannel', channelId: 1, data: { name: 'New Name' } }, [snapshot(1, 5, '5 | ESPN')])],
      ),
    );
    expect(renames).toEqual([]);
  });

  it('reports every automatic rename in a bulk renumber', () => {
    const renames = deriveAutomaticRenames(
      buildFinalNumberingPlan(
        [channel(1, 1, '1 | A'), channel(2, 2, '2 | B')],
        [
          op({ type: 'updateChannel', channelId: 1, data: { channel_number: 10, name: '10 | A' } }, [snapshot(1, 1, '1 | A')]),
          op({ type: 'updateChannel', channelId: 2, data: { channel_number: 11, name: '11 | B' } }, [snapshot(2, 2, '2 | B')]),
        ],
      ),
    );
    expect(renames.map((r) => r.channelId)).toEqual([1, 2]);
  });

  it('is empty when auto-rename is off, because nothing stages a name', () => {
    const renames = deriveAutomaticRenames(
      buildFinalNumberingPlan(
        [channel(1, 5, '5 | ESPN')],
        [op({ type: 'updateChannel', channelId: 1, data: { channel_number: 9 } }, [snapshot(1, 5, '5 | ESPN')])],
      ),
    );
    expect(renames).toEqual([]);
  });

  // Fix round 2 of bead enhancedchannelmanager-ic884.5: the preview has to
  // describe the state Apply will produce, not an intermediate one.

  it('drops a rename a later edit superseded', () => {
    const renames = deriveAutomaticRenames(
      buildFinalNumberingPlan(
        [channel(1, 5, '5 | ESPN')],
        [
          op({ type: 'updateChannel', channelId: 1, data: { channel_number: 6, name: '6 | ESPN' } }, [snapshot(1, 5, '5 | ESPN')]),
          op({ type: 'updateChannel', channelId: 1, data: { name: 'ESPN HD' } }, [snapshot(1, 6, '6 | ESPN')]),
        ],
      ),
    );
    expect(renames).toEqual([]);
  });

  it('reports only the last of a chain of automatic renames', () => {
    const renames = deriveAutomaticRenames(
      buildFinalNumberingPlan(
        [channel(1, 5, '5 | ESPN')],
        [
          op({ type: 'updateChannel', channelId: 1, data: { channel_number: 6, name: '6 | ESPN' } }, [snapshot(1, 5, '5 | ESPN')]),
          op({ type: 'updateChannel', channelId: 1, data: { channel_number: 7, name: '7 | ESPN' } }, [snapshot(1, 6, '6 | ESPN')]),
        ],
      ),
    );
    expect(renames).toEqual([
      { channelId: 1, from: '5 | ESPN', to: '7 | ESPN', fromNumber: 5, toNumber: 7 },
    ]);
  });

  it('reports nothing for a channel returned to the name and number it started on', () => {
    const renames = deriveAutomaticRenames(
      buildFinalNumberingPlan(
        [channel(1, 5, '5 | ESPN')],
        [
          op({ type: 'updateChannel', channelId: 1, data: { channel_number: 6, name: '6 | ESPN' } }, [snapshot(1, 5, '5 | ESPN')]),
          op({ type: 'updateChannel', channelId: 1, data: { channel_number: 5, name: '5 | ESPN' } }, [snapshot(1, 6, '6 | ESPN')]),
        ],
      ),
    );
    expect(renames).toEqual([]);
  });
});

describe('validateFinalNumberingPlan consent direction', () => {
  // Fix round 3 adjudication. An external reviewer read
  // `standing ⊆ acknowledged` as a hole and asked for equality. It is not a
  // hole, and equality would be a defect of its own: the acknowledgement names
  // what the operator was SHOWN (recorded from the occupants the dialog
  // rendered, not from a fresh lookup) while `standing` is what actually
  // materialised. These are pins, and the reasoning is in the names so the
  // next reviewer does not re-litigate it.

  it('accepts a collision that shrank between the confirmation and Apply, because the operator was shown worse', () => {
    // X on 5; A joins acknowledging [X]; B joins acknowledging [X, A]; A then
    // leaves for 6. B is left sharing 5 with X — exactly who B was shown.
    const plan = buildFinalNumberingPlan(
      [channel(9, 5, 'X'), channel(1, 1, 'A'), channel(2, 2, 'B')],
      [
        op(
          {
            type: 'updateChannel',
            channelId: 1,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [9] },
          },
          [snapshot(1, 1)],
        ),
        op(
          {
            type: 'updateChannel',
            channelId: 2,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [9, 1] },
          },
          [snapshot(2, 2)],
        ),
        op({ type: 'updateChannel', channelId: 1, data: { channel_number: 6 } }, [snapshot(1, 5)]),
      ],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });

  it('still refuses an occupant the operator was never shown, which is what makes the subset test a check', () => {
    const plan = buildFinalNumberingPlan(
      [channel(9, 5, 'X'), channel(1, 1, 'A'), channel(2, 2, 'B')],
      [
        op(
          {
            type: 'updateChannel',
            channelId: 1,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [9] },
          },
          [snapshot(1, 1)],
        ),
        op(
          {
            type: 'updateChannel',
            channelId: 2,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [9] },
          },
          [snapshot(2, 2)],
        ),
      ],
    );
    const issues = validateFinalNumberingPlan(plan);
    expect(issues).toHaveLength(1);
    expect(issues[0].type).toBe('duplicate_channel_number');
  });

  it('authorises a placement whose dialog named a channel that a later re-staging puts after it', () => {
    // Why equality would refuse a fully informed operator. A's dialog named X
    // and B because both were on 5 when A was staged; re-staging B makes B the
    // LAST arrival, so `standing` for A is {X} while its acknowledgement names
    // {X, B}.
    const plan = buildFinalNumberingPlan(
      [channel(9, 5, 'X'), channel(1, 1, 'A'), channel(2, 2, 'B')],
      [
        op(
          {
            type: 'updateChannel',
            channelId: 2,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [9] },
          },
          [snapshot(2, 2)],
        ),
        op(
          {
            type: 'updateChannel',
            channelId: 1,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [9, 2] },
          },
          [snapshot(1, 1)],
        ),
        op(
          {
            type: 'updateChannel',
            channelId: 2,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [9, 1] },
          },
          [snapshot(2, 5)],
        ),
      ],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });
});

describe('validateFinalNumberingPlan on a number nobody was on', () => {
  // The defect the adjudication turned up, in the same expression. The consent
  // walk asked every newcomer for an acknowledgement including the FIRST one
  // to arrive on a free number. No dialog fires when a number is free, so no
  // acknowledgement can exist, and the operator was told to confirm the
  // duplicate where they staged it — at a place where there had never been a
  // duplicate to confirm.

  it('accepts a second channel confirmed onto a number the first moved onto while it was free', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 1, 'A'), channel(2, 2, 'B')],
      [
        op({ type: 'updateChannel', channelId: 2, data: { channel_number: 5 } }, [snapshot(2, 2)]),
        op(
          {
            type: 'updateChannel',
            channelId: 1,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 5, occupantChannelIds: [2] },
          },
          [snapshot(1, 1)],
        ),
      ],
    );
    expect(validateFinalNumberingPlan(plan)).toEqual([]);
  });

  it('still refuses the second arrival when it named nobody', () => {
    // The anti-vacuity control: only the arrival that landed on an empty
    // number is excused.
    const plan = buildFinalNumberingPlan(
      [channel(1, 1, 'A'), channel(2, 2, 'B')],
      [
        op({ type: 'updateChannel', channelId: 2, data: { channel_number: 5 } }, [snapshot(2, 2)]),
        op({ type: 'updateChannel', channelId: 1, data: { channel_number: 5 } }, [snapshot(1, 1)]),
      ],
    );
    expect(validateFinalNumberingPlan(plan)).toHaveLength(1);
  });

  it('still refuses an acknowledgement that names the wrong number', () => {
    const plan = buildFinalNumberingPlan(
      [channel(1, 1, 'A'), channel(2, 2, 'B')],
      [
        op({ type: 'updateChannel', channelId: 2, data: { channel_number: 5 } }, [snapshot(2, 2)]),
        op(
          {
            type: 'updateChannel',
            channelId: 1,
            data: { channel_number: 5 },
            acknowledgedDuplicate: { number: 9, occupantChannelIds: [2] },
          },
          [snapshot(1, 1)],
        ),
      ],
    );
    expect(validateFinalNumberingPlan(plan)).toHaveLength(1);
  });
});
