/**
 * The proposed FINAL channel numbering of an Edit Mode session, and what is
 * wrong with it (beads `enhancedchannelmanager-vdxbx`,
 * `enhancedchannelmanager-ic884.2`, `enhancedchannelmanager-ic884.5`).
 *
 * Everything here is derived from `stagedOperations`. That is deliberate and
 * load-bearing: the operation queue is the one collection `stageOperation`,
 * `localUndo`, `localRedo`, `discard` and the persisted ledger all maintain,
 * so a view computed from it cannot drift out of step with it. In particular
 * an operator's acknowledgement of a duplicate rides on the OPERATION that
 * created the duplicate rather than in a registry beside it, so undoing the
 * operation withdraws the acknowledgement automatically and deleting an
 * unrelated earlier action cannot empty it.
 *
 * The three questions this module answers, all against the same materialised
 * final state:
 *
 *   1. "Is this number already taken?" — {@link channelsHoldingNumber}, asked
 *      before a single edit is staged, against effective local state rather
 *      than the last server-loaded list.
 *   2. "Does the whole staged plan land somewhere legal?" —
 *      {@link buildFinalNumberingPlan} plus {@link validateFinalNumberingPlan},
 *      asked before Apply mutates anything.
 *   3. "What will change that the operator did not type?" —
 *      {@link deriveAutomaticRenames}, so a numbering-driven rename is visible
 *      in the change preview.
 *
 * What this module does NOT do is enforce uniqueness. Bead
 * `enhancedchannelmanager-ic884.1` decided that deliberately: Dispatcharr
 * declares `channel_number` as a non-unique float and permits duplicates, and
 * real lineups have them. So a duplicate that already exists on the server is
 * never reported, and a duplicate the operator confirmed is never reported
 * twice. Only a duplicate this session created and nobody agreed to is an
 * error.
 *
 * Comparison is canonical, on the tenths grid the contract defines
 * (`channelNumber.ts`), so `7`, `7.0` and `07` are one number and `0.7 + 0.1`
 * is the channel number `0.8`.
 *
 * Tests: `channelNumberPlan.test.ts`.
 */

import { isValidChannelNumber } from './channelNumber';
import { computeAutoRename } from './channelRename';
import type { StagedOperation, ApiCallSpec } from '../types/editMode';

/** The minimum shape this module needs of a channel. `Channel` satisfies it. */
export interface NumberedChannel {
  id: number;
  name: string;
  channel_number: number | null;
}

/**
 * Every float at or above `2**53` is an exact integer, so scaling one by ten
 * would leave the safe-integer range (and `1e308 * 10` is `Infinity`, which
 * would make every absurd value compare equal to every other). Above the floor
 * the float IS the identity; below it, the tenths tick is. Mirrors
 * `EXACT_INTEGER_FLOOR` in `channelNumber.ts` and `_EXACT_INTEGER_FLOOR` in
 * `backend/channel_number.py`.
 */
const EXACT_INTEGER_FLOOR = 2 ** 53;

/**
 * The bound below which distinct tenths keep distinct floats. From `2**49` up,
 * adjacent floats are `0.125` apart — wider than a tenth — so a run of tenths
 * collapses onto one float and a fractional step stops stepping. The same
 * measurement and the same bound are recorded on `channel_number_from_ticks`
 * in `backend/channel_number.py`.
 */
const DISTINCT_TENTHS_CEILING = 2 ** 49;

const TENTHS = 10;

/**
 * The slot a channel number occupies. Two numbers share a slot exactly when
 * this module calls them the same channel number.
 *
 * A string rather than a number so the two regimes cannot collide: below the
 * floor a tick index, at or above it the float's own identity, and the `t`/`x`
 * prefixes keep one from ever being read as the other.
 */
function slotKey(value: number): string {
  if (!Number.isFinite(value)) return `n${String(value)}`;
  if (Math.abs(value) >= EXACT_INTEGER_FLOOR) return `x${value}`;
  return `t${Math.round(value * TENTHS)}`;
}

/**
 * Whether two channel numbers are the same number, canonically.
 *
 * `null` is "unassigned", which is not a slot: two unnumbered channels do not
 * collide with each other, so an unassigned value is never equal to anything,
 * including another unassigned value.
 */
export function sameChannelNumber(
  a: number | null | undefined,
  b: number | null | undefined,
): boolean {
  if (typeof a !== 'number' || typeof b !== 'number') return false;
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
  return slotKey(a) === slotKey(b);
}

/**
 * The channels effectively holding `value`, ignoring `excludeIds`.
 *
 * `excludeIds` is how the channel being edited is kept out of its own conflict
 * check, so retaining a channel's current number never warns.
 */
