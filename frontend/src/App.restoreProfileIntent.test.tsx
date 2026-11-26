/**
 * A staged create's profile choice must survive the tab losing its session
 * before Apply. The Edit Mode ledger already survives in sessionStorage; the
 * profile intent has to travel with it, restore with it, and still be bound to
 * the operator and Dispatcharr destination it was chosen against.
 *
 * "Reload" here is an unmount followed by a fresh mount over the same
 * sessionStorage, which is everything a reload preserves for this app.
 */
import type { ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChannelManagerTabProps } from './components/tabs/ChannelManagerTab';
import type { BulkCommitRequest, BulkCommitResponse } from './services/api';
import type { Stream } from './types';
import * as api from './services/api';
import { STAGED_LEDGER_STORAGE_KEY, type PersistedStagedLedger } from './utils/stagedLedgerStorage';
import recordedProfiles from '../../backend/tests/fixtures/y3m6o1/dispatcharr_channel_profiles_mixed_membership.json';

const testState = vi.hoisted(() => ({
  bulkCommit: vi.fn(),
  props: null as ChannelManagerTabProps | null,
}));
const recoveryBinding = 'f'.repeat(64);
const DESTINATION_A = 'a'.repeat(64);
const DESTINATION_B = 'b'.repeat(64);
const CREATED_ID = 50_000;
const INTENT_KEY_PREFIX = 'ecm.created-channel-profiles.v1:';

const STREAM: Stream = {
  id: 10_001, name: 'Fixture Stream', url: 'http://stream/1', m3u_account: 1,
  logo_url: null, tvg_id: null, channel_group: null, channel_group_name: 'Fixture Group',
  is_custom: false,
} as Stream;

vi.mock('./hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 7, auth_provider: 'local', username: 'operator', is_admin: true },
    logout: vi.fn(),
  }),
  useAdminNavVisible: () => true,
}));

vi.mock('./components/tabs/ChannelManagerTab', () => ({
  ChannelManagerTab: (props: ChannelManagerTabProps) => {
    testState.props = props;
    return <div data-testid="channel-manager-harness" />;
  },
}));

vi.mock('./components/NotificationCenter', () => ({
  NotificationCenter: () => null,
}));

vi.mock('./components', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./components')>();
  return {
    ...actual,
    SettingsModal: () => null,
    EditModeRestoredBadge: () => null,
    TabNavigation: () => null,
    UserMenu: () => null,
    PageHeader: ({ actions }: { actions?: ReactNode }) => <div>{actions}</div>,
    EditModeExitDialog: (props: Record<string, unknown>) => (props.isOpen
      ? (
        <div>
          <button onClick={() => void (props.onApply as () => Promise<void>)()}>Apply</button>
          <button onClick={() => (props.onDiscard as () => void)()}>Discard</button>
        </div>
      )
      : null),
  };
});

vi.mock('./services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./services/api')>();
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({ status: 'healthy', service: 'ECM' }),
    getSettings: vi.fn(),
    getChannelGroups: vi.fn().mockResolvedValue([]),
    getChannels: vi.fn().mockResolvedValue({ results: [], count: 0, next: null }),
    getProviderGroupSettings: vi.fn().mockResolvedValue([]),
    getM3UAccounts: vi.fn().mockResolvedValue([]),
    getStreamGroups: vi.fn().mockResolvedValue([]),
    getStreams: vi.fn().mockResolvedValue({ results: [], count: 0, next: null }),
    getAllLogos: vi.fn().mockResolvedValue([]),
    getStreamProfiles: vi.fn().mockResolvedValue([]),
    getChannelProfiles: vi.fn(),
    getProfileConflictReviews: vi.fn().mockResolvedValue({ reviews: [], total: 0 }),
    getChannelProfile: vi.fn(),
    updateProfileChannel: vi.fn(),
    bulkUpdateProfileChannels: vi.fn(),
    clearStreamStats: vi.fn().mockResolvedValue({ cleared: 1, stream_ids: [10_001] }),
    getEPGSources: vi.fn().mockResolvedValue([]),
    getEPGData: vi.fn().mockResolvedValue([]),
    getM3UStreamMetadata: vi.fn().mockResolvedValue({ count: 0, metadata: {} }),
    bulkCommit: (request: BulkCommitRequest, batchId?: string) => testState.bulkCommit(request, batchId),
  };
});

