/**
 * Whether the numbers this session is about to write are still the numbers it
 * was looking at (bead `enhancedchannelmanager-ic884.4`).
 *
 * WHAT THE UPSTREAM API MAKES POSSIBLE, measured rather than assumed. The live
 * Dispatcharr 0.28.x schema (`GET /api/schema/?format=json`, HTTP 200, ~717KB,
 * fetched 2026-08-15) contains ZERO occurrences of `If-Match`, `If-None-Match`,
 * `If-Unmodified-Since`, `ETag` or `412`, and neither `Channel` nor
 * `PatchedChannel` carries a version or modified-at field. There is no
 * conditional update and no revision token, so nothing here can PREVENT a
 * concurrent overwrite. What it can do is READ the current state, compare it
 * against the state the session was staged on, and refuse to write over a
 * difference the operator has not seen. A change that lands between that read
 * and the write is still lost, and no wording in this module says otherwise.
 *
 * THE BASELINE is the channel list as it stood when the session began —
 * `EditModeState.baselineSnapshot`, captured by `enterEditMode` and RE-captured
 * by `restoreStagedLedger`, so a session rebuilt from `sessionStorage` compares
 * against today's lineup and never against the one its dead predecessor saw.
 * The persisted ledger deliberately does not carry a baseline: a stored
 * baseline would be exactly the stale value the whole check exists to catch.
 *
 * A RECONCILE DECISION IS BOUND TO THE CONFLICT IT RESOLVED, the same way a
 * duplicate acknowledgement is bound to the collision it consented to (bead
 * `enhancedchannelmanager-vdxbx`). "Keep mine" is consent to overwrite ONE
 * observed change — this channel, this baseline value, this server value — and
 * it rides on the staged operation rather than in a registry beside it, so
 * undoing the operation withdraws the decision, a restored ledger carries it,
 * and a server value that moves AGAIN before Apply makes the decision stop
 * matching and the conflict is raised once more.
 *
 * Comparison is the canonical one `channelNumberPlan.ts` defines, so `7` and
 * `7.0` are one number and `7.1` is a different one.
 *
 * Tests: `channelNumberConcurrency.test.ts`.
 */

import { sameChannelNumber } from './channelNumberPlan';
import type { ChannelSnapshot } from '../types';
import type {
  ConcurrentNumberAcknowledgement,
  StagedOperation,
} from '../types/editMode';

/** The minimum shape this module needs of the server's current lineup. */
export interface ServerChannelNumber {
  id: number;
  name: string;
  channel_number: number | null;
}

/** Why one channel's staged number cannot be written without a decision. */
export type NumberingConflictKind =
  /** Somebody changed this channel's number after the baseline was captured. */
  | 'number-changed'
  /** The channel is no longer on the server at all. */
  | 'channel-deleted';

/** One channel whose staged number would overwrite work done since the baseline. */
export interface NumberingConflict {
  kind: NumberingConflictKind;
  channelId: number;
  /** The name the SERVER has now, falling back to the staged plan's. */
  name: string;
  /** The number the session was staged against. */
  baselineNumber: number | null;
  /** The number the server holds now. `null` for a deleted channel. */
  serverNumber: number | null;
  /** The number this session would write. */
  proposedNumber: number | null;
  /** The staged operations that would write it. */
  operationIds: string[];
  operationDescriptions: string[];
  /**
   * True when the placement came from a range assignment. A range's numbers
   * are positional, so one channel cannot be taken out of it without moving
   * everybody after it — see {@link applyReconcileDecisions}.
   */
  fromRangeAssignment: boolean;
}

export interface DetectNumberingConflictsInput {
  /** The lineup as it stood when this session began. */
  baseline: readonly ChannelSnapshot[];
  /** The lineup as the server reports it NOW. */
  server: readonly ServerChannelNumber[];
  /** The staged operations, in staging order. */
  operations: readonly StagedOperation[];
}