export function channelsHoldingNumber<T extends NumberedChannel>(
  channels: readonly T[],
  value: number | null | undefined,
  excludeIds: Iterable<number> = [],
): T[] {
  if (typeof value !== 'number' || !Number.isFinite(value)) return [];
  const excluded = new Set(excludeIds);
  return channels.filter(
    (channel) => !excluded.has(channel.id) && sameChannelNumber(channel.channel_number, value),
  );
}

/** Where one channel ends up once every staged operation has been applied. */
export interface NumberPlacement {
  channelId: number;
  name: string;
  /** The number this channel holds in the proposed final state. */
  number: number | null;
  /** The number it held before this session staged anything; `undefined` for a channel created here. */
  baseNumber: number | null | undefined;
  /**
   * The staged operations responsible for it holding `number`, empty when the
   * session leaves the channel on the number it started with. Re-asserting a
   * channel's own number is not a placement, so a pre-existing duplicate is
   * never turned into this session's problem by an unrelated edit.
   */
  placedByOperationIds: string[];
  /** Slots the operator explicitly accepted a duplicate on for this channel. */
  acknowledgedSlots: Set<string>;
}

export interface FinalNumberingPlan {
  placements: NumberPlacement[];
  operationsById: Map<string, StagedOperation>;
}

/** An acknowledgement travels on the operation, so it survives serialisation. */
function acknowledgedNumberOf(apiCall: ApiCallSpec): number | undefined {
  if (apiCall.type === 'updateChannel' || apiCall.type === 'createChannel') {
    return apiCall.acknowledgedDuplicateNumber;
  }
  return undefined;
}

interface MutablePlacement {
  channelId: number;
  name: string;
  number: number | null;
  baseNumber: number | null | undefined;
  touchedBy: string[];
  acknowledgedSlots: Set<string>;
}

/**
 * Materialise the proposed final numbering from the server list plus every
 * staged operation — edits, creates, deletes, and bulk range assignments.
 *
 * Order matters and is the staging order: later operations win, exactly as the
 * commit will apply them.
 */
export function buildFinalNumberingPlan(
  base: readonly NumberedChannel[],
  operations: readonly StagedOperation[],
): FinalNumberingPlan {
  const byId = new Map<number, MutablePlacement>();
  const order: number[] = [];

  const register = (placement: MutablePlacement) => {
    byId.set(placement.channelId, placement);
    order.push(placement.channelId);
  };

  for (const channel of base) {
    register({
      channelId: channel.id,
      name: channel.name,
      number: channel.channel_number,
      baseNumber: channel.channel_number,
      touchedBy: [],
      acknowledgedSlots: new Set(),
    });
  }

  // A channel created in this session carries the temp id its `createChannel`
  // allocated (the id every other operation in the ledger references). When
  // the operation predates its own after-snapshot — a hand-built operation in
  // a test, or an operation restored before replay — fall back to an id below
  // every one already in play, so two creates can never share one.
  let nextSyntheticId = -1;
  for (const id of byId.keys()) nextSyntheticId = Math.min(nextSyntheticId, id - 1);

  const setNumber = (
    placement: MutablePlacement,
    value: number | null,
    operation: StagedOperation,
  ) => {
    placement.number = value;
    placement.touchedBy.push(operation.id);
    const acknowledged = acknowledgedNumberOf(operation.apiCall);
    if (typeof acknowledged === 'number' && Number.isFinite(acknowledged)) {
      placement.acknowledgedSlots.add(slotKey(acknowledged));
    }
  };

  for (const operation of operations) {
    const { apiCall } = operation;
    switch (apiCall.type) {
      case 'updateChannel': {
        const placement = byId.get(apiCall.channelId);
        if (!placement) break;
        if (apiCall.data.name !== undefined) placement.name = apiCall.data.name;
        if (apiCall.data.channel_number !== undefined) {
          setNumber(placement, apiCall.data.channel_number ?? null, operation);
        }
        break;
      }
      case 'createChannel': {
        const snapshotId = operation.afterSnapshot[0]?.id;
        const channelId =
          typeof snapshotId === 'number' && snapshotId < 0 ? snapshotId : nextSyntheticId;
        nextSyntheticId = Math.min(nextSyntheticId, channelId) - 1;
        const placement: MutablePlacement = {
          channelId,
          name: apiCall.name,
          number: null,
          baseNumber: undefined,
          touchedBy: [],
          acknowledgedSlots: new Set(),
        };
        register(placement);
        setNumber(placement, apiCall.channelNumber ?? null, operation);
        break;
      }
      case 'deleteChannel': {
        byId.delete(apiCall.channelId);
        break;
      }
      case 'bulkAssignChannelNumbers': {
        const start = apiCall.startingNumber ?? 1;
        apiCall.channelIds.forEach((channelId, index) => {
          const placement = byId.get(channelId);
          if (!placement) return;
          setNumber(placement, start + index, operation);
        });
        break;
      }
      default:
        break;
    }
  }

  const placements: NumberPlacement[] = [];
  for (const channelId of order) {
    const placement = byId.get(channelId);
    if (!placement) continue;
    // Judged against the BASE number, not the running one: a channel that was
    // moved and moved back has not been placed anywhere, whatever route it
    // took to get there.
    const moved =
      placement.baseNumber === undefined ||
      !sameChannelNumber(placement.baseNumber, placement.number) ||
      (placement.baseNumber === null) !== (placement.number === null);
    placements.push({
      channelId: placement.channelId,
      name: placement.name,
      number: placement.number,
      baseNumber: placement.baseNumber,
      placedByOperationIds: moved ? [...placement.touchedBy] : [],
      acknowledgedSlots: placement.acknowledgedSlots,
    });
  }

  return {
    placements,
    operationsById: new Map(operations.map((operation) => [operation.id, operation])),
  };
}

