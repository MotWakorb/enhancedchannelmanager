/**
 * Channel-number push-down planner (bead `enhancedchannelmanager-i85dg`).
 *
 * Inserting channels at an occupied number has to move the channels already
 * sitting there. Two call sites used to plan that move independently, and both
 * planned it wrong:
 *
 *   - `App.tsx` `handleBulkCreateFromGroup` modelled each channel group as one
 *     continuous `[minNum, maxNum]` interval and cascaded into the next group
 *     whenever `maxNum + shiftAmount` reached that group's `minNum`. A single
 *     outlier high number anywhere in the target group inflated `maxNum` past
 *     every downstream group's minimum, so the cascade swept the whole lineup.
 *     The same interval model also skipped groups whose `minNum` sorted before
 *     the target group but whose `maxNum` overlapped its tail, which created
 *     duplicates.
 *   - `ChannelsPane.tsx` `handleCrossGroupMoveConfirm` shifted every target
 *     group channel at or after the insertion point with no cascade at all, so
 *     the target group's tail was pushed silently onto the next group's
 *     numbers.
 *
 * Neither model is repairable: nothing in ECM, Dispatcharr's API contract or
 * the database constrains `channel_number` by `channel_group_id`. Groups are
 * free to contain holes and outliers, to overlap, and to interleave, and
 * `channel_number` is a non-unique float, so duplicates exist in real lineups.
 *
 * This planner therefore never asks which group a number belongs to. Group
 * membership decides the operator's INTENT (which channels are being inserted,
 * and where); it is not an input to the arithmetic. The rule is pure occupancy:
 *
 *   Given the occupied channel numbers of the working copy, an insertion point
 *   `s` and a count `n` of numbers to claim, walk upward from `s` and stop at
 *   the first run of free numbers wide enough to absorb the shift. Shift every
 *   channel occupying a number from `s` up to (not including) that stop point,
 *   each by `n` steps. Every occupant of a number moves, so pre-existing
 *   duplicates stay together instead of being split apart.
 *
 * Why that is sufficient: after the move, the claimed region runs from `s` to
 * `stop + shift`. New channels take the lattice points in `[s, s + shift)`,
 * moved channels land in `[s + shift, stop + shift)`, and every channel that
 * did not move sits at or above `stop`. So the plan is collision-free exactly
 * when nothing occupies `[stop, stop + shift)`, which is the stop condition
 * the walk enforces. Choosing the SMALLEST such `stop` is what makes the plan
 * minimal: no channel moves whose number is not required to move.
 *
 * All planning is done in integer ticks. The previous implementation compared
 * an unrounded running maximum against group minimums, so `0.7 + 0.1` compared
 * below `0.8` and invented a gap, while other combinations compared above an
 * exact boundary and invented a cascade. Float drift was a cause of both a
 * wrong stop and a wrong cascade, not merely untidy output. Ticks remove the
 * comparison entirely; the division back to channel numbers happens once, at
 * the end.
 *
 * Tests: `channelNumberShift.test.ts`.
 */

/** Minimum shape the planner needs. `Channel` from `../types` satisfies it. */
export interface ShiftableChannel {
  id: number;
  channel_number: number | null;
}

export interface PlannedChannelShift<T extends ShiftableChannel> {
  channel: T;
  fromNumber: number;
  toNumber: number;
}

export interface ChannelNumberShiftPlan<T extends ShiftableChannel> {
  /** Channels that must move, ascending by current number. Empty means nothing moves. */
  shifts: PlannedChannelShift<T>[];
  /** How far each shifted channel moves: `count * step`, free of float drift. */
  shiftAmount: number;
  /**
   * First number on the insertion lattice (`startingNumber`, `+ step`, ...)
   * that the shift leaves alone. Every shifted channel sits below it. The walk
   * itself stops at a finer resolution than the lattice, so this is that stop
   * rounded up to the next number an operator would recognise: in the PO's
   * 201-500 example it reads 501, not 500.001.
   */
  stopNumber: number;
}

