/**
 * Push-down planner properties (bead `enhancedchannelmanager-i85dg`).
 *
 * The shift logic had shipped three times with no test at all, and came back
 * a fourth time. Every property below is written against the PO's stated rule
 * rather than against the implementation, and properties 2 to 5, 7 and 10 were
 * each demonstrated failing against a verbatim port of the replaced
 * `App.tsx` interval algorithm before this planner existed.
 *
 * Numbering matches the brief so a future reader can map a regression back to
 * the behaviour it broke.
 *
 * A Codex pre-merge review then found that the first planner treated a whole
 * `step`-wide continuous interval as claimed rather than the lattice points
 * the new channels actually take, and that THIS FILE could not catch it: the
 * reference implementation below encoded the same assumption, and the
 * randomised generator emitted whole numbers only, for which the two readings
 * are identical. Both were rewritten from the rule. The reference decides
 * validity by applying a candidate plan and looking for collisions, and the
 * generator now emits decimals, off-lattice numbers and mixed lineups. No
 * expectation in the properties above changed, because none of their fixtures
 * could distinguish the two rules; the gap was coverage, not wrong answers.
 *
 * A second Codex re-review then found that `bruteForceStopTick` below shares
 * the planner's own tick-collapse comparison (both round to three decimals
 * before comparing occupancy), so it cannot see the planner over-move a
 * sub-tick channel that collapses onto the insertion slot, and the generator
 * never produced such a channel to begin with. That collapse is a deliberate,
 * kept choice — see the `TICK_SCALE` comment in `channelNumberShift.ts` — so
 * this is not a bug to fix. The "tick-collapse comparison contract" describe
 * block below pins the deliberate behaviour explicitly, and the sampled
 * property test now also generates sub-tick jitter and checks a weaker,
 * genuinely independent minimality property against it (`preciseBruteForceStopTick`,
 * rounded to a resolution 1000x finer than the planner's own), since the
 * tick-sharing oracle cannot be used to check that case.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { planChannelNumberShift } from './channelNumberShift';
import type { ChannelNumberShiftPlan } from './channelNumberShift';

interface TestChannel {
  id: number;
  channel_number: number | null;
  channel_group_id: number | null;
  name: string;
}

const GROUP_A = 1;
const GROUP_B = 2;
const GROUP_C = 3;
const UNGROUPED = null;

let nextId = 1;

function channel(number: number | null, groupId: number | null): TestChannel {
  const id = nextId++;
  return { id, channel_number: number, channel_group_id: groupId, name: `Channel ${id}` };
}

/** Inclusive contiguous run, one channel per whole number. */
function run(from: number, to: number, groupId: number | null): TestChannel[] {
  const out: TestChannel[] = [];
  for (let n = from; n <= to; n++) out.push(channel(n, groupId));
  return out;
}

/**
 * The PO's worked example, plus a third group so a runaway cascade has
 * somewhere to run to: A occupies 201-500, B occupies 600-699, C occupies
 * 700-799, all contiguous.
 */
function poFixture(): TestChannel[] {
  return [...run(201, 500, GROUP_A), ...run(600, 699, GROUP_B), ...run(700, 799, GROUP_C)];
}

function numbersOf(channels: TestChannel[]): number[] {
  return channels.map((c) => c.channel_number as number).sort((a, b) => a - b);
}

function shiftedNumbers(plan: { shifts: Array<{ fromNumber: number }> }): number[] {
  return plan.shifts.map((s) => s.fromNumber).sort((a, b) => a - b);
}

const SCALE = 1000;
const tickOf = (n: number) => Math.round(n * SCALE);

/**
 * Independent reference for the stop point, derived from the RULE rather than
 * from the planner, and deliberately written the slow obvious way.
 *
 * The rule, restated so this reference does not inherit the planner's
 * reasoning: `count` channels inserted at `startingNumber` with spacing `step`
 * take the LATTICE POINTS `startingNumber + k * step` for `k < count`. They do
 * not occupy the continuous interval spanned by those points, so a number
 * sitting between two of them is not in the way. Shifting the run of channels
 * occupying `[startingNumber, stop)` up by `count * step` is collision-free
 * exactly when nothing the plan CLAIMS (those lattice points, plus the number
 * each shifted channel lands on) is still held by a channel that stayed put.
 *
 * So: try each candidate stop in ascending order, build the claimed and
 * stayed-put sets the long way, and return the first stop where they are
 * disjoint. The plan depends on `stop` only through which occupied ticks fall
 * below it, so `startingNumber` and one tick past each occupied number are the
 * only values that can ever be the smallest valid stop.
 *
 * Returns the raw stop in ticks, not the lattice-rounded `stopNumber` the
 * planner reports, so nothing about the planner's rounding leaks in here.
 */