import App from './App';
import { ResolvedCreateChannelNames } from './services/api';

const fixture = (id: number) => {
  const profile = recordedProfiles.profiles.find(p => p.id === id)!;
  return { id: profile.id, name: profile.name, channels: [] as number[] };
};
let profiles = [fixture(6), fixture(9)];
let destination = DESTINATION_A;

function settings(dest: string) {
  return {
    configured: true, default_channel_profile_ids: [6], profile_recovery_destination: dest,
    default_normalize_on_create: false,
    stream_sort_priority: [], stream_sort_enabled: {}, m3u_account_priorities: {},
  } as unknown as Awaited<ReturnType<typeof api.getSettings>>;
}

function savedIntents(): Array<{ channelId: number; profileIds: number[] }> {
  const keys = Object.keys(sessionStorage).filter(key => key.startsWith(INTENT_KEY_PREFIX));
  return keys.flatMap(key => JSON.parse(sessionStorage.getItem(key)!));
}

function ledger(): PersistedStagedLedger | null {
  const raw = sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY);
  return raw === null ? null : JSON.parse(raw) as PersistedStagedLedger;
}

async function mountAndEnterEditMode() {
  const view = render(<App />);
  const editButton = await screen.findByRole('button', { name: /edit mode/i });
  await waitFor(() => expect(editButton).toBeEnabled());
  fireEvent.click(editButton);
  await screen.findByTitle('Apply changes');
  return view;
}

async function stageManual(name: string, profileIds: number[] | undefined) {
  await act(async () => {
    await testState.props!.onCreateChannelManual!(name, undefined, undefined, undefined, false, profileIds);
  });
}

async function stageBulk(profileIds: number[] | undefined) {
  await act(async () => {
    await testState.props!.onBulkCreateFromGroup(
      [STREAM], 100, null, undefined, undefined, undefined, false, undefined,
      false, undefined, undefined, undefined, undefined, undefined, undefined,
      profileIds, false, new ResolvedCreateChannelNames(new Map([[STREAM.name, STREAM.name]]), false),
    );
  });
}

/** Unmount and mount again over the same sessionStorage; returns the restore dialog. */
async function reload(view: ReturnType<typeof render>) {
  view.unmount();
  testState.props = null;
  const next = render(<App />);
  const dialog = await screen.findByTestId('edit-mode-restore-dialog');
  await waitFor(() => expect(screen.getByRole('button', { name: /^Restore/ })).toBeEnabled());
  return { view: next, dialog };
}

async function restore() {
  fireEvent.click(screen.getByRole('button', { name: /^Restore/ }));
  await screen.findByTitle('Apply changes');
}

async function apply() {
  fireEvent.click(screen.getByTitle('Apply changes'));
  fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));
  await waitFor(() => expect(testState.bulkCommit).toHaveBeenCalled());
}

/** The membership writes a settled Apply makes, after every profile read. */
async function settledMembershipWrites() {
  await waitFor(() => expect(vi.mocked(api.getChannelProfiles).mock.calls
    .filter(call => call[0] === recoveryBinding).length).toBeGreaterThanOrEqual(2));
  await waitFor(() => expect(screen.queryByTitle('Apply changes')).not.toBeInTheDocument());
  return vi.mocked(api.bulkUpdateProfileChannels).mock.calls;
}

function membership(): Record<number, boolean> {
  return Object.fromEntries(profiles.map(profile => [profile.id, profile.channels.includes(CREATED_ID)]));
}