export interface ChannelNumberShiftOptions<T extends ShiftableChannel> {
  /** The working copy to plan against. In edit mode this is the STAGED channel list, not the persisted one. */
  channels: readonly T[];
  /** First number the new or incoming channels will claim. */
  startingNumber: number;
  /** How many numbers they claim. */
  count: number;
  /** Spacing between claimed numbers: 1 for whole numbers, 0.1 for decimal inserts. Defaults to 1. */
  step?: number;
  /** Channels that vacate their number as part of the same operation (a cross-group move) and so do not occupy it. */
  excludeIds?: Iterable<number>;
}

/**
 * Channel numbers are planned on a fixed integer lattice so that every
 * comparison is exact. Three decimal places covers ECM's 0.1 decimal step with
 * room to spare, and keeps two numbers that differ in the third decimal from
 * collapsing onto one slot.
 */
const TICK_SCALE = 1000;

function toTicks(value: number): number {
  return Math.round(value * TICK_SCALE);
}

function fromTicks(ticks: number): number {
  return Math.round(ticks) / TICK_SCALE;
}

/**
 * Plan the minimal set of channel-number shifts that frees `count` numbers
 * starting at `startingNumber`.
 *
 * Returns an empty `shifts` list when the insertion point is already free,
 * when `count` is not positive, or when the inputs are not finite.
 */
export function planChannelNumberShift<T extends ShiftableChannel>({
  channels,
  startingNumber,
  count,
  step = 1,
  excludeIds,
}: ChannelNumberShiftOptions<T>): ChannelNumberShiftPlan<T> {
  const noShift: ChannelNumberShiftPlan<T> = {
    shifts: [],
    shiftAmount: 0,
    stopNumber: startingNumber,
  };

  if (!Number.isFinite(startingNumber) || !Number.isFinite(count) || !Number.isFinite(step)) {
    return noShift;
  }

  const stepTicks = toTicks(step);
  const claimCount = Math.floor(count);
  if (stepTicks <= 0 || claimCount <= 0) {
    return noShift;
  }

  const shiftTicks = stepTicks * claimCount;
  const startTick = toTicks(startingNumber);
  const excluded = excludeIds ? new Set(excludeIds) : null;

  // Occupancy is global and covers every channel, not just the target group's.
  // Channels below the insertion point can never be affected, so they are
  // dropped here rather than carried through the walk.
  const occupants: Array<{ channel: T; tick: number }> = [];
  for (const channel of channels) {
    const number = channel.channel_number;
    if (number === null || !Number.isFinite(number)) continue;
    if (excluded?.has(channel.id)) continue;
    const tick = toTicks(number);
    if (tick < startTick) continue;
    occupants.push({ channel, tick });
  }
  occupants.sort((a, b) => a.tick - b.tick);

  // Walk upward from the insertion point and stop at the first run of
  // `shiftTicks` free ticks. A free run can only begin at the insertion point
  // itself or immediately after an occupied tick, so scanning the occupied
  // ticks in order finds the smallest stop without probing every tick. The
  // walk always terminates: past the highest occupied tick every run is free.
  let stopTick = startTick;
  for (const { tick } of occupants) {
    if (tick >= stopTick + shiftTicks) break;
    if (tick >= stopTick) stopTick = tick + 1;
  }

  const shifts: Array<PlannedChannelShift<T>> = [];
  for (const { channel, tick } of occupants) {
    if (tick >= stopTick) break;
    shifts.push({
      channel,
      fromNumber: channel.channel_number as number,
      toNumber: fromTicks(tick + shiftTicks),
    });
  }

  // Report the stop on the insertion lattice rather than at tick resolution.
  // This is presentation only: which channels move is decided by `stopTick`.
  const latticeSteps = Math.max(0, Math.ceil((stopTick - startTick) / stepTicks));

  return {
    shifts,
    shiftAmount: fromTicks(shiftTicks),
    stopNumber: fromTicks(startTick + latticeSteps * stepTicks),
  };
}