/** One channel whose number this session will PATCH, and what with. */
interface PlannedNumberWrite {
  channelId: number;
  proposedNumber: number | null;
  operations: StagedOperation[];
  fromRangeAssignment: boolean;
}

/**
 * Every channel whose number this session will actually WRITE.
 *
 * Deliberately not read off {@link FinalNumberingPlan.placements}: that answers
 * "where does this channel end up compared to where it started", which
 * correctly says "nowhere" for a channel moved and moved back — and a channel
 * moved and moved back is still PATCHed, so it still overwrites whatever
 * somebody else put there in the meantime. Re-asserting a channel's own number
 * is not a placement, but it is a write, and this check is about writes.
 *
 * `deleteChannel` withdraws the entry, mirroring the executor's own
 * consolidation, which drops operations targeting a channel the same batch
 * deletes.
 */
function plannedNumberWrites(
  operations: readonly StagedOperation[],
): Map<number, PlannedNumberWrite> {
  const writes = new Map<number, PlannedNumberWrite>();
  const record = (
    channelId: number,
    proposedNumber: number | null,
    operation: StagedOperation,
    fromRangeAssignment: boolean,
  ) => {
    const existing = writes.get(channelId);
    if (existing) {
      existing.proposedNumber = proposedNumber;
      existing.operations.push(operation);
      existing.fromRangeAssignment = existing.fromRangeAssignment || fromRangeAssignment;
      return;
    }
    writes.set(channelId, {
      channelId,
      proposedNumber,
      operations: [operation],
      fromRangeAssignment,
    });
  };

  for (const operation of operations) {
    const { apiCall } = operation;
    switch (apiCall.type) {
      case 'updateChannel':
        if (apiCall.data.channel_number !== undefined) {
          record(apiCall.channelId, apiCall.data.channel_number ?? null, operation, false);
        }
        break;
      case 'bulkAssignChannelNumbers': {
        const start = apiCall.startingNumber ?? 1;
        apiCall.channelIds.forEach((channelId, offset) => {
          record(channelId, start + offset, operation, true);
        });
        break;
      }
      case 'deleteChannel':
        writes.delete(apiCall.channelId);
        break;
      default:
        break;
    }
  }
  return writes;
}

/** The acknowledgements a staged operation carries, or an empty list. */
export function concurrentAcknowledgementsOf(
  operation: StagedOperation,
): readonly ConcurrentNumberAcknowledgement[] {
  const { apiCall } = operation;
  if (apiCall.type !== 'updateChannel' && apiCall.type !== 'bulkAssignChannelNumbers') {
    return [];
  }
  return apiCall.acknowledgedConcurrentChanges ?? [];
}

/**
 * Every channel this session would renumber over somebody else's change.
 *
 * A conflict is raised when the number the SERVER holds now is not the number
 * the session's BASELINE recorded — regardless of what the session proposes to
 * write, because "somebody else moved this channel" is the fact the operator
 * has to see. Re-asserting the same value is not exempt: agreeing by accident
 * is not agreeing.
 *
 * A channel this session does not place a number on is never reported, however
 * far it has moved: an unrelated edit elsewhere in the lineup is not this
 * session's business, and blocking Apply over one would be the "unrelated
 * changes block a valid plan" failure the bead names.
 *
 * A channel created in this session has no baseline and cannot conflict; a
 * SERVER channel that has appeared on a number the plan proposes is a
 * different question, answered by the final-state preflight run against the
 * fresh lineup (`validateFinalNumberingPlan`).
 */