function bruteForceStopTick(
  channels: TestChannel[],
  startingNumber: number,
  count: number,
  step: number,
): number {
  const startTick = tickOf(startingNumber);
  const stepTicks = tickOf(step);
  const shiftTicks = stepTicks * count;

  const occupied = new Set<number>();
  for (const c of channels) {
    if (c.channel_number !== null) occupied.add(tickOf(c.channel_number));
  }
  // Numbers below the insertion point are never claimed and never move.
  const above = [...occupied].filter((t) => t >= startTick).sort((a, b) => a - b);

  const newChannelTicks: number[] = [];
  for (let k = 0; k < count; k++) newChannelTicks.push(startTick + k * stepTicks);

  for (const stop of [startTick, ...above.map((t) => t + 1)]) {
    const stayed = new Set(above.filter((t) => t >= stop));
    const claimed = [...newChannelTicks, ...above.filter((t) => t < stop).map((t) => t + shiftTicks)];
    if (claimed.every((c) => !stayed.has(c))) return stop;
  }
  throw new Error('brute force found no stop point, which cannot happen above the highest number');
}

function bruteForceStopNumber(
  channels: TestChannel[],
  startingNumber: number,
  count: number,
  step: number,
): number {
  return bruteForceStopTick(channels, startingNumber, count, step) / SCALE;
}

/**
 * Same rule as `bruteForceStopTick`, at a resolution 1000x finer than the
 * planner's own three-decimal comparison. This is what an EXACT-equality
 * planner (no tick-collapse at all) would compute, so it is NOT independent
 * of `bruteForceStopTick` in the way that function is independent of the
 * planner's stop-search loop — it shares the same "try each candidate stop,
 * check the claimed and stayed-put sets" method — but it IS independent of
 * the planner's occupancy-equivalence assumption, which is the thing
 * `bruteForceStopTick` cannot check (see the file header comment on the
 * second Codex re-review).
 *
 * Never asserted equal to the planner's stop: the planner is deliberately
 * coarser than this, and over-moving relative to it is the documented,
 * intended behaviour. It is used only for the weaker "never fewer moves than
 * exact necessity" check in the sampled property test below.
 */
const PRECISE_SCALE = 1_000_000;
const preciseTickOf = (n: number) => Math.round(n * PRECISE_SCALE);

function preciseBruteForceStopTick(
  channels: TestChannel[],
  startingNumber: number,
  count: number,
  step: number,
): number {
  const startTick = preciseTickOf(startingNumber);
  const stepTicks = preciseTickOf(step);
  const shiftTicks = stepTicks * count;

  const occupied = new Set<number>();
  for (const c of channels) {
    if (c.channel_number !== null) occupied.add(preciseTickOf(c.channel_number));
  }
  const above = [...occupied].filter((t) => t >= startTick).sort((a, b) => a - b);

  const newChannelTicks: number[] = [];
  for (let k = 0; k < count; k++) newChannelTicks.push(startTick + k * stepTicks);

  for (const stop of [startTick, ...above.map((t) => t + 1)]) {
    const stayed = new Set(above.filter((t) => t >= stop));
    const claimed = [...newChannelTicks, ...above.filter((t) => t < stop).map((t) => t + shiftTicks)];
    if (claimed.every((c) => !stayed.has(c))) return stop;
  }
  throw new Error('precise brute force found no stop point, which cannot happen above the highest number');
}

/** The numbers the newly inserted channels will take, on the insertion lattice. */
function claimedLatticeTicks(startingNumber: number, count: number, step: number): number[] {
  const out: number[] = [];
  for (let k = 0; k < count; k++) out.push(tickOf(startingNumber) + k * tickOf(step));
  return out;
}

