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
 *   `s`, a count `n` and a spacing `step`, the new channels take the INSERTION
 *   LATTICE points `s, s + step, ..., s + (n-1) * step`. Shift every channel
 *   occupying a number from `s` up to (not including) a stop point, each by
 *   `shift = n * step`, choosing the smallest stop that collides with nothing.
 *   Every occupant of a number moves, so pre-existing duplicates stay together
 *   instead of being split apart.
 *
 * What "collides with nothing" means, precisely, because this is where the
 * first version of this planner was wrong. The plan CLAIMS exactly two sets of
 * numbers: the lattice points the new channels take, and the number each
 * shifted channel lands on (`t + shift` for every occupied `t` in `[s, stop)`).
 * A stop is valid when no claimed number is still held by a channel that did
 * not move: that is, by an occupant at or above `stop`. Nothing else can
 * clash: a shifted channel lands at or above `s + shift`, which is above every
 * lattice point the new channels take, and the shift is order-preserving and
 * injective, so shifted channels cannot land on each other.
 *
 * The first version instead required an entire `shift`-wide CONTINUOUS
 * interval to be free. That conflates the lattice with the interval it spans.
 * Inserting one channel at 300 claims the single number 300; it does not claim
 * `[300.0, 300.9]`. With only `300.5` in the lineup, the interval test
 * moved it to `301.5` although the insertion slot 300 was free; with `300` and
 * `300.5`, it moved both although moving `300 -> 301` was already enough. The
 * two rules coincide for whole numbers on a whole-number step, which is why a
 * suite of integer fixtures could not tell them apart.
 *
 * Choosing the SMALLEST valid stop is what makes the plan minimal. Note that
 * minimality is over the family "shift the contiguous RUN `[s, stop)`", which
 * is the rule the operator was given ("the shift propagates only as far as it
 * must, and terminates at the first free number"). A channel sitting off the
 * lattice INSIDE that run moves with it (`300.5` moves when `300` and `301`
 * both move) because leaving it behind would put it below the channel that
 * was at 300, reversing two channels the operator never asked to reorder.
 *
 * All planning is done in integer ticks, one tick per tenth. The previous
 * implementation compared an unrounded running maximum against group minimums,
 * so `0.7 + 0.1` compared below `0.8` and invented a gap, while other
 * combinations compared above an exact boundary and invented a cascade. Float
 * drift was a cause of both a wrong stop and a wrong cascade, not merely untidy
 * output. Ticks remove the comparison entirely; the division back to channel
 * numbers happens once, at the end.
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
   * Where the moved run ends, reported on the insertion lattice
   * (`startingNumber`, `+ step`, ...). Every shifted channel's CURRENT number
   * is below it, and no channel at or above it moves. The walk stops at tick
   * resolution, which for a whole-number step is finer than the lattice, so
   * this is that stop rounded up to the next number an operator would
   * recognise: in the PO's 201-500 example it reads 501, not 500.1. It is not
   * a claim that everything below it moves: a number off the lattice can sit
   * below it and stay put.
   *
   * Reporting only. Nothing in ECM plans from this field; which channels move
   * is decided at tick resolution inside the planner.
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
  /**
   * Spacing between claimed numbers: 1 for whole numbers, 0.1 for decimal
   * inserts. Defaults to 1. A tenth is the finest step there is, because it
   * is the finest number Dispatcharr holds; anything finer rounds onto the
   * tenths grid (see `TICK_SCALE`) and a step below 0.05 rounds to zero,
   * which returns an empty plan. Both call sites pass 1 or 0.1.
   */
  step?: number;
  /** Channels that vacate their number as part of the same operation (a cross-group move) and so do not occupy it. */
  excludeIds?: Iterable<number>;
}

