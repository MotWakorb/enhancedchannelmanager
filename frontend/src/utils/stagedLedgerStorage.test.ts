/**
 * Tests for the Edit Mode staged-ledger survival store (bead
 * enhancedchannelmanager-r93hq, session-expiry follow-up).
 *
 * WHY THIS FILE EXISTS AT ALL. Every other exit from Edit Mode now asks first:
 * in-page navigation, browser Back/Forward, and Sign Out all run through the
 * exit guard. One exit cannot be guarded, because the app is not the thing
 * leaving — the SESSION is. A token refresh fails, or /me answers 401, and
 * `useAuth` clears the user; `ProtectedRoute` swaps the whole app for
 * `<LoginPage />`, and the staged ledger, which lives only in `useEditMode`'s
 * React state, goes with it. Apply is not an option at that moment: the session
 * is already dead, so every commit call would 401.
 *
 * THE TWO TESTS THAT MATTER MOST are the identity discard and the staleness
 * rejection, and they matter for the same reason: a persistence feature that
 * hands one operator's staged channel edits to whoever logs in next, or that
 * applies them against ids that moved while the session was dead, is strictly
 * worse than losing the work. Both are proven here against the exact dangerous
 * mutant — a ledger stamped with a different operator, and a ledger whose
 * referenced channel/group/stream ids no longer resolve.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import type { Channel, ChannelGroup, StagedOperation, ApiCallSpec } from '../types';
import {
  STAGED_LEDGER_STORAGE_KEY,
  STAGED_LEDGER_FORMAT_VERSION,
  STAGED_LEDGER_MAX_AGE_MS,
  operatorLedgerKey,
  saveStagedLedger,
  readStagedLedger,
  clearStagedLedger,
  planLedgerRestore,
} from './stagedLedgerStorage';

// ------------------------------------------------------------------ fixtures

const OPERATOR_A = 'local#7';
const OPERATOR_B = 'local#8';

function channel(id: number, overrides: Partial<Channel> = {}): Channel {
  return {
    id,
    channel_number: 100 + id,
    name: `Channel ${id}`,
    channel_group_id: 1,
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
    ...overrides,
  };
}

const GROUPS: ChannelGroup[] = [
  { id: 1, name: 'Entertainment', channel_count: 2 },
  { id: 2, name: 'Sports', channel_count: 0 },
];

let opCounter = 0;

/** A staged operation shaped exactly as `stageOperation` builds them. */
function op(apiCall: ApiCallSpec, description = 'staged change'): StagedOperation {
  opCounter += 1;
  return {
    id: `op-${opCounter}`,
    timestamp: 1_700_000_000_000 + opCounter,
    description,
    apiCall,
    beforeSnapshot: [],
    afterSnapshot: [],
  };
}

/** A `createChannel` operation carrying its temp id where the hook puts it. */
function createChannelOp(tempId: number, apiCall: Extract<ApiCallSpec, { type: 'createChannel' }>): StagedOperation {
  const operation = op(apiCall, `Create channel "${apiCall.name}"`);
  operation.afterSnapshot = [{
    id: tempId,
    channel_number: apiCall.channelNumber ?? null,
    name: apiCall.name,
    channel_group_id: apiCall.stagedGroupId ?? apiCall.groupId ?? null,
    streams: [],
  }];
  return operation;
}

const NOW = 1_760_000_000_000;

beforeEach(() => {
  opCounter = 0;
  window.sessionStorage.clear();
});

// ------------------------------------------------------- identity + lifecycle

describe('operatorLedgerKey', () => {
  it('keys on the identity the auth layer already exposes, provider-qualified', () => {
    expect(operatorLedgerKey({ id: 7, auth_provider: 'local' })).toBe('local#7');
    // Same row id, different provider — never the same operator.
    expect(operatorLedgerKey({ id: 7, auth_provider: 'dispatcharr' })).not.toBe(
      operatorLedgerKey({ id: 7, auth_provider: 'local' }),
    );
  });

  it('gives an instance with no identity its own key rather than borrowing one', () => {
    expect(operatorLedgerKey(null)).toBe('anonymous');
    expect(operatorLedgerKey(null)).not.toBe(operatorLedgerKey({ id: 1, auth_provider: 'local' }));
  });
});

