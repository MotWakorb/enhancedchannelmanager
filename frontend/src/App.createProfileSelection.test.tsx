/**
 * The create dialog's profile picker is a three-way answer: untouched means
 * the configured defaults, a cleared picker means no profiles at all, and any
 * other selection means exactly those profiles. The answer has to survive the
 * real dialog, App's staging, the commit, and the retained recovery intent.
 *
 * ChannelManagerTab is replaced by a harness that renders the REAL StreamsPane
 * wired to App's real props the way the tab wires it, so the arguments under
 * assertion are produced by the dialog and consumed by App's own callbacks.
 */
import { useState, type ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChannelManagerTabProps } from './components/tabs/ChannelManagerTab';
import type { BulkCommitRequest, BulkCommitResponse } from './services/api';
import type { Stream } from './types';
import * as api from './services/api';
import recordedProfiles from '../../backend/tests/fixtures/y3m6o1/dispatcharr_channel_profiles_mixed_membership.json';

const testState = vi.hoisted(() => ({
  bulkCommit: vi.fn(),
  manualCalls: [] as unknown[][],
  bulkCalls: [] as unknown[][],
  props: null as ChannelManagerTabProps | null,
}));
const recoveryBinding = 'f'.repeat(64);
const profileDestination = vi.hoisted(() => 'a'.repeat(64));
const CREATED_ID = 50_000;
const INTENT_KEY_PREFIX = 'ecm.created-channel-profiles.v1:';

const STREAM: Stream = {
  id: 10_001, name: 'Fixture Stream', url: 'http://stream/1', m3u_account: 1,
  logo_url: null, tvg_id: null, channel_group: null, channel_group_name: 'Fixture Group',
  is_custom: false,
} as Stream;
const STREAMS = [STREAM];
const STREAM_IDS = [STREAM.id];

vi.mock('./hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 7, auth_provider: 'local', username: 'operator', is_admin: true },
    logout: vi.fn(),
  }),
  useAdminNavVisible: () => true,
}));

vi.mock('./components/tabs/ChannelManagerTab', async () => {
  const { StreamsPane } = await import('./components/StreamsPane');
  return {
    ChannelManagerTab: (props: ChannelManagerTabProps) => {
      const [open, setOpen] = useState<'manual' | 'bulk' | null>(null);
      testState.props = props;
      return (
        <div>
          <button onClick={() => setOpen('manual')}>Open manual create</button>
          <button onClick={() => setOpen('bulk')}>Open bulk create</button>
          <StreamsPane
            streams={STREAMS}
            providers={[]}
            streamGroups={[]}
            searchTerm=""
            onSearchChange={vi.fn()}
            providerFilter={null}
            onProviderFilterChange={vi.fn()}
            groupFilter={null}
            onGroupFilterChange={vi.fn()}
            loading={false}
            channels={props.channels}
            channelGroups={props.channelGroups}
            isEditMode={props.isEditMode}
            channelProfiles={props.channelProfiles}
            channelDefaults={props.channelDefaults}
            externalTriggerManualEntry={open === 'manual'}
            externalTriggerStreamIds={open === 'bulk' ? STREAM_IDS : null}
            onExternalTriggerHandled={() => setOpen(null)}
            onCheckConflicts={props.onCheckConflicts}
            onCountPushDownShift={props.onCountPushDownShift}
            onGetHighestChannelNumber={props.onGetHighestChannelNumber}
            onBulkCreateFromGroup={(...args) => {
              testState.bulkCalls.push(args);
              return props.onBulkCreateFromGroup(...args);
            }}
            onCreateChannel={(...args) => {
              testState.manualCalls.push(args);
              return props.onCreateChannelManual!(...args);
            }}
          />
        </div>
      );
    },
  };
});

vi.mock('./components/NotificationCenter', () => ({
  NotificationCenter: () => null,
}));

vi.mock('./components', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./components')>();
  return {
    ...actual,
    SettingsModal: () => null,
    EditModeRestoreDialog: () => null,
    EditModeRestoredBadge: () => null,
    TabNavigation: () => null,
    UserMenu: () => null,
    PageHeader: ({ actions }: { actions?: ReactNode }) => <div>{actions}</div>,
    EditModeExitDialog: (props: Record<string, unknown>) => (props.isOpen
      ? <button onClick={() => void (props.onApply as () => Promise<void>)()}>Apply</button>
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
    getEPGSources: vi.fn().mockResolvedValue([]),
    getEPGData: vi.fn().mockResolvedValue([]),
    getM3UStreamMetadata: vi.fn().mockResolvedValue({ count: 0, metadata: {} }),
    bulkCommit: (request: BulkCommitRequest, batchId?: string) => testState.bulkCommit(request, batchId),
  };
});

import App from './App';
import { ResolvedCreateChannelNames } from './services/api';