/** Apply a plan and return every channel's resulting number. */
function applyPlan(
  channels: TestChannel[],
  plan: ChannelNumberShiftPlan<TestChannel>,
): Map<number, number> {
  const moved = new Map(plan.shifts.map((s) => [s.channel.id, s.toNumber]));
  const finals = new Map<number, number>();
  for (const c of channels) {
    if (c.channel_number === null) continue;
    finals.set(c.id, moved.get(c.id) ?? c.channel_number);
  }
  return finals;
}

/**
 * The plan must not merge two distinct occupied numbers onto one. Channels
 * that already shared a number are allowed to keep sharing it, which is why
 * this checks the number-to-number mapping rather than counting occupants.
 */
function expectNoNewDuplicates(channels: TestChannel[], finals: Map<number, number>): void {
  const mapping = new Map<number, number>();
  for (const c of channels) {
    if (c.channel_number === null) continue;
    const from = c.channel_number;
    const to = finals.get(c.id) as number;
    const recorded = mapping.get(from);
    if (recorded === undefined) {
      mapping.set(from, to);
    } else {
      expect(recorded).toBe(to);
    }
  }
  const targets = [...mapping.values()];
  expect(new Set(targets).size).toBe(targets.length);
}

beforeEach(() => {
  nextId = 1;
});