export type NumberingIssueType = 'duplicate_channel_number' | 'invalid_channel_number';

export interface NumberingPlanIssue {
  type: NumberingIssueType;
  severity: 'error';
  /** Operator-facing sentence naming the number, the channels, and what to do. */
  message: string;
  /** The number the issue is about. */
  channelNumber: number;
  /** Every channel sitting on it in the proposed final state. */
  channelIds: number[];
  /** The staged operations to change to resolve it. */
  operationIds: string[];
  operationDescriptions: string[];
}

function describeChannels(placements: readonly NumberPlacement[]): string {
  return placements.map((placement) => `"${placement.name}"`).join(', ');
}

/**
 * What is wrong with the proposed final state, if anything.
 *
 * Two properties, checked over the final state rather than over any one
 * operation, so no combination of operations can slip between them:
 *
 *   - Every number this session PUT a channel on is in contract. A number the
 *     session did not touch is left alone: it came from Dispatcharr, which
 *     enforces nothing, and refusing to Apply because of a value the operator
 *     cannot reach from here would be a dead end rather than a safeguard.
 *   - No number this session put a channel on is shared, unless every channel
 *     this session put there was put there deliberately. A duplicate that was
 *     already on the server is not reported, and an acknowledged one is not
 *     reported again.
 */
export function validateFinalNumberingPlan(plan: FinalNumberingPlan): NumberingPlanIssue[] {
  const issues: NumberingPlanIssue[] = [];
  const describeOperations = (operationIds: readonly string[]) =>
    operationIds.map((id) => plan.operationsById.get(id)?.description ?? id);

  const bySlot = new Map<string, NumberPlacement[]>();
  for (const placement of plan.placements) {
    if (placement.number === null) continue;
    const placed = placement.placedByOperationIds.length > 0;
    if (placed && !isValidChannelNumber(placement.number)) {
      const operationIds = [...placement.placedByOperationIds];
      issues.push({
        type: 'invalid_channel_number',
        severity: 'error',
        message:
          `"${placement.name}" would end up on channel number ${placement.number}, which is not a ` +
          'valid channel number. Channel numbers must be 0 or greater and support one decimal ' +
          'place (for example 1.0 or 1.1).',
        channelNumber: placement.number,
        channelIds: [placement.channelId],
        operationIds,
        operationDescriptions: describeOperations(operationIds),
      });
      continue;
    }
    const key = slotKey(placement.number);
    const bucket = bySlot.get(key);
    if (bucket) bucket.push(placement);
    else bySlot.set(key, [placement]);
  }

  for (const [key, occupants] of bySlot) {
    if (occupants.length < 2) continue;
    const contributors = occupants.filter(
      (placement) => placement.placedByOperationIds.length > 0,
    );
    // Nobody moved onto this number in this session, so the duplicate is the
    // lineup's, not the operator's. ic884.1 declined to enforce uniqueness.
    if (contributors.length === 0) continue;
    const accidental = contributors.filter((placement) => !placement.acknowledgedSlots.has(key));
    if (accidental.length === 0) continue;

    const operationIds = Array.from(
      new Set(accidental.flatMap((placement) => placement.placedByOperationIds)),
    );
    const others = occupants.filter((placement) => !accidental.includes(placement));
    const number = accidental[0].number as number;
    issues.push({
      type: 'duplicate_channel_number',
      severity: 'error',
      message:
        `Channel number ${number} would be used by ${occupants.length} channels: ` +
        `${describeChannels(accidental)} would join ${describeChannels(others)}. ` +
        'Change one of them, or confirm the duplicate where you staged it.',
      channelNumber: number,
      channelIds: occupants.map((placement) => placement.channelId),
      operationIds,
      operationDescriptions: describeOperations(operationIds),
    });
  }

  return issues;
}