describe('saveStagedLedger / readStagedLedger', () => {
  it('round-trips a ledger for the operator that staged it', () => {
    const operations = [op({ type: 'deleteChannel', channelId: 5 })];
    saveStagedLedger({ operatorKey: OPERATOR_A, operations, undoGroups: [['op-1']], now: NOW });

    const restored = readStagedLedger(OPERATOR_A, NOW + 1000);
    expect(restored).not.toBeNull();
    expect(restored!.operations).toHaveLength(1);
    expect(restored!.operations[0].apiCall).toEqual({ type: 'deleteChannel', channelId: 5 });
    expect(restored!.undoGroups).toEqual([['op-1']]);
    expect(restored!.savedAt).toBe(NOW);
  });

  it('writes to sessionStorage, never localStorage, so the ledger dies with the tab', () => {
    saveStagedLedger({
      operatorKey: OPERATOR_A,
      operations: [op({ type: 'deleteChannel', channelId: 5 })],
      undoGroups: [['op-1']],
      now: NOW,
    });
    expect(window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).not.toBeNull();
    expect(window.localStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull();
  });

  it('stores nothing at all when there is nothing staged', () => {
    saveStagedLedger({ operatorKey: OPERATOR_A, operations: [], undoGroups: [], now: NOW });
    expect(window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull();
  });

  it('clearStagedLedger removes the entry', () => {
    saveStagedLedger({
      operatorKey: OPERATOR_A,
      operations: [op({ type: 'deleteChannel', channelId: 5 })],
      undoGroups: [['op-1']],
      now: NOW,
    });
    clearStagedLedger();
    expect(window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull();
  });

  it('returns null when no ledger was ever written', () => {
    expect(readStagedLedger(OPERATOR_A, NOW)).toBeNull();
  });
});

// ============================================ THE IDENTITY GUARD (requirement 1)

describe('a restored ledger is bound to the identity that created it', () => {
  it('refuses a ledger staged by a different operator, and destroys it', () => {
    saveStagedLedger({
      operatorKey: OPERATOR_A,
      operations: [op({ type: 'deleteChannel', channelId: 5 }, 'Delete "Channel 5"')],
      undoGroups: [['op-1']],
      now: NOW,
    });

    // Operator B signs in on the same workstation, same tab.
    expect(readStagedLedger(OPERATOR_B, NOW + 1000)).toBeNull();

    // Not merely withheld — GONE. Leaving A's staged channel edits sitting in
    // B's tab is the risk; B would Apply them under their own credentials and
    // the journal would attribute every change to B.
    expect(window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull();

    // And it stays gone even if A comes back in the same tab.
    expect(readStagedLedger(OPERATOR_A, NOW + 2000)).toBeNull();
  });

  it('refuses an anonymous session a signed-in operator\'s ledger', () => {
    saveStagedLedger({
      operatorKey: OPERATOR_A,
      operations: [op({ type: 'deleteChannel', channelId: 5 })],
      undoGroups: [['op-1']],
      now: NOW,
    });
    expect(readStagedLedger(operatorLedgerKey(null), NOW + 1000)).toBeNull();
  });

  it('refuses a ledger with no operator stamp at all', () => {
    window.sessionStorage.setItem(STAGED_LEDGER_STORAGE_KEY, JSON.stringify({
      version: STAGED_LEDGER_FORMAT_VERSION,
      savedAt: NOW,
      operations: [op({ type: 'deleteChannel', channelId: 5 })],
      undoGroups: [['op-1']],
    }));
    expect(readStagedLedger(OPERATOR_A, NOW + 1000)).toBeNull();
    expect(window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull();
  });
});

// ================================================ AGE BOUND AND FORMAT GUARDS

describe('a persisted ledger expires', () => {
  it('accepts a ledger inside the age bound', () => {
    saveStagedLedger({
      operatorKey: OPERATOR_A,
      operations: [op({ type: 'deleteChannel', channelId: 5 })],
      undoGroups: [['op-1']],
      now: NOW,
    });
    expect(readStagedLedger(OPERATOR_A, NOW + STAGED_LEDGER_MAX_AGE_MS - 1)).not.toBeNull();
  });

  it('refuses and destroys a ledger past the age bound', () => {
    saveStagedLedger({
      operatorKey: OPERATOR_A,
      operations: [op({ type: 'deleteChannel', channelId: 5 })],
      undoGroups: [['op-1']],
      now: NOW,
    });
    expect(readStagedLedger(OPERATOR_A, NOW + STAGED_LEDGER_MAX_AGE_MS + 1)).toBeNull();
    expect(window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull();
  });

  it('refuses a ledger written by a different format version', () => {
    window.sessionStorage.setItem(STAGED_LEDGER_STORAGE_KEY, JSON.stringify({
      version: STAGED_LEDGER_FORMAT_VERSION + 1,
      operatorKey: OPERATOR_A,
      savedAt: NOW,
      operations: [op({ type: 'deleteChannel', channelId: 5 })],
      undoGroups: [['op-1']],
    }));
    expect(readStagedLedger(OPERATOR_A, NOW)).toBeNull();
    expect(window.sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull();
  });

  it('refuses unparseable or wrongly-shaped contents instead of throwing', () => {
    window.sessionStorage.setItem(STAGED_LEDGER_STORAGE_KEY, 'not json at all');
    expect(readStagedLedger(OPERATOR_A, NOW)).toBeNull();

    window.sessionStorage.setItem(STAGED_LEDGER_STORAGE_KEY, JSON.stringify({
      version: STAGED_LEDGER_FORMAT_VERSION,
      operatorKey: OPERATOR_A,
      savedAt: NOW,
      operations: 'nope',
      undoGroups: [],
    }));
    expect(readStagedLedger(OPERATOR_A, NOW)).toBeNull();

    window.sessionStorage.setItem(STAGED_LEDGER_STORAGE_KEY, JSON.stringify({
      version: STAGED_LEDGER_FORMAT_VERSION,
      operatorKey: OPERATOR_A,
      savedAt: NOW,
      operations: [{ id: 'x' }],
      undoGroups: [],
    }));
    expect(readStagedLedger(OPERATOR_A, NOW)).toBeNull();
  });
});

// =========================================== THE STALENESS GUARD (requirement 3)

describe('planLedgerRestore rejects operations whose referenced ids have moved', () => {
  const context = () => ({
    channels: [channel(5, { streams: [11, 12] }), channel(6)],
    channelGroups: GROUPS,
  });

  it('keeps operations whose every reference still resolves', () => {
    const plan = planLedgerRestore(
      [
        op({ type: 'updateChannel', channelId: 5, data: { name: 'Renamed' } }),
        op({ type: 'renameChannelGroup', groupId: 2, newName: 'Sports HD' }),
      ],
      context(),
    );
    expect(plan.dropped).toEqual([]);
    expect(plan.restorable).toHaveLength(2);
  });

  it('drops an operation whose channel was deleted while the session was dead', () => {
    const stale = op({ type: 'updateChannel', channelId: 404, data: { channel_number: 9 } }, 'Renumber "Gone HD"');
    stale.beforeSnapshot = [{ id: 404, channel_number: 9, name: 'Gone HD', channel_group_id: 1, streams: [] }];

    const plan = planLedgerRestore([stale], context());

    expect(plan.restorable).toEqual([]);
    expect(plan.dropped).toHaveLength(1);
    expect(plan.dropped[0].reason).toBe('channel-missing');
    // The account has to name the thing, or it is not an account.
    expect(plan.dropped[0].detail).toContain('Gone HD');
    expect(plan.dropped[0].description).toBe('Renumber "Gone HD"');
  });

  it('drops an operation whose channel group was deleted while the session was dead', () => {
    const plan = planLedgerRestore(
      [op({ type: 'renameChannelGroup', groupId: 909, newName: 'Whatever' }, 'Rename group')],
      context(),
    );
    expect(plan.restorable).toEqual([]);
    expect(plan.dropped[0].reason).toBe('group-missing');
    expect(plan.dropped[0].detail).toContain('909');
  });

  it('drops a move into a channel group that no longer exists', () => {
    const plan = planLedgerRestore(
      [op({ type: 'updateChannel', channelId: 5, data: { channel_group_id: 909 } }, 'Move to group')],
      context(),
    );
    expect(plan.restorable).toEqual([]);
    expect(plan.dropped[0].reason).toBe('group-missing');
  });

  it('drops a stream removal for a stream the channel no longer carries', () => {
    const plan = planLedgerRestore(
      [op({ type: 'removeStreamFromChannel', channelId: 5, streamId: 99 }, 'Remove stream 99')],
      context(),
    );
    expect(plan.restorable).toEqual([]);
    expect(plan.dropped[0].reason).toBe('stream-detached');
  });

  it('keeps a stream removal for a stream an EARLIER staged operation adds', () => {
    // The projection has to follow the ledger, not just the server: staging
    // add-then-remove in one session is legal and both operations survive.
    const add = op({ type: 'addStreamToChannel', channelId: 5, streamId: 77 });
    const remove = op({ type: 'removeStreamFromChannel', channelId: 5, streamId: 77 });
    const plan = planLedgerRestore([add, remove], context());
    expect(plan.dropped).toEqual([]);
    expect(plan.restorable).toHaveLength(2);
  });

  it('drops a stream reorder that no longer describes the channel\'s streams', () => {
    // Another operator attached a stream while the session was dead. Applying
    // the reorder would silently detach it, because a reorder REPLACES the list.
    const plan = planLedgerRestore(
      [op({ type: 'reorderChannelStreams', channelId: 5, streamIds: [12, 11] })],
      { channels: [channel(5, { streams: [11, 12, 13] })], channelGroups: GROUPS },
    );
    expect(plan.restorable).toEqual([]);
    expect(plan.dropped[0].reason).toBe('stream-detached');
  });

  it('drops a bulk renumber wholesale when any one of its channels is gone', () => {
    // Partial is not an option: the numbers are assigned by POSITION in the
    // list, so dropping one channel renumbers every channel after it.
    const plan = planLedgerRestore(
      [op({ type: 'bulkAssignChannelNumbers', channelIds: [5, 6, 404], startingNumber: 10 })],
      context(),
    );
    expect(plan.restorable).toEqual([]);
    expect(plan.dropped[0].reason).toBe('channel-missing');
  });

  it('drops a profile membership change for a profile that no longer exists', () => {
    const plan = planLedgerRestore(
      [op({ type: 'setProfileMembership', profileId: 42, channelId: 5, enabled: true })],
      { ...context(), profileIds: [1, 2] },
    );
    expect(plan.restorable).toEqual([]);
    expect(plan.dropped[0].reason).toBe('profile-missing');
  });
});

describe('planLedgerRestore keeps this session\'s own temp references consistent', () => {
  const context = () => ({ channels: [channel(5)], channelGroups: GROUPS });

  it('keeps a channel staged into a group staged in the same ledger', () => {
    const group = op({ type: 'createGroup', name: 'Drill Locals', tempGroupId: -1000 });
    const create = createChannelOp(-1, {
      type: 'createChannel', name: 'Local 1', newGroupName: 'Drill Locals', stagedGroupId: -1000,
    });
    const addStream = op({ type: 'addStreamToChannel', channelId: -1, streamId: 31 });

    const plan = planLedgerRestore([group, create, addStream], context());
    expect(plan.dropped).toEqual([]);
    expect(plan.restorable).toHaveLength(3);
  });

  it('drops everything that depends on a dropped create', () => {
    // The create targets a real group that has since been deleted, so it goes.
    // Its temp channel id then refers to nothing, and the operations pointing
    // at it must go too — applying them against id -1 is meaningless.
    const create = createChannelOp(-1, { type: 'createChannel', name: 'Orphan', groupId: 909 });
    const addStream = op({ type: 'addStreamToChannel', channelId: -1, streamId: 31 }, 'Assign stream to "Orphan"');
    const rename = op({ type: 'updateChannel', channelId: -1, data: { name: 'Orphan 2' } });

    const plan = planLedgerRestore([create, addStream, rename], context());

    expect(plan.restorable).toEqual([]);
    expect(plan.dropped.map((d) => d.reason)).toEqual([
      'group-missing', 'depends-on-dropped', 'depends-on-dropped',
    ]);
  });

  it('drops a reference to a temp group id no surviving operation creates', () => {
    const plan = planLedgerRestore(
      [op({ type: 'updateChannel', channelId: 5, data: { channel_group_id: -4242 } })],
      context(),
    );
    expect(plan.restorable).toEqual([]);
    expect(plan.dropped[0].reason).toBe('depends-on-dropped');
  });

  it('does not judge a hidden-group restore against the visible group list', () => {
    // A hidden group is by definition absent from `channelGroups`; validating
    // it there would drop every restore. Named explicitly so the gap is a
    // decision rather than an accident.
    const plan = planLedgerRestore(
      [op({ type: 'restoreChannelGroup', groupId: 77 })],
      context(),
    );
    expect(plan.dropped).toEqual([]);
    expect(plan.restorable).toHaveLength(1);
  });

  it('reports a partial restore as both halves, never as a silent subset', () => {
    const good = op({ type: 'updateChannel', channelId: 5, data: { name: 'Kept' } });
    const bad = op({ type: 'updateChannel', channelId: 404, data: { name: 'Lost' } }, 'Rename "Lost"');
    const plan = planLedgerRestore([good, bad], context());
    expect(plan.restorable).toEqual([good]);
    expect(plan.dropped).toHaveLength(1);
    expect(plan.dropped[0].description).toBe('Rename "Lost"');
  });
});

// --------------------------------------- an acknowledgement cannot outlive
// the collision it consented to (bead enhancedchannelmanager-vdxbx, round 2)

describe('planLedgerRestore withdraws an acknowledgement the lineup outgrew', () => {
  const lineup = (...channels: Channel[]) => ({
    channels,
    channelGroups: GROUPS,
  });

  /** Stage channel 6 onto channel 5's number, having confirmed that collision. */
  const stagedOntoFive = () =>
    op(
      {
        type: 'updateChannel',
        channelId: 6,
        data: { channel_number: 105 },
        acknowledgedDuplicate: { number: 105, occupantChannelIds: [5] },
      },
      'Changed channel number from 106 to 105',
    );

  it('keeps it when the channels on that number are the ones the operator was shown', () => {
    // The anti-vacuity control. An ordinary restore must not lose consent.
    const staged = stagedOntoFive();
    const plan = planLedgerRestore([staged], lineup(channel(5), channel(6)));
    expect(plan.withdrawnAcknowledgements).toEqual([]);
    expect(plan.restorable).toEqual([staged]);
  });

  it('withdraws it when a different channel now holds the number', () => {
    // 5 moved off 105 and 7 moved on while the session was dead. The operator
    // consented to joining channel 5, and was never shown channel 7.
    const staged = stagedOntoFive();
    const plan = planLedgerRestore(
      [staged],
      lineup(channel(5, { channel_number: 500 }), channel(6), channel(7, { channel_number: 105 })),
    );
    expect(plan.withdrawnAcknowledgements).toHaveLength(1);
    expect(plan.withdrawnAcknowledgements[0].id).toBe(staged.id);
    // The operation itself survives: the operator's EDIT is not the problem,
    // only the consent attached to it. It comes back for re-confirmation
    // rather than being thrown away.
    expect(plan.restorable).toHaveLength(1);
    expect(plan.dropped).toEqual([]);
    const restored = plan.restorable[0].apiCall as { acknowledgedDuplicate?: unknown };
    expect(restored.acknowledgedDuplicate).toBeUndefined();
  });

  it('does not mutate the persisted operation it withdraws from', () => {
    const staged = stagedOntoFive();
    planLedgerRestore(
      [staged],
      lineup(channel(5, { channel_number: 500 }), channel(6), channel(7, { channel_number: 105 })),
    );
    const original = staged.apiCall as { acknowledgedDuplicate?: unknown };
    expect(original.acknowledgedDuplicate).toEqual({ number: 105, occupantChannelIds: [5] });
  });

  it('keeps it when the collision merely got SMALLER while the session was dead', () => {
    // Channels 5 and 7 both held 105 and the operator confirmed joining both.
    // 7 moved off while they were away. `{5} ⊂ {5, 7}` is the harmless
    // direction — consent to a worse collision covers a lesser one — and it is
    // the direction BOTH final-state validators already accept
    // (`channelNumberPlan.ts` "SUBSET, NOT EQUALITY", `channel_number_plan.py`
    // likewise, `docs/api.md` §"Duplicate-number acknowledgements"). Under
    // equality the operator was re-interrogated here and then Apply accepted
    // the very thing restore had just refused.
    const staged = op(
      {
        type: 'updateChannel',
        channelId: 6,
        data: { channel_number: 105 },
        acknowledgedDuplicate: { number: 105, occupantChannelIds: [5, 7] },
      },
      'Changed channel number from 106 to 105',
    );
    const plan = planLedgerRestore(
      [staged],
      lineup(channel(5), channel(6), channel(7, { channel_number: 700 })),
    );
    expect(plan.withdrawnAcknowledgements).toEqual([]);
    expect(plan.restorable).toEqual([staged]);
  });

  it('keeps it when nobody holds the number any more', () => {
    // The empty set is a subset of every set, and both validators short-circuit
    // an empty slot to "consented" for the same reason: there is nothing left
    // to consent to. Withdrawing here told the operator "the number will be
    // checked again before anything is applied" about a check that then passed
    // silently, which is a re-ask dressed as a warning.
    const staged = stagedOntoFive();
    const plan = planLedgerRestore([staged], lineup(channel(5, { channel_number: 500 }), channel(6)));
    expect(plan.withdrawnAcknowledgements).toEqual([]);
    expect(plan.restorable).toHaveLength(1);
  });

  it('still withdraws when a STRANGER joins the ones the operator was shown', () => {
    // The dangerous direction, and the whole reason the occupant set is
    // carried at all: `{5, 8} ⊄ {5}`. Channel 8 arrived on 105 while the
    // session was dead and no dialog ever named it.
    const staged = stagedOntoFive();
    const plan = planLedgerRestore(
      [staged],
      lineup(channel(5), channel(6), channel(8, { channel_number: 105 })),
    );
    expect(plan.withdrawnAcknowledgements).toHaveLength(1);
    const restored = plan.restorable[0].apiCall as { acknowledgedDuplicate?: unknown };
    expect(restored.acknowledgedDuplicate).toBeUndefined();
  });

  it('measures the occupants against this ledger\'s own earlier operations, not only the server', () => {
    // Channel 5 is moved off 105 EARLIER IN THE SAME LEDGER. The staging-time
    // warning would not have named it, so a plan that only consulted the
    // server list would withdraw a consent that is still exactly right.
    const vacate = op({ type: 'updateChannel', channelId: 5, data: { channel_number: 500 } });
    const staged = op(
      {
        type: 'updateChannel',
        channelId: 6,
        data: { channel_number: 105 },
        acknowledgedDuplicate: { number: 105, occupantChannelIds: [] },
      },
      'Changed channel number from 106 to 105',
    );
    const plan = planLedgerRestore([vacate, staged], lineup(channel(5), channel(6)));
    expect(plan.withdrawnAcknowledgements).toEqual([]);
  });
});