describe('planChannelNumberShift', () => {
  // Property 1
  it("shifts only the target group's tail when a gap already absorbs the insert", () => {
    const channels = poFixture();
    const plan = planChannelNumberShift({ channels, startingNumber: 300, count: 1 });

    expect(shiftedNumbers(plan)).toEqual(numbersOf(run(300, 500, GROUP_A)));
    expect(plan.shifts).toHaveLength(201);
    expect(plan.shiftAmount).toBe(1);
    expect(plan.stopNumber).toBe(501);

    const movedGroups = new Set(plan.shifts.map((s) => s.channel.channel_group_id));
    expect([...movedGroups]).toEqual([GROUP_A]);
    expectNoNewDuplicates(channels, applyPlan(channels, plan));
  });

  // Property 2
  it('stops at the first free number inside the target group', () => {
    const channels = poFixture().filter((c) => c.channel_number !== 350);
    const plan = planChannelNumberShift({ channels, startingNumber: 300, count: 1 });

    expect(plan.shifts).toHaveLength(50);
    expect(shiftedNumbers(plan)).toEqual(numbersOf(run(300, 349, GROUP_A)));
    expect(plan.stopNumber).toBe(350);
    expectNoNewDuplicates(channels, applyPlan(channels, plan));
  });

  // Property 3 - the defect the PO reported from the live instance.
  it('is immune to a single outlier high number in the target group', () => {
    const baseline = poFixture();
    const baselinePlan = planChannelNumberShift({
      channels: baseline,
      startingNumber: 300,
      count: 1,
    });

    nextId = 1;
    const withOutlier = [...poFixture(), channel(8000, GROUP_A)];
    const plan = planChannelNumberShift({ channels: withOutlier, startingNumber: 300, count: 1 });

    expect(shiftedNumbers(plan)).toEqual(shiftedNumbers(baselinePlan));
    expect(plan.shifts).toHaveLength(201);
    expect(plan.shifts.some((s) => s.fromNumber === 8000)).toBe(false);
    expectNoNewDuplicates(withOutlier, applyPlan(withOutlier, plan));
  });

  // Property 4
  it('is immune to a bucket that interleaves with the target group', () => {
    // An ungrouped pair straddling the target group: 350 sits inside A's
    // range, 9999 far above every group. The interval model sorted this
    // bucket after A, saw its 9999 maximum, and swept B and C with it.
    const channels = [...poFixture(), channel(350, UNGROUPED), channel(9999, UNGROUPED)];
    const plan = planChannelNumberShift({ channels, startingNumber: 300, count: 1 });

    // The interleaved 350 genuinely occupies a number the shift claims, so it
    // moves. Everything above the stop point, including 9999, does not.
    expect(plan.shifts).toHaveLength(202);
    expect(plan.stopNumber).toBe(501);
    expect(plan.shifts.filter((s) => s.channel.channel_group_id === UNGROUPED)).toHaveLength(1);
    expect(plan.shifts.some((s) => s.fromNumber === 9999)).toBe(false);
    expect(
      plan.shifts.some((s) => s.channel.channel_group_id === GROUP_B || s.channel.channel_group_id === GROUP_C),
    ).toBe(false);
    expectNoNewDuplicates(channels, applyPlan(channels, plan));
  });

  // Property 5, worked case
  it('does not land a shifted channel on a group that overlaps the target tail', () => {
    const channels = [...run(201, 500, GROUP_A), channel(100, GROUP_B), channel(505, GROUP_B)];
    const plan = planChannelNumberShift({ channels, startingNumber: 300, count: 5 });

    const finals = applyPlan(channels, plan);
    expectNoNewDuplicates(channels, finals);
    expect(plan.shifts.some((s) => s.fromNumber === 505)).toBe(true);
    expect(plan.shifts.some((s) => s.fromNumber === 100)).toBe(false);
    expect(plan.stopNumber).toBe(506);
  });

  // Property 5, sampled
  it('never creates a duplicate over randomised lineups with holes, overlaps, duplicates and decimals', () => {
    // Deterministic 32-bit LCG so a failure is reproducible from the seed.
    let seed = 0x5eed1e;
    const rand = (n: number) => {
      seed = (seed * 1664525 + 1013904223) >>> 0;
      return seed % n;
    };

    // Real lineups are not integers-only: ECM's own decimal insert produces
    // x.1 spacing, and imports carry whatever the provider had. A generator
    // that emits only whole numbers cannot tell the insertion lattice apart
    // from the continuous interval between its points, because for step 1 and
    // integer data the two coincide, which is why the first version of this
    // suite passed against a planner that confused them.
    const OFFSETS = [0, 0, 0, 0.1, 0.2, 0.25, 0.5, 0.05, 0.75, 0.9];

    // Sub-tick jitter: magnitudes stay under 0.0005 so they never cross a
    // tick boundary (Math.round(x * 1000) is unchanged by adding one of
    // these), which reliably produces channels that are genuinely distinct
    // at exact resolution but collapse onto the same tick as their neighbour
    // for the planner's comparison — the case the second Codex re-review
    // found uncovered. See the file header comment and the "tick-collapse
    // comparison contract" describe block above for the deliberate rule this
    // exercises.
    const MICRO_OFFSETS = [0, 0, 0, 0.00004, 0.00013, 0.00027, 0.00041, -0.00004, -0.00019, -0.00033];

    for (let iteration = 0; iteration < 400; iteration++) {
      nextId = 1;
      const step = rand(2) === 0 ? 1 : 0.1;
      const channels: TestChannel[] = [];
      const bucketCount = 1 + rand(4);
      for (let bucket = 0; bucket < bucketCount; bucket++) {
        const from = 1 + rand(120);
        const width = rand(40);
        for (let n = from; n <= from + width; n++) {
          // Holes, off-lattice numbers, and occasionally a second occupant on
          // the same number.
          if (rand(5) === 0) continue;
          const value = n + OFFSETS[rand(OFFSETS.length)] + MICRO_OFFSETS[rand(MICRO_OFFSETS.length)];
          channels.push(channel(value, bucket));
          if (rand(11) === 0) channels.push(channel(value, bucket));
        }
      }
      if (channels.length === 0) continue;

      // The insertion point is on the lattice by construction (it IS the
      // lattice origin), but it is not always a whole number.
      const startingNumber = 1 + rand(160) + OFFSETS[rand(OFFSETS.length)];
      const count = 1 + rand(6);
      const plan = planChannelNumberShift({ channels, startingNumber, count, step });
      const finals = applyPlan(channels, plan);
      const seedNote = `iteration ${iteration}: start ${startingNumber} count ${count} step ${step}`;

      expectNoNewDuplicates(channels, finals);

      // Every lattice point the new channels take must end up free.
      const claimed = new Set(claimedLatticeTicks(startingNumber, count, step));
      for (const [, finalNumber] of finals) {
        expect(claimed.has(tickOf(finalNumber)), seedNote).toBe(false);
      }

      // Exactly the channels the independent reference says must move, and no
      // others. This is the minimality half: a planner that moves a channel it
      // did not have to fails here even though the lineup stays duplicate-free.
      const stopTick = bruteForceStopTick(channels, startingNumber, count, step);
      const required = channels
        .filter((c) => c.channel_number !== null)
        .map((c) => c.channel_number as number)
        .filter((n) => tickOf(n) >= tickOf(startingNumber) && tickOf(n) < stopTick)
        .sort((a, b) => a - b);
      expect(shiftedNumbers(plan), seedNote).toEqual(required);

      // Weaker minimality, checked independently of the tick-collapse
      // assumption: the planner must never move FEWER channels than an
      // exact-equality planner would require. `bruteForceStopTick` above
      // cannot check this — it shares the same three-decimal rounding as the
      // planner, so it agrees with every over-move by construction (see the
      // file header comment). `preciseBruteForceStopTick` rounds 1000x finer,
      // so a channel it says must move because it sits at an exact number the
      // plan claims is a real, non-collapsed requirement. This is
      // deliberately NOT `toEqual`: the planner is allowed, and by the
      // TICK_SCALE contract in channelNumberShift.ts sometimes required, to
      // move MORE than this exact reference — only moving fewer would be a
      // bug (a channel genuinely in the way left behind, which the strict
      // tick-model check above cannot see either, because it would agree with
      // the planner having skipped it).
      const preciseStopTick = preciseBruteForceStopTick(channels, startingNumber, count, step);
      const preciseStartTick = preciseTickOf(startingNumber);
      const exactlyRequired = channels
        .filter((c) => c.channel_number !== null)
        .map((c) => c.channel_number as number)
        .filter((n) => preciseTickOf(n) >= preciseStartTick && preciseTickOf(n) < preciseStopTick);
      const shiftedSet = new Set(shiftedNumbers(plan));
      for (const n of exactlyRequired) {
        expect(shiftedSet.has(n), `${seedNote}: exact-required ${n} was left behind`).toBe(true);
      }
    }
  });

  // Property 5, the insertion lattice specifically. Both cases come from the
  // Codex pre-merge review of the first version of this planner, which walked
  // continuous ticks and so treated a whole `step`-wide interval as claimed.
  it('leaves an off-lattice neighbour alone when the insertion slot itself is free', () => {
    // A whole-number insert at 300 takes the lattice point 300 and nothing
    // else. 300.5 is not 300, so no channel has to move at all.
    const channels = [channel(300.5, GROUP_A)];
    const plan = planChannelNumberShift({ channels, startingNumber: 300, count: 1 });

    expect(plan.shifts).toEqual([]);
    expect(plan.stopNumber).toBe(300);
    expect(bruteForceStopNumber(channels, 300, 1, 1)).toBe(300);
  });

  it('moves only the channel on the claimed slot, not the off-lattice one above it', () => {
    // 300 is in the way and moves to 301, which is free. 300.5 is above the
    // moved run and below the next lattice point, so it is not required to
    // move and must not be moved.
    const channels = [channel(300, GROUP_A), channel(300.5, GROUP_A)];
    const plan = planChannelNumberShift({ channels, startingNumber: 300, count: 1 });

    expect(plan.shifts.map((s) => [s.fromNumber, s.toNumber])).toEqual([[300, 301]]);
    expect(plan.stopNumber).toBe(301);
    expectNoNewDuplicates(channels, applyPlan(channels, plan));
  });

  it('carries an off-lattice channel that sits inside the moved run along with it', () => {
    // Here 300.5 IS inside the run that has to move (300 goes to 301, which
    // pushes 301 to 302), so it moves too and keeps its place in the order.
    // Moving it is not optional: leaving it behind would put it below 301,
    // which the channel formerly at 300 now occupies, reversing the two.
    const channels = [channel(300, GROUP_A), channel(300.5, GROUP_A), channel(301, GROUP_A)];
    const plan = planChannelNumberShift({ channels, startingNumber: 300, count: 1 });

    expect(plan.shifts.map((s) => [s.fromNumber, s.toNumber])).toEqual([
      [300, 301],
      [300.5, 301.5],
      [301, 302],
    ]);
    expect(plan.stopNumber).toBe(302);
    expectNoNewDuplicates(channels, applyPlan(channels, plan));
  });

  it('does not merge two numbers that differ below tick resolution', () => {
    // Nothing in ECM, the CSV importer or Dispatcharr constrains a channel
    // number to three decimals, so an import can carry 1.0001 and 1.0004.
    // Both round to the same tick, which is deliberate for COMPARISON, since
    // they are the same slot to an operator, but the numbers the plan emits must
    // stay as distinct as the numbers it was given (bead ic884.1 owns whether
    // such values should be accepted in the first place).
    const channels = [channel(1.0001, GROUP_A), channel(1.0004, GROUP_A)];
    const plan = planChannelNumberShift({ channels, startingNumber: 1, count: 1 });

    const finals = applyPlan(channels, plan);
    expectNoNewDuplicates(channels, finals);
    expect(new Set(plan.shifts.map((s) => s.toNumber)).size).toBe(plan.shifts.length);
  });

  // Second Codex pre-merge re-review, MEDIUM finding. These tests pin the
  // CURRENT behaviour on purpose — they do not describe a bug to fix. The
  // tick-collapse comparison in channelNumberShift.ts (see the `TICK_SCALE`
  // comment) is a deliberate, kept design choice: it can only ever cause the
  // planner to move a channel it did not strictly have to, never to merge two
  // channels onto one output number (the previous test above already pins
  // that half). Making the comparison exact instead would remove these
  // over-moves, but it would also let two channels that read as the same
  // slot to an operator (e.g. `1.0001` and `1.0004`, both "channel 1") end up
  // sitting on what looks like one slot after an insert that was supposed to
  // clear it — the earlier engineer's judgment, which this suite agrees
  // with. The real fix for sub-tick input in the first place is bead
  // `enhancedchannelmanager-ic884.1` ("Define and enforce valid canonical
  // channel-number values"); nothing here implements that invariant.
  describe('tick-collapse comparison contract (deliberate conservative choice)', () => {
    it('moves a sub-tick occupant that collapses onto the insertion slot, even though its exact number is free', () => {
      // Codex's exact case: no channel sits at exactly 1, so a human reading
      // raw numbers would call the insertion slot free. But
      // Math.round(1.0004 * 1000) === Math.round(1 * 1000) === 1000, so the
      // tick-collapse comparison treats 1.0004 as occupying the slot and
      // moves it — the over-move the TICK_SCALE comment documents as the
      // conservative cost of collapsing at three decimals.
      const channels = [channel(1.0004, GROUP_A)];
      const plan = planChannelNumberShift({ channels, startingNumber: 1, count: 1 });

      expect(plan.shifts.map((s) => [s.fromNumber, s.toNumber])).toEqual([[1.0004, 2.0004]]);
      expect(plan.stopNumber).toBe(2);
      expectNoNewDuplicates(channels, applyPlan(channels, plan));
    });

    it('moves every sub-tick occupant sharing the insertion slot, not just one of them', () => {
      // Both 1.0001 and 1.0004 round to the same tick as the insertion
      // point, so the collapse treats the slot as occupied by both. Neither
      // is special-cased out: both move, and their output numbers (computed
      // from the original floats, not the shared tick — see the `toNumber`
      // computation in the planner) stay distinct rather than merging.
      const channels = [channel(1.0001, GROUP_A), channel(1.0004, GROUP_A)];
      const plan = planChannelNumberShift({ channels, startingNumber: 1, count: 1 });

      // Expected `toNumber`s are computed the same way the planner computes
      // them (fromNumber + shiftAmount by plain addition, not re-emitted from
      // the shared tick — see the `toNumber` computation in the planner) so
      // this assertion isn't tripped up by IEEE 754 float representation
      // (`1.0001 + 1` is `2.0000999999999998`, not the decimal `2.0001`).
      expect(plan.shifts.map((s) => [s.fromNumber, s.toNumber]).sort((a, b) => a[0] - b[0])).toEqual([
        [1.0001, 1.0001 + 1],
        [1.0004, 1.0004 + 1],
      ]);
      expect(plan.stopNumber).toBe(2);
      expectNoNewDuplicates(channels, applyPlan(channels, plan));
    });

    it('does not collapse a channel one tick above the insertion slot', () => {
      // Contrast case: 1.0006 rounds UP to the next tick
      // (Math.round(1.0006 * 1000) === 1001), one above the insertion
      // point's 1000, so it is not treated as occupying the slot. The
      // collapse is bounded to "same tick," not a general closeness fuzz —
      // it does not reach into a neighbouring tick.
      const channels = [channel(1.0006, GROUP_A)];
      const plan = planChannelNumberShift({ channels, startingNumber: 1, count: 1 });

      expect(plan.shifts).toEqual([]);
      expect(plan.stopNumber).toBe(1);
    });
  });

  // Property 6
  it('moves no channel that is not required to move', () => {
    const cases: Array<{ channels: TestChannel[]; startingNumber: number; count: number; step: number }> = [
      { channels: poFixture(), startingNumber: 300, count: 1, step: 1 },
      { channels: poFixture().filter((c) => c.channel_number !== 350), startingNumber: 300, count: 1, step: 1 },
      { channels: [...run(201, 500, GROUP_A), ...run(503, 599, GROUP_B)], startingNumber: 300, count: 5, step: 1 },
      { channels: [...run(201, 500, GROUP_A), channel(100, GROUP_B), channel(505, GROUP_B)], startingNumber: 300, count: 5, step: 1 },
      { channels: [channel(38.1, GROUP_A), channel(38.2, GROUP_A), channel(38.3, GROUP_A)], startingNumber: 38.1, count: 3, step: 0.1 },
    ];

    for (const { channels, startingNumber, count, step } of cases) {
      const plan = planChannelNumberShift({ channels, startingNumber, count, step });
      const rawStop = bruteForceStopNumber(channels, startingNumber, count, step);

      // Exactly the channels the slow walk says must move, and no others.
      const required = channels
        .filter((c) => c.channel_number !== null && c.channel_number >= startingNumber && c.channel_number < rawStop)
        .map((c) => c.channel_number as number)
        .sort((a, b) => a - b);
      expect(shiftedNumbers(plan)).toEqual(required);

      // The reported stop is the first lattice point at or above the raw stop.
      const tick = (n: number) => Math.round(n * 1000);
      expect(tick(plan.stopNumber)).toBeGreaterThanOrEqual(tick(rawStop));
      expect(tick(plan.stopNumber - step)).toBeLessThan(tick(rawStop));
    }
  });

  // Property 7
  it('walks past a gap narrower than the shift and moves the far side by the full shift', () => {
    const channels = [...run(201, 500, GROUP_A), ...run(503, 599, GROUP_B), ...run(700, 799, GROUP_C)];
    const plan = planChannelNumberShift({ channels, startingNumber: 300, count: 5 });

    // The 501-502 gap is two wide and the shift is five, so it does not stop
    // there. It stops above B, and C never moves.
    expect(plan.stopNumber).toBe(600);
    expect(plan.shiftAmount).toBe(5);
    expect(plan.shifts.every((s) => s.toNumber === s.fromNumber + 5)).toBe(true);
    expect(plan.shifts.some((s) => s.channel.channel_group_id === GROUP_B)).toBe(true);
    expect(plan.shifts.some((s) => s.channel.channel_group_id === GROUP_C)).toBe(false);
    expectNoNewDuplicates(channels, applyPlan(channels, plan));
  });

  // Property 8
  it('terminates above the highest number when the target group is last', () => {
    const channels = run(201, 500, GROUP_A);
    const plan = planChannelNumberShift({ channels, startingNumber: 300, count: 3 });

    expect(plan.stopNumber).toBe(501);
    expect(plan.shifts).toHaveLength(201);
    expectNoNewDuplicates(channels, applyPlan(channels, plan));
  });

  // Property 9
  it('plans nothing when the insertion point is already free', () => {
    const channels = poFixture().filter((c) => c.channel_number !== 350);

    expect(planChannelNumberShift({ channels, startingNumber: 350, count: 1 }).shifts).toEqual([]);
    expect(planChannelNumberShift({ channels, startingNumber: 501, count: 5 }).shifts).toEqual([]);
    expect(planChannelNumberShift({ channels, startingNumber: 9000, count: 2 }).shifts).toEqual([]);
  });

  // Property 10
  it('plans decimal inserts on the 0.1 step without float drift', () => {
    const channels = [channel(38.1, GROUP_A), channel(38.2, GROUP_A), channel(39, GROUP_A)];
    const plan = planChannelNumberShift({ channels, startingNumber: 38.1, count: 3, step: 0.1 });

    expect(plan.shiftAmount).toBe(0.3);
    expect(plan.shifts.map((s) => [s.fromNumber, s.toNumber])).toEqual([
      [38.1, 38.4],
      [38.2, 38.5],
    ]);
    expect(plan.stopNumber).toBe(38.3);
    expect(plan.shifts.some((s) => s.fromNumber === 39)).toBe(false);
  });

  it('compares on exact ticks where a float sum would misjudge the boundary', () => {
    // 0.7 + 0.1 is 0.7999999999999999 in IEEE 754, so the replaced code
    // compared below 0.8 and reported a gap that was not there.
    expect(0.7 + 0.1 < 0.8).toBe(true);

    const channels = [channel(20.7, GROUP_A), channel(20.8, GROUP_A), channel(20.9, GROUP_A)];
    const plan = planChannelNumberShift({ channels, startingNumber: 20.7, count: 1, step: 0.1 });

    expect(plan.shifts.map((s) => [s.fromNumber, s.toNumber])).toEqual([
      [20.7, 20.8],
      [20.8, 20.9],
      [20.9, 21],
    ]);
    expect(plan.stopNumber).toBe(21);
  });

  it('ignores channels that have no number and channels being moved out of the way', () => {
    const moving = channel(305, GROUP_B);
    const channels = [...run(300, 310, GROUP_A), channel(null, GROUP_A), moving];

    const withMover = planChannelNumberShift({ channels, startingNumber: 305, count: 1 });
    expect(withMover.shifts.some((s) => s.channel.id === moving.id)).toBe(true);

    const excludingMover = planChannelNumberShift({
      channels,
      startingNumber: 305,
      count: 1,
      excludeIds: [moving.id],
    });
    expect(excludingMover.shifts.some((s) => s.channel.id === moving.id)).toBe(false);
    expect(excludingMover.shifts.every((s) => s.channel.channel_number !== null)).toBe(true);
  });

  it('returns an empty plan for a non-positive or non-finite claim', () => {
    const channels = run(300, 310, GROUP_A);
    expect(planChannelNumberShift({ channels, startingNumber: 300, count: 0 }).shifts).toEqual([]);
    expect(planChannelNumberShift({ channels, startingNumber: 300, count: -3 }).shifts).toEqual([]);
    expect(planChannelNumberShift({ channels, startingNumber: NaN, count: 2 }).shifts).toEqual([]);
    expect(planChannelNumberShift({ channels, startingNumber: 300, count: 2, step: 0 }).shifts).toEqual([]);
  });
});