export function detectNumberingConflicts({
  baseline,
  server,
  operations,
}: DetectNumberingConflictsInput): NumberingConflict[] {
  const baselineById = new Map(baseline.map((entry) => [entry.id, entry]));
  const serverById = new Map(server.map((channel) => [channel.id, channel]));

  const conflicts: NumberingConflict[] = [];
  for (const write of plannedNumberWrites(operations).values()) {
    // A negative id is a channel this session is creating: no baseline, no
    // server row, nothing to conflict with.
    if (write.channelId < 0) continue;

    const baselineEntry = baselineById.get(write.channelId);
    // Not in the baseline either, so this session never saw it as an existing
    // channel. Nothing to compare against.
    if (baselineEntry === undefined) continue;

    const serverChannel = serverById.get(write.channelId);
    const baselineNumber = baselineEntry.channel_number ?? null;

    const conflict = (
      kind: NumberingConflictKind,
      name: string,
      serverNumber: number | null,
    ): NumberingConflict => ({
      kind,
      channelId: write.channelId,
      name,
      baselineNumber,
      serverNumber,
      proposedNumber: write.proposedNumber,
      operationIds: write.operations.map((operation) => operation.id),
      operationDescriptions: write.operations.map((operation) => operation.description),
      fromRangeAssignment: write.fromRangeAssignment,
    });

    if (serverChannel === undefined) {
      conflicts.push(conflict('channel-deleted', baselineEntry.name, null));
      continue;
    }

    const serverNumber = serverChannel.channel_number ?? null;
    if (numbersAgree(baselineNumber, serverNumber)) continue;

    // Consent, checked against the conflict it named rather than merely
    // carried. A server value that moved again since the decision no longer
    // matches, so the operator is asked about the change that is really there.
    const consented = write.operations.some((operation) =>
      concurrentAcknowledgementsOf(operation).some(
        (acknowledgement) =>
          acknowledgement.channelId === write.channelId &&
          numbersAgree(acknowledgement.baselineNumber, baselineNumber) &&
          numbersAgree(acknowledgement.serverNumber, serverNumber),
      ),
    );
    if (consented) continue;

    conflicts.push(
      conflict('number-changed', serverChannel.name || baselineEntry.name, serverNumber),
    );
  }
  return conflicts;
}

/** Canonical equality that also calls two unassigned values the same value.
 *
 *  Deliberately NOT `sameChannelNumber`, which calls `null` different from
 *  everything including itself — correct when asking "do these two channels
 *  collide?", wrong when asking "is this the same value I recorded?". */
function numbersAgree(a: number | null, b: number | null): boolean {
  if (a === null || b === null) return a === b;
  return sameChannelNumber(a, b);
}

export type ReconcileChoice = 'keep-mine' | 'take-theirs';

export interface ReconcileDecision {
  channelId: number;
  choice: ReconcileChoice;
  /** The conflict this decision answers. Bound, so it cannot outlive it. */
  baselineNumber: number | null;
  serverNumber: number | null;
}

export interface ReconcileResult {
  operations: StagedOperation[];
  /** Operations removed entirely, with the reason to show the operator. */
  removed: { id: string; description: string; detail: string }[];
}

/**
 * Rewrite the staged plan to carry the operator's per-conflict decisions.
 *
 * `keep-mine` attaches the acknowledgement — this channel, this baseline, this
 * server value — to every staged operation that places the channel's number.
 * Nothing else about the operation changes: the write is the one the operator
 * already staged, and the acknowledgement is only what stops the check asking
 * again about the answer it just got.
 *
 * `take-theirs` withdraws the number from the plan:
 *
 * * an `updateChannel` loses its `channel_number` key, and is REMOVED entirely
 *   when that was the only thing it carried, because an update with an empty
 *   payload is an operation that does nothing and still counts as one;
 * * a `bulkAssignChannelNumbers` is removed WHOLE. Its numbers are positional,
 *   so dropping one channel out of the middle would shift every channel after
 *   it onto a different number from the one the operator was shown — which
 *   would break the "what you saw is what Apply produces" property in the act
 *   of protecting it. Removing the range is honest and is reported.
 *
 * The result is a new `stagedOperations` list: the operation queue stays the
 * single source of truth, and everything derived from it — the staged groups,
 * the side effects, the change summary, the final-state preflight — follows
 * automatically.
 */
