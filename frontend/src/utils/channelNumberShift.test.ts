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

/**
 * Independent reference for the stop point, deliberately written the slow
 * obvious way: walk one tick at a time and return the start of the first run
 * of free ticks wide enough for the shift. Used to check the planner's
 * gap-scan shortcut, which is the part that could silently stop too early.
 *
 * This returns the raw stop, not the lattice-rounded `stopNumber` the planner
 * reports, so nothing about the planner's rounding leaks into the reference.
 */
function bruteForceStopNumber(
  channels: TestChannel[],
  startingNumber: number,
  count: number,
  step: number,
): number {
  const scale = 1000;
  const occupied = new Set<number>();
  for (const c of channels) {
    if (c.channel_number !== null) occupied.add(Math.round(c.channel_number * scale));
  }
  const startTick = Math.round(startingNumber * scale);
  const shiftTicks = Math.round(step * scale) * count;
  const maxTick = occupied.size > 0 ? Math.max(...occupied) : startTick;

  let freeRun = 0;
  for (let tick = startTick; tick <= maxTick + shiftTicks + 1; tick++) {
    freeRun = occupied.has(tick) ? 0 : freeRun + 1;
    if (freeRun >= shiftTicks) return (tick - shiftTicks + 1) / scale;
  }
  throw new Error('brute force found no stop point, which cannot happen above the highest number');
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
  it('never creates a duplicate over randomised lineups with holes, overlaps and duplicates', () => {
    // Deterministic 32-bit LCG so a failure is reproducible from the seed.
    let seed = 0x5eed1e;
    const rand = (n: number) => {
      seed = (seed * 1664525 + 1013904223) >>> 0;
      return seed % n;
    };

    for (let iteration = 0; iteration < 250; iteration++) {
      nextId = 1;
      const channels: TestChannel[] = [];
      const bucketCount = 1 + rand(4);
      for (let bucket = 0; bucket < bucketCount; bucket++) {
        const from = 1 + rand(120);
        const width = rand(40);
        for (let n = from; n <= from + width; n++) {
          // Holes, and occasionally a second occupant on the same number.
          if (rand(5) === 0) continue;
          channels.push(channel(n, bucket));
          if (rand(11) === 0) channels.push(channel(n, bucket));
        }
      }
      if (channels.length === 0) continue;

      const startingNumber = 1 + rand(160);
      const count = 1 + rand(6);
      const plan = planChannelNumberShift({ channels, startingNumber, count });
      const finals = applyPlan(channels, plan);

      expectNoNewDuplicates(channels, finals);

      // The claimed window must end up empty of everything that stayed behind.
      for (const [, finalNumber] of finals) {
        const claimsIt = finalNumber >= startingNumber && finalNumber < startingNumber + count;
        expect(claimsIt).toBe(false);
      }
    }
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