/**
 * Comparisons are made on a fixed integer grid so that every one of them is
 * exact. The grid is TENTHS, because that is Dispatcharr's channel-number
 * resolution: a channel number carries at most one decimal place, and ECM's
 * decimal insert steps by exactly `0.1`. Grid resolution and data contract
 * therefore coincide, and for any number the system can actually hold the
 * number-to-tick mapping is one-to-one. No two distinct valid channel numbers
 * share a tick, and no valid free number can be read as taken, so on
 * in-contract data the tick comparison IS exact equality; the grid is not an
 * approximation of it.
 *
 * Nothing enforces that contract on the way in yet. `backend/csv_handler.py`
 * accepts any positive float for `channel_number` with no precision check,
 * there is no ECM column for `channel_number` at all, and the frontend parses
 * with a bare `parseFloat`, so an import can still carry an out-of-contract
 * value like `1.05`. Such a value collapses onto the nearest tenth for
 * COMPARISON, and lands on the tenths grid on OUTPUT (see the `toNumber`
 * computation below). Both are deliberate:
 *
 *   - Collapsing for comparison is conservative. An out-of-contract number is
 *     treated as occupying the slot an operator reads it as, so the planner
 *     can only move a channel it might have left alone; it can never leave one
 *     sitting on a number the insert was supposed to free.
 *   - Snapping on output returns a number the contract can represent. Emitting
 *     `2.05` instead would carry the out-of-contract value forward into a
 *     value Dispatcharr cannot hold at that precision.
 *
 * This module does NOT enforce the invariant; it only has to cope with
 * whatever is already in the lineup. Defining and enforcing the canonical
 * channel-number domain is bead `enhancedchannelmanager-ic884.1` ("Define and
 * enforce valid canonical channel-number values"), which now carries the PO's
 * one-decimal constraint. The collapse is pinned by the "collapses an
 * out-of-contract number onto the nearest tenth" test in
 * `channelNumberShift.test.ts`; it is a pinned behaviour, not an enforced
 * guarantee about the data.
 */
const TICK_SCALE = 10;

function toTicks(value: number): number {
  return Math.round(value * TICK_SCALE);
}

function fromTicks(ticks: number): number {
  return Math.round(ticks) / TICK_SCALE;
}

/**
 * The slot key for a channel number: two numbers share a key exactly when this
 * module treats them as the same channel number. Exported so that a caller
 * deciding whether a number is already taken uses the planner's own notion of
 * "same number" rather than `===` on two floats that a decimal insert has
 * already made non-identical (`20.7 + 0.1 !== 20.8`).
 */
export function channelNumberSlot(value: number): number {
  return toTicks(value);
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
  const occupiedTicks = new Set<number>();
  for (const channel of channels) {
    const number = channel.channel_number;
    if (number === null || !Number.isFinite(number)) continue;
    if (excluded?.has(channel.id)) continue;
    const tick = toTicks(number);
    if (tick < startTick) continue;
    occupants.push({ channel, tick });
    occupiedTicks.add(tick);
  }
  occupants.sort((a, b) => a.tick - b.tick);

  // Walk the occupied numbers upward from the insertion point, extending the
  // moved run past any number the plan would otherwise land on. A number that
  // is already inside the run (`tick < stopTick`) is moving, so it cannot be
  // landed on; anything at or above the run stays put, so it can. It is in the
  // way for exactly two reasons: a new channel takes it, or a channel already
  // inside the run lands on it. Either way the run has to swallow it.
  //
  // One ascending pass is enough even though extending the run turns numbers
  // the walk already passed into movers. If `u` was left behind and the run
  // later grows past it, the run grew because of some number above `u + shift`
  // (nothing below is examined again), so `u + shift` is inside the run too
  // and moves by the same amount, so the two can never meet.
  let stopTick = startTick;
  let previousTick = Number.NEGATIVE_INFINITY;
  for (const { tick } of occupants) {
    if (tick === previousTick) continue; // Duplicates share one number.
    previousTick = tick;
    if (tick < stopTick) continue;

    const offsetFromStart = tick - startTick;
    const takenByNewChannel =
      offsetFromStart % stepTicks === 0 && offsetFromStart / stepTicks < claimCount;
    const landedOnByMovedChannel =
      tick - shiftTicks >= startTick &&
      tick - shiftTicks < stopTick &&
      occupiedTicks.has(tick - shiftTicks);

    if (takenByNewChannel || landedOnByMovedChannel) stopTick = tick + 1;
  }

  const shiftAmount = fromTicks(shiftTicks);
  const shifts: Array<PlannedChannelShift<T>> = [];
  for (const { channel, tick } of occupants) {
    if (tick >= stopTick) break;
    const fromNumber = channel.channel_number as number;
    shifts.push({
      channel,
      fromNumber,
      // Re-emitted from ticks, which a float sum is not: `38.1 + 0.3` is
      // `38.400000000000006`. Every in-contract number is on the tenths grid,
      // so this is exact for anything the system can hold; an out-of-contract
      // number lands on the grid, which is the intended repair (see TICK_SCALE).
      toNumber: fromTicks(tick + shiftTicks),
    });
  }

  // Report the stop on the insertion lattice rather than at tick resolution.
  // This is presentation only: which channels move is decided by `stopTick`.
  const latticeSteps = Math.max(0, Math.ceil((stopTick - startTick) / stepTicks));

  return {
    shifts,
    shiftAmount,
    stopNumber: fromTicks(startTick + latticeSteps * stepTicks),
  };
}