export function applyReconcileDecisions(
  operations: readonly StagedOperation[],
  decisions: readonly ReconcileDecision[],
): ReconcileResult {
  const keepMine = new Map<number, ReconcileDecision>();
  const takeTheirs = new Set<number>();
  for (const decision of decisions) {
    if (decision.choice === 'keep-mine') keepMine.set(decision.channelId, decision);
    else takeTheirs.add(decision.channelId);
  }

  const next: StagedOperation[] = [];
  const removed: ReconcileResult['removed'] = [];

  for (const operation of operations) {
    const { apiCall } = operation;

    if (apiCall.type === 'bulkAssignChannelNumbers') {
      const surrendered = apiCall.channelIds.filter((id) => takeTheirs.has(id));
      if (surrendered.length > 0) {
        removed.push({
          id: operation.id,
          description: operation.description,
          detail:
            `This renumbering was dropped: its numbers run in sequence, so keeping the ` +
            `server's number for ${surrendered.length} channel${surrendered.length !== 1 ? 's' : ''} ` +
            'would move every channel after it to a number you were not shown.',
        });
        continue;
      }
      const agreed = apiCall.channelIds
        .map((id) => keepMine.get(id))
        .filter((decision): decision is ReconcileDecision => decision !== undefined);
      if (agreed.length > 0) {
        next.push({
          ...operation,
          apiCall: {
            ...apiCall,
            acknowledgedConcurrentChanges: mergeAcknowledgements(
              apiCall.acknowledgedConcurrentChanges,
              agreed,
            ),
          },
        });
        continue;
      }
      next.push(operation);
      continue;
    }

    if (apiCall.type !== 'updateChannel' || apiCall.data.channel_number === undefined) {
      next.push(operation);
      continue;
    }

    if (takeTheirs.has(apiCall.channelId)) {
      const { channel_number: _dropped, ...rest } = apiCall.data;
      if (Object.keys(rest).length === 0) {
        removed.push({
          id: operation.id,
          description: operation.description,
          detail: "This change only set the channel number, so keeping the server's value drops it.",
        });
        continue;
      }
      next.push({ ...operation, apiCall: { ...apiCall, data: rest } });
      continue;
    }

    const decision = keepMine.get(apiCall.channelId);
    if (decision === undefined) {
      next.push(operation);
      continue;
    }
    next.push({
      ...operation,
      apiCall: {
        ...apiCall,
        acknowledgedConcurrentChanges: mergeAcknowledgements(
          apiCall.acknowledgedConcurrentChanges,
          [decision],
        ),
      },
    });
  }

  return { operations: next, removed };
}

/** One acknowledgement per channel: a later decision replaces an earlier one. */
function mergeAcknowledgements(
  existing: readonly ConcurrentNumberAcknowledgement[] | undefined,
  decisions: readonly ReconcileDecision[],
): ConcurrentNumberAcknowledgement[] {
  const byChannel = new Map<number, ConcurrentNumberAcknowledgement>();
  for (const acknowledgement of existing ?? []) {
    byChannel.set(acknowledgement.channelId, acknowledgement);
  }
  for (const decision of decisions) {
    byChannel.set(decision.channelId, {
      channelId: decision.channelId,
      baselineNumber: decision.baselineNumber,
      serverNumber: decision.serverNumber,
    });
  }
  return [...byChannel.values()];
}

/**
 * The number a staged operation expects the channel to be on when Apply runs.
 *
 * Sent to the backend so the check holds for the WRITE and not only for the
 * browser's read of it: the browser reads the lineup at one moment and the
 * executor writes at a later one, and this is what closes that window as far as
 * it can be closed. It is still a check and not a guarantee — there is no
 * conditional update to hang one on.
 *
 * After a `keep-mine` decision the expectation is the SERVER's value, because
 * that is what the operator was shown and agreed to overwrite. Without a
 * decision it is the baseline, which is what the session was staged against.
 */
export function expectedServerNumber(
  operation: StagedOperation,
  channelId: number,
  baselineNumber: number | null,
): number | null {
  for (const acknowledgement of concurrentAcknowledgementsOf(operation)) {
    if (acknowledgement.channelId === channelId) return acknowledgement.serverNumber;
  }
  return baselineNumber;
}