/**
 * The one operator-facing sentence a refused range uses.
 *
 * Deliberately distinct from `CHANNEL_NUMBER_RULE_MESSAGE`: the start of a
 * range can be a perfectly valid channel number and still describe a run that
 * cannot exist, so quoting the value rule would send the operator to look at
 * the wrong field.
 */
export const CHANNEL_NUMBER_RANGE_RULE_MESSAGE =
  'That range cannot be numbered: the run has to start at a valid channel number and every ' +
  'number in it has to be a distinct, valid channel number.';

/**
 * Why `start`, `count` and `step` cannot describe a run of channel numbers, or
 * `null` when they can.
 *
 * The run is `start, start + step, ..., start + (count - 1) * step`, and every
 * one of those has to be a channel number the contract can hold AND distinct
 * from its neighbours. The second half is what a per-value check misses:
 * consecutive integers stop being distinct above `2**53`, and consecutive
 * tenths stop being distinct above `2**49`, so a range that starts legally can
 * still pile several channels onto one number at its tail.
 */
export function channelNumberRangeError(
  start: number,
  count: number,
  step: number = 1,
): string | null {
  if (!isValidChannelNumber(start)) return CHANNEL_NUMBER_RANGE_RULE_MESSAGE;
  if (!Number.isInteger(count) || count < 1) return CHANNEL_NUMBER_RANGE_RULE_MESSAGE;
  if (!isValidChannelNumber(step) || step <= 0) return CHANNEL_NUMBER_RANGE_RULE_MESSAGE;
  if (count === 1) return null;

  // Walk in ticks so the arithmetic is exact, then read the answer back once.
  if (start >= EXACT_INTEGER_FLOOR) return CHANNEL_NUMBER_RANGE_RULE_MESSAGE;
  const startTicks = Math.round(start * TENTHS);
  const stepTicks = Math.round(step * TENTHS);
  const lastTicks = startTicks + (count - 1) * stepTicks;
  if (!Number.isSafeInteger(lastTicks)) return CHANNEL_NUMBER_RANGE_RULE_MESSAGE;

  const last = lastTicks / TENTHS;
  if (!isValidChannelNumber(last)) return CHANNEL_NUMBER_RANGE_RULE_MESSAGE;
  const wholeStep = stepTicks % TENTHS === 0;
  // A whole step keeps neighbours distinct while they stay safe integers; a
  // fractional one only while tenths stay distinct floats.
  const ceiling = wholeStep ? Number.MAX_SAFE_INTEGER : DISTINCT_TENTHS_CEILING;
  if (last > ceiling) return CHANNEL_NUMBER_RANGE_RULE_MESSAGE;
  return null;
}

/** A channel name a numbering change rewrote, with nobody typing it. */
export interface AutomaticRename {
  channelId: number;
  from: string;
  to: string;
  fromNumber: number | null;
  toNumber: number | null;
}

/**
 * Every rename the numbering caused, read back off the staged operations.
 *
 * Derived rather than recorded, and derived by REPRODUCING the producer's own
 * computation: a staged `updateChannel` carrying both a number and a name is
 * an automatic rename exactly when `computeAutoRename` on its before-snapshot
 * yields that name. An operator who changed the number and typed a name in the
 * same save is therefore not reported as an automatic rename, and no producer
 * has to remember to flag itself.
 *
 * The `auto_rename_channel_number` setting is not an input here, and does not
 * need to be. It gates the PRODUCERS: when it is off, no handler computes a
 * name, so no staged operation carries one and this returns nothing. Reading
 * the staged consequences rather than the setting is also what keeps the
 * preview honest across a setting changed mid-session, and across a ledger
 * restored into a session whose setting differs from the one that staged it.
 */
export function deriveAutomaticRenames(
  operations: readonly StagedOperation[],
): AutomaticRename[] {
  const renames: AutomaticRename[] = [];
  for (const operation of operations) {
    const { apiCall } = operation;
    if (apiCall.type !== 'updateChannel') continue;
    const { channel_number: toNumber, name: to } = apiCall.data;
    if (toNumber === undefined || to === undefined) continue;
    const before = operation.beforeSnapshot.find((snapshot) => snapshot.id === apiCall.channelId);
    if (!before) continue;
    const expected = computeAutoRename(before.name, before.channel_number, toNumber ?? null);
    if (expected !== to) continue;
    renames.push({
      channelId: apiCall.channelId,
      from: before.name,
      to,
      fromNumber: before.channel_number,
      toNumber: toNumber ?? null,
    });
  }
  return renames;
}