// Property 11
describe('both push-down call sites plan through this module', () => {
  it('produces one plan for the bulk-create and cross-group-move parameterisations', () => {
    // Same lineup, same insertion point, same number of channels claiming
    // numbers. Bulk create adds new channels; a cross-group move brings three
    // channels in from group C, which vacate their own numbers on the way.
    const lineup = [...run(201, 500, GROUP_A), channel(8000, GROUP_A), ...run(600, 699, GROUP_B)];
    const movers = run(900, 902, GROUP_C);

    const bulkCreatePlan = planChannelNumberShift({
      channels: lineup,
      startingNumber: 300,
      count: 3,
      step: 1,
    });
    const crossGroupMovePlan = planChannelNumberShift({
      channels: [...lineup, ...movers],
      startingNumber: 300,
      count: movers.length,
      excludeIds: movers.map((c) => c.id),
    });

    expect(shiftedNumbers(crossGroupMovePlan)).toEqual(shiftedNumbers(bulkCreatePlan));
    expect(crossGroupMovePlan.stopNumber).toBe(bulkCreatePlan.stopNumber);
    expect(crossGroupMovePlan.shiftAmount).toBe(bulkCreatePlan.shiftAmount);
  });

  // A tripwire, not the behavioural proof above: it catches a future edit that
  // reintroduces a local planner at either site instead of changing this one.
  it('leaves no group-interval planner behind in either call site', () => {
    const srcRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
    const callSites = ['App.tsx', 'components/ChannelsPane.tsx'];

    for (const relativePath of callSites) {
      const source = fs.readFileSync(path.join(srcRoot, relativePath), 'utf8');
      expect(source).toContain('planChannelNumberShift');
      expect(source).not.toContain('currentMaxAfterShift');
    }
  });
});