// Two recorded profile identities; membership is synthetic. Dispatcharr may
// enable a new channel in every profile on its own, so the commit puts the
// created channel in both and only an explicit disable takes it out.
const fixture = (id: number) => {
  const profile = recordedProfiles.profiles.find(p => p.id === id)!;
  return { id: profile.id, name: profile.name, channels: [] as number[] };
};
let profiles = [fixture(6), fixture(9)];
let writesFail = true;

type Choice = 'untouched' | 'explicit' | 'cleared';
type Dialog = 'manual' | 'bulk';

// Defaults are [Madison]; the explicit choice is [Music] only.
const EXPECTED: Record<Choice, { reachedApp: number[] | undefined; intent: number[] }> = {
  untouched: { reachedApp: undefined, intent: [6] },
  explicit: { reachedApp: [9], intent: [9] },
  cleared: { reachedApp: [], intent: [] },
};

function savedIntents(): Array<{ channelId: number; profileIds: number[] }> {
  const keys = Object.keys(sessionStorage).filter(key => key.startsWith(INTENT_KEY_PREFIX));
  return keys.flatMap(key => JSON.parse(sessionStorage.getItem(key)!));
}

async function enterEditMode() {
  render(<App />);
  const editButton = await screen.findByRole('button', { name: /edit mode/i });
  await waitFor(() => expect(editButton).toBeEnabled());
  fireEvent.click(editButton);
  await screen.findByRole('button', { name: 'Open manual create' });
}

async function chooseProfiles(user: ReturnType<typeof userEvent.setup>, choice: Choice) {
  if (choice === 'untouched') return;
  await user.click(screen.getByText('Channel Profiles'));
  const madison = screen.getByRole('checkbox', { name: /Madison/ });
  expect(madison).toBeChecked();
  if (choice === 'cleared') {
    await user.click(screen.getByRole('button', { name: 'Clear selection' }));
  } else {
    await user.click(madison);
    await user.click(screen.getByRole('checkbox', { name: /Music/ }));
  }
}