describe('a restored Edit Mode session keeps its profile choices', () => {
  beforeEach(() => {
    sessionStorage.clear();
    profiles = [fixture(6), fixture(9)];
    destination = DESTINATION_A;
    vi.stubGlobal('alert', vi.fn());
    window.history.replaceState({}, '', '#channel-manager');
    vi.mocked(api.getSettings).mockReset().mockImplementation(async () => settings(destination));
    vi.mocked(api.getChannelProfiles).mockReset().mockImplementation(async () =>
      profiles.map(profile => ({ ...profile, channels: [...profile.channels] })));
    vi.mocked(api.getChannelProfile).mockReset().mockImplementation(async (id: number) => {
      const profile = profiles.find(p => p.id === id)!;
      return { ...profile, channels: [...profile.channels] };
    });
    vi.mocked(api.updateProfileChannel).mockReset().mockRejectedValue(new Error('unexpected single write'));
    vi.mocked(api.bulkUpdateProfileChannels).mockReset().mockImplementation(async (id, ids, enabled) => {
      const profile = profiles.find(p => p.id === id)!;
      profile.channels = enabled
        ? [...new Set([...profile.channels, ...ids])]
        : profile.channels.filter(channelId => !ids.includes(channelId));
      return { success: true };
    });
    testState.bulkCommit.mockReset().mockImplementation(async (request: BulkCommitRequest) => {
      const creates = request.operations.filter(op => op.type === 'createChannel');
      // Dispatcharr may enable a new channel everywhere on its own.
      for (const profile of profiles) profile.channels = [CREATED_ID];
      return {
        success: true, operationsApplied: request.operations.length, operationsFailed: 0, errors: [],
        tempIdMap: Object.fromEntries(creates.map(op => [Number(op.tempId), CREATED_ID])),
        profileRecoveryBinding: recoveryBinding, groupIdMap: {},
      } satisfies BulkCommitResponse;
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it.each([
    ['manual', [9], [[6, [CREATED_ID], false, recoveryBinding]], { 6: false, 9: true }],
    ['manual', [], [[6, [CREATED_ID], false, recoveryBinding], [9, [CREATED_ID], false, recoveryBinding]], { 6: false, 9: false }],
    ['manual', undefined, [[9, [CREATED_ID], false, recoveryBinding]], { 6: true, 9: false }],
    ['bulk', [9], [[6, [CREATED_ID], false, recoveryBinding]], { 6: false, 9: true }],
    ['bulk', [], [[6, [CREATED_ID], false, recoveryBinding], [9, [CREATED_ID], false, recoveryBinding]], { 6: false, 9: false }],
    ['bulk', undefined, [[9, [CREATED_ID], false, recoveryBinding]], { 6: true, 9: false }],
  ] as Array<['manual' | 'bulk', number[] | undefined, unknown[][], Record<number, boolean>]>)(
    '%s create with profileIds %j survives reload, restore and Apply',
    async (dialog, profileIds, writes, final) => {
      const first = await mountAndEnterEditMode();
      if (dialog === 'manual') await stageManual('Restored channel', profileIds);
      else await stageBulk(profileIds);

      await reload(first);
      await restore();
      await apply();

      const request = testState.bulkCommit.mock.calls[0][0] as BulkCommitRequest;
      expect(request.operations.filter(op => op.type === 'createChannel')).toHaveLength(1);
      expect(request).toMatchObject({
        profileRecoveryOperator: 'local#7', profileRecoveryDestination: DESTINATION_A,
      });
      for (const [sent] of testState.bulkCommit.mock.calls as Array<[BulkCommitRequest]>) {
        expect(JSON.stringify(sent)).not.toContain('profileIntent');
        const operations = JSON.stringify(sent.operations);
        expect(operations).not.toContain('local#7');
        expect(operations).not.toContain(DESTINATION_A);
      }
      expect(await settledMembershipWrites()).toEqual(writes);
      expect(membership()).toEqual(final);
      expect(savedIntents()).toEqual([]);
      expect(testState.bulkCommit).toHaveBeenCalledTimes(1);
    },
  );

  it('restoring makes no upstream call of any kind', async () => {
    const first = await mountAndEnterEditMode();
    await stageManual('Restored channel', [9]);
    await reload(first);
    // Let the remount's own loads settle before measuring.
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });

    const mocks = Object.entries(api).filter(([, fn]) => vi.isMockFunction(fn)) as Array<[string, ReturnType<typeof vi.fn>]>;
    expect(mocks.length).toBeGreaterThan(10);
    const before = new Map(mocks.map(([name, fn]) => [name, fn.mock.calls.length]));
    const commitsBefore = testState.bulkCommit.mock.calls.length;
    const fetchSpy = vi.spyOn(globalThis, 'fetch');

    await restore();
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });

    const called = mocks.filter(([name, fn]) => fn.mock.calls.length !== before.get(name)).map(([name]) => name);
    expect(called).toEqual([]);
    expect(testState.bulkCommit.mock.calls.length).toBe(commitsBefore);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('a different Dispatcharr destination withdraws the intent instead of applying it', async () => {
    const first = await mountAndEnterEditMode();
    await stageManual('Restored channel', [9]);

    destination = DESTINATION_B;
    const { dialog } = await reload(first);
    expect(dialog).toHaveTextContent('Restored channel');
    expect(screen.getByTestId('edit-mode-restore-withdrawn-profiles')).toHaveTextContent('Restored channel');
    await restore();
    await apply();

    const request = testState.bulkCommit.mock.calls[0][0] as BulkCommitRequest;
    expect(request.operations.filter(op => op.type === 'createChannel')).toHaveLength(1);
    expect(request.profileRecoveryDestination).toBeUndefined();
    await waitFor(() => expect(screen.queryByTitle('Apply changes')).not.toBeInTheDocument());
    expect(api.bulkUpdateProfileChannels).not.toHaveBeenCalled();
    expect(savedIntents()).toEqual([]);
  });

  it('an old-shape ledger with no intent restores the create without one', async () => {
    const first = await mountAndEnterEditMode();
    await stageManual('Restored channel', [9]);
    const stored = ledger()!;
    for (const op of stored.operations) {
      delete (op.apiCall as { profileIntent?: unknown }).profileIntent;
    }
    sessionStorage.setItem(STAGED_LEDGER_STORAGE_KEY, JSON.stringify(stored));

    await reload(first);
    expect(screen.queryByTestId('edit-mode-restore-withdrawn-profiles')).not.toBeInTheDocument();
    await restore();
    await apply();

    const request = testState.bulkCommit.mock.calls[0][0] as BulkCommitRequest;
    expect(request.operations.filter(op => op.type === 'createChannel')).toHaveLength(1);
    await waitFor(() => expect(screen.queryByTitle('Apply changes')).not.toBeInTheDocument());
    expect(api.bulkUpdateProfileChannels).not.toHaveBeenCalled();
    expect(savedIntents()).toEqual([]);
  });

  it('Undo of a staged create drops its intent from the saved session', async () => {
    const first = await mountAndEnterEditMode();
    await stageManual('Kept channel', [9]);
    await stageManual('Undone channel', []);
    act(() => testState.props!.onUndo());

    const intents = ledger()!.operations
      .filter(op => op.apiCall.type === 'createChannel')
      .map(op => [op.apiCall.type === 'createChannel' && op.apiCall.name,
        (op.apiCall as { profileIntent?: { profileIds: number[] } }).profileIntent?.profileIds]);
    expect(intents).toEqual([['Kept channel', [9]]]);

    await reload(first);
    await restore();
    await apply();
    expect(await settledMembershipWrites()).toEqual([[6, [CREATED_ID], false, recoveryBinding]]);
    expect(membership()).toEqual({ 6: false, 9: true });
  });

  it('Discard of a staged create drops its intent', async () => {
    await mountAndEnterEditMode();
    await stageManual('Discarded channel', [9]);
    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(ledger()).toBeNull());

    const editButton = await screen.findByRole('button', { name: /edit mode/i });
    fireEvent.click(editButton);
    await screen.findByTitle('Apply changes');
    await stageManual('Fresh channel', undefined);
    await apply();
    // Only the fresh create's defaults; nothing of the discarded [9].
    expect(await settledMembershipWrites()).toEqual([[9, [CREATED_ID], false, recoveryBinding]]);
    expect(membership()).toEqual({ 6: true, 9: false });
  });

  it('an unrelated staged operation and its Undo leave the intent alone', async () => {
    const first = await mountAndEnterEditMode();
    await stageManual('Restored channel', [9]);
    act(() => testState.props!.onStageClearStreamStats([STREAM.id], 'Clear stream stats'));
    act(() => testState.props!.onUndo());
    act(() => testState.props!.onStageClearStreamStats([STREAM.id], 'Clear stream stats'));

    await reload(first);
    await restore();
    await apply();
    expect(await settledMembershipWrites()).toEqual([[6, [CREATED_ID], false, recoveryBinding]]);
    expect(membership()).toEqual({ 6: false, 9: true });
  });
});