describe('App create dialogs honor the profile picker', () => {
  beforeEach(() => {
    sessionStorage.clear();
    testState.manualCalls = [];
    testState.bulkCalls = [];
    profiles = [fixture(6), fixture(9)];
    writesFail = true;
    vi.stubGlobal('alert', vi.fn());
    window.history.replaceState({}, '', '#channel-manager');
    vi.mocked(api.getSettings).mockResolvedValue({
      configured: true, default_channel_profile_ids: [6], profile_recovery_destination: profileDestination,
      default_normalize_on_create: false,
      stream_sort_priority: [], stream_sort_enabled: {}, m3u_account_priorities: {},
    } as unknown as Awaited<ReturnType<typeof api.getSettings>>);
    vi.mocked(api.getChannelProfiles).mockReset().mockImplementation(async () =>
      profiles.map(profile => ({ ...profile, channels: [...profile.channels] })));
    vi.mocked(api.getChannelProfile).mockReset().mockImplementation(async (id: number) => {
      const profile = profiles.find(p => p.id === id)!;
      return { ...profile, channels: [...profile.channels] };
    });
    vi.mocked(api.updateProfileChannel).mockReset().mockRejectedValue(new Error('Synthetic write rejected'));
    vi.mocked(api.bulkUpdateProfileChannels).mockReset().mockImplementation(async (id, ids, enabled) => {
      if (writesFail) throw new Error('Synthetic write rejected');
      const profile = profiles.find(p => p.id === id)!;
      profile.channels = enabled
        ? [...new Set([...profile.channels, ...ids])]
        : profile.channels.filter(channelId => !ids.includes(channelId));
      return { success: true };
    });
    testState.bulkCommit.mockReset().mockImplementation(async (request: BulkCommitRequest) => {
      const creates = request.operations.filter(op => op.type === 'createChannel');
      for (const profile of profiles) profile.channels = [CREATED_ID];
      return {
        success: true, operationsApplied: request.operations.length, operationsFailed: 0, errors: [],
        tempIdMap: Object.fromEntries(creates.map(op => [Number(op.tempId), CREATED_ID])),
        profileRecoveryBinding: recoveryBinding, groupIdMap: {},
      } satisfies BulkCommitResponse;
    });
  });

  afterEach(() => vi.restoreAllMocks());

  const cases: Array<[Dialog, Choice, boolean]> = [
    ['manual', 'untouched', true], ['manual', 'explicit', true], ['manual', 'cleared', true],
    ['manual', 'untouched', false], ['manual', 'explicit', false], ['manual', 'cleared', false],
    // The bulk dialog always requires a starting number.
    ['bulk', 'untouched', true], ['bulk', 'explicit', true], ['bulk', 'cleared', true],
  ];

  it.each(cases)('%s dialog, %s picker, channel number given: %s', async (dialog, choice, withNumber) => {
    const user = userEvent.setup();
    await enterEditMode();

    await user.click(screen.getByRole('button', { name: dialog === 'manual' ? 'Open manual create' : 'Open bulk create' }));
    if (dialog === 'manual') {
      await user.type(await screen.findByPlaceholderText('Enter channel name'), 'Picker channel');
    }
    const number = await screen.findByPlaceholderText('e.g., 100 or 38.1');
    await user.clear(number);
    if (withNumber) await user.type(number, '100');
    await chooseProfiles(user, choice);
    await user.click(screen.getByRole('button', { name: dialog === 'manual' ? /Create Channel/ : /Create 1 Channel/ }));

    // What the dialog handed App.
    if (dialog === 'manual') {
      await waitFor(() => expect(testState.manualCalls).toHaveLength(1));
      const [, channelNumber, , , , profileIds] = testState.manualCalls[0];
      expect(channelNumber).toBe(withNumber ? 100 : undefined);
      expect(profileIds).toEqual(EXPECTED[choice].reachedApp);
    } else {
      await waitFor(() => expect(testState.bulkCalls).toHaveLength(1));
      expect(testState.bulkCalls[0][15]).toEqual(EXPECTED[choice].reachedApp);
    }

    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));

    // The commit payload carries the create and opts into profile recovery.
    await waitFor(() => expect(testState.bulkCommit).toHaveBeenCalledTimes(1));
    const request = testState.bulkCommit.mock.calls[0][0] as BulkCommitRequest;
    const creates = request.operations.filter(op => op.type === 'createChannel');
    expect(creates).toHaveLength(1);
    expect(creates[0].channelNumber ?? undefined).toBe(withNumber ? 100 : undefined);
    expect(request).toMatchObject({
      profileRecoveryOperator: 'local#7', profileRecoveryDestination: profileDestination,
    });

    // The first membership attempt is rejected, so the intent is retained.
    const notice = await screen.findByText(/profile assignments unresolved/i);
    expect(notice).toBeInTheDocument();
    expect(savedIntents()).toEqual([
      expect.objectContaining({ channelId: CREATED_ID, profileIds: EXPECTED[choice].intent }),
    ]);

    // Retry against a healthy peer applies exactly the operator's choice.
    vi.mocked(api.bulkUpdateProfileChannels).mockClear();
    writesFail = false;
    const retry = screen.getByRole('button', { name: 'Retry profile assignments' });
    await waitFor(() => expect(retry).toBeEnabled());
    await act(async () => { fireEvent.click(retry); });
    await waitFor(() => expect(screen.queryByText(/profile assignments unresolved/i)).not.toBeInTheDocument());

    const expectedOff = [6, 9].filter(id => !EXPECTED[choice].intent.includes(id));
    expect(vi.mocked(api.bulkUpdateProfileChannels).mock.calls).toEqual(
      expectedOff.map(id => [id, [CREATED_ID], false, recoveryBinding]),
    );
    for (const profile of profiles) {
      expect(profile.channels.includes(CREATED_ID), `profile ${profile.id}`)
        .toBe(EXPECTED[choice].intent.includes(profile.id));
    }
    expect(savedIntents()).toEqual([]);
    expect(testState.bulkCommit).toHaveBeenCalledTimes(1);
  });

  // App's own resolution, independent of the dialog: `[]` must not collapse
  // into the defaults on either create callback.
  it.each([
    ['manual', undefined, [6]], ['manual', [], []], ['manual', [9], [9]],
    ['bulk', undefined, [6]], ['bulk', [], []], ['bulk', [9], [9]],
  ] as Array<[Dialog, number[] | undefined, number[]]>)(
    '%s callback resolves profileIds %j to retained intent %j',
    async (dialog, profileIds, intent) => {
      await enterEditMode();
      await act(async () => {
        if (dialog === 'manual') {
          await testState.props!.onCreateChannelManual!('Callback channel', undefined, undefined, undefined, false, profileIds);
        } else {
          await testState.props!.onBulkCreateFromGroup(
            STREAMS, 100, null, undefined, undefined, undefined, false, undefined,
            false, undefined, undefined, undefined, undefined, undefined, undefined,
            profileIds, false, new ResolvedCreateChannelNames(new Map([[STREAM.name, STREAM.name]]), false),
          );
        }
      });
      fireEvent.click(screen.getByTitle('Apply changes'));
      fireEvent.click(await screen.findByRole('button', { name: 'Apply' }));
      expect(await screen.findByText(/profile assignments unresolved/i)).toBeInTheDocument();
      expect(savedIntents()).toEqual([expect.objectContaining({ channelId: CREATED_ID, profileIds: intent })]);
    },
  );
});
