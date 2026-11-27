import { StrictMode, type ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChannelManagerTabProps } from './components/tabs/ChannelManagerTab';
import type { BulkCommitRequest, BulkCommitResponse, ResolvedCreateChannelNames } from './services/api';
import type { Channel, EditModeSummary, Stream } from './types';
import { STAGED_LEDGER_STORAGE_KEY, type PersistedStagedLedger } from './utils/stagedLedgerStorage';
import * as api from './services/api';
import { HttpError } from './services/httpClient';
import recordedProfiles from '../../backend/tests/fixtures/y3m6o1/dispatcharr_channel_profiles_mixed_membership.json';

const testState = vi.hoisted(() => ({
  operatorId: 7,
  onSettingsSaved: null as (() => Promise<void>) | null,
  channelManagerProps: null as ChannelManagerTabProps | null,
  guideProps: null as Record<string, unknown> | null,
  exitDialogProps: null as Record<string, unknown> | null,
  bulkCommit: vi.fn(),
  resolvedAssignmentPairs: [] as Array<{ channelId: number; streamId: number }>,
}));
const recoveryBinding = 'f'.repeat(64);
const profileDestination = vi.hoisted(() => 'a'.repeat(64));

vi.mock('./hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: testState.operatorId, auth_provider: 'local', username: 'operator', is_admin: true },
    logout: vi.fn(),
  }),
  useAdminNavVisible: () => true,
}));

vi.mock('./components/tabs/ChannelManagerTab', () => ({
  ChannelManagerTab: (props: ChannelManagerTabProps) => {
    testState.channelManagerProps = props;
    return <div data-testid="channel-manager" />;
  },
}));

vi.mock('./components/tabs/GuideTab', () => ({
  GuideTab: (props: Record<string, unknown>) => {
    testState.guideProps = props;
    return <div data-testid="guide" />;
  },
}));

vi.mock('./components/NotificationCenter', () => ({
  NotificationCenter: () => null,
}));

vi.mock('./components', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./components')>();
  return {
    ...actual,
    SettingsModal: ({ onSaved }: { onSaved: () => Promise<void> }) => {
      testState.onSettingsSaved = onSaved;
      return null;
    },
    EditModeRestoreDialog: () => null,
    EditModeRestoredBadge: () => null,
    TabNavigation: () => null,
    UserMenu: () => null,
    PageHeader: ({ actions }: { actions?: ReactNode }) => <div>{actions}</div>,
    EditModeExitDialog: (props: Record<string, unknown>) => {
      testState.exitDialogProps = props;
      if (!props.isOpen) return null;
      const summary = props.summary as EditModeSummary;
      return (
        <div>
          <span>{summary.newChannels} channels</span>
          <span>{summary.streamsAdded} stream assignments</span>
          <button onClick={() => void (props.onApply as () => Promise<void>)()}>Apply</button>
        </div>
      );
    },
  };
});

vi.mock('./services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./services/api')>();
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({ status: 'healthy', service: 'ECM' }),
    getSettings: vi.fn().mockResolvedValue({
      configured: true,
      profile_recovery_destination: profileDestination,
      default_channel_profile_ids: [],
      stream_sort_priority: [],
      stream_sort_enabled: {},
      m3u_account_priorities: {},
    }),
    getChannelGroups: vi.fn().mockResolvedValue([]),
    getChannels: vi.fn().mockResolvedValue({ results: [], count: 0, next: null }),
    getProviderGroupSettings: vi.fn().mockResolvedValue([]),
    getM3UAccounts: vi.fn().mockResolvedValue([]),
    getStreamGroups: vi.fn().mockResolvedValue([]),
    getStreams: vi.fn().mockResolvedValue({ results: [], count: 0, next: null }),
    getAllLogos: vi.fn().mockResolvedValue([]),
    getStreamProfiles: vi.fn().mockResolvedValue([]),
    getChannelProfiles: vi.fn().mockResolvedValue([]),
    getProfileConflictReviews: vi.fn().mockResolvedValue({ reviews: [], total: 0 }),
    getChannelProfile: vi.fn(),
    updateProfileChannel: vi.fn(),
    bulkUpdateProfileChannels: vi.fn(),
    getEPGSources: vi.fn().mockResolvedValue([]),
    getEPGData: vi.fn().mockResolvedValue([]),
    getM3UStreamMetadata: vi.fn().mockResolvedValue({ count: 0, metadata: {} }),
    bulkCommit: (request: BulkCommitRequest, batchId?: string) => testState.bulkCommit(request, batchId),
    bulkAssignChannelNumbers: vi.fn().mockResolvedValue(undefined),
    updateChannel: vi.fn(),
  };
});

import App from './App';
import { ResolvedCreateChannelNames as NameResolution } from './services/api';

function makeStream(index: number): Stream {
  return {
    id: 10_000 + index,
    name: `Fixture ${String(index).padStart(3, '0')}`,
    url: `http://stream/${index}`,
    m3u_account: null,
    stream_group_id: null,
    logo_url: null,
    tvg_id: null,
    current_viewers: 0,
    stream_profile_id: null,
    custom_properties: {},
    channel_group: null,
    channel_group_name: null,
    is_custom: false,
  } as Stream;
}

describe('App bulk-create staging', () => {
  beforeEach(() => {
    sessionStorage.clear();
    testState.operatorId = 7;
    vi.mocked(api.getChannels).mockReset().mockResolvedValue({ results: [], count: 0, next: null, previous: null });
    vi.mocked(api.getSettings).mockReset().mockResolvedValue({
      configured: true, default_channel_profile_ids: [], profile_recovery_destination: profileDestination,
      stream_sort_priority: [], stream_sort_enabled: {}, m3u_account_priorities: {},
    } as unknown as Awaited<ReturnType<typeof api.getSettings>>);
    vi.mocked(api.getChannelProfiles).mockReset().mockResolvedValue([]);
    vi.mocked(api.getChannelProfile).mockReset();
    vi.mocked(api.updateProfileChannel).mockReset();
    vi.mocked(api.bulkUpdateProfileChannels).mockReset();
    testState.channelManagerProps = null;
    testState.guideProps = null;
    testState.exitDialogProps = null;
    testState.bulkCommit.mockReset();
    testState.resolvedAssignmentPairs = [];
    vi.stubGlobal('alert', vi.fn());
    window.history.replaceState({}, '', '#channel-manager');
    testState.bulkCommit.mockImplementation(async (request: BulkCommitRequest) => {
      const creates = request.operations.filter((operation) => operation.type === 'createChannel');
      const tempIds = creates.map((operation) => {
        if (typeof operation.tempId !== 'number') throw new Error('create missing numeric temp id');
        return operation.tempId;
      });
      if (new Set(tempIds).size !== tempIds.length) {
        throw new Error('duplicate create temp id');
      }
      const tempIdMap: Record<number, number> = {};
      creates.forEach((operation, index) => {
        if (typeof operation.tempId !== 'number') throw new Error('create missing numeric temp id');
        tempIdMap[operation.tempId] = 50_000 + index;
      });
      testState.resolvedAssignmentPairs = request.operations.flatMap((operation) => {
        if (operation.type !== 'addStreamToChannel') return [];
        if (typeof operation.channelId !== 'number' || typeof operation.streamId !== 'number') {
          throw new Error('assignment missing numeric ids');
        }
        const channelId = tempIdMap[operation.channelId];
        if (channelId === undefined) throw new Error(`unresolved assignment ${operation.channelId}`);
        return [{ channelId, streamId: operation.streamId }];
      });
      return {
        success: true,
        operationsApplied: request.operations.length,
        operationsFailed: 0,
        errors: [],
        tempIdMap,
        profileRecoveryBinding: recoveryBinding,
        groupIdMap: {},
      } satisfies BulkCommitResponse;
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it.each(['accepted-unapplied', 'rejected', 'final-read-failed', 'lost-response'] as const)(
    'retains post-create profile intent and retries membership only after %s (0m06f.3.4.1)',
    async initialFault => {
      let fault: string = initialFault;
      let committed = false;
      let wrote = false;
      const created = {
        id: 50_000, name: 'New profile-bound channel', channel_number: 100,
        channel_group_id: null, tvg_id: null, tvc_guide_stationid: null,
        epg_data_id: null, streams: [], stream_profile_id: null,
        uuid: 'synthetic-created', logo_id: null, auto_created: false,
        auto_created_by: null, auto_created_by_name: null,
      };
      const originalCommit = testState.bulkCommit.getMockImplementation()!;
      testState.bulkCommit.mockImplementation(async (...args) => {
        const result = await originalCommit(...args);
        committed = true;
        return result;
      });
      vi.mocked(api.getChannels).mockImplementation(async () => ({
        results: committed ? [created] : [], count: committed ? 1 : 0, next: null, previous: null,
      }));
      // Parse the recorded enabled-only contract; the created channel is synthetic.
      const profile = { ...recordedProfiles.profiles.find(p => p.id === 6)!, channels: [] as number[] };
      vi.mocked(api.getSettings).mockResolvedValue({
        configured: true, default_channel_profile_ids: [6],
        profile_recovery_destination: profileDestination,
        stream_sort_priority: [], stream_sort_enabled: {}, m3u_account_priorities: {},
      } as unknown as Awaited<ReturnType<typeof api.getSettings>>);
      const read = async () => {
        if (wrote && fault === 'final-read-failed') throw new Error('Synthetic read unavailable');
        return { ...profile, channels: [...profile.channels] };
      };
      vi.mocked(api.getChannelProfiles).mockImplementation(async () => [await read()]);
      vi.mocked(api.getChannelProfile).mockImplementation(read);
      const write = async (enabled: boolean) => {
        wrote = true;
        if (fault === 'rejected') throw new Error('Synthetic write rejected');
        if (fault !== 'accepted-unapplied') profile.channels = enabled ? [created.id] : [];
        if (fault === 'lost-response') throw new Error('Synthetic reply lost after write');
        return { success: true };
      };
      vi.mocked(api.updateProfileChannel).mockImplementation(async (_profile, _channel, body) => write(body.enabled!));
      vi.mocked(api.bulkUpdateProfileChannels).mockImplementation(async (_profile, _channels, enabled) => write(enabled));

      render(<App />);
      const editButton = await screen.findByRole('button', { name: /edit mode/i });
      await waitFor(() => expect(editButton).toBeEnabled());
      fireEvent.click(editButton);
      await waitFor(() => expect(testState.channelManagerProps?.isEditMode).toBe(true));
      await act(async () => {
        await testState.channelManagerProps!.onCreateChannelManual!(created.name, initialFault === 'rejected' ? undefined : 100);
      });
      expect(wrote).toBe(false);
      fireEvent.click(screen.getByTitle('Apply changes'));
      fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
      await waitFor(() => expect(wrote).toBe(true));
      await waitFor(() => expect(testState.exitDialogProps?.isOpen).toBe(false));
      if (initialFault === 'lost-response') {
        await waitFor(() => expect(screen.queryByText(/checking profile assignments/i)).not.toBeInTheDocument());
        expect(screen.queryByText(/profile assignments unresolved/i)).not.toBeInTheDocument();
        expect(profile.channels).toEqual([created.id]);
        expect(api.bulkUpdateProfileChannels).toHaveBeenCalledTimes(1);
        expect(api.updateProfileChannel).not.toHaveBeenCalled();
        expect(testState.bulkCommit).toHaveBeenCalledTimes(1);
        return;
      }
      expect(await screen.findByText(/profile assignments unresolved/i)).toBeInTheDocument();
      const retry = screen.getByRole('button', { name: 'Retry profile assignments' });
      await waitFor(() => expect(retry).toBeEnabled());
      fault = 'healthy';
      fireEvent.click(retry);
      await waitFor(() => expect(screen.queryByText(/profile assignments unresolved/i)).not.toBeInTheDocument());
      expect(profile.channels).toContain(created.id);
      expect(testState.bulkCommit).toHaveBeenCalledTimes(1);
    },
  );

  it('outside Edit Mode, a range renumber sends the numbers of the lineup the UI loaded', async () => {
    const template: Channel = {
      id: 44, name: 'Original', channel_number: 80, streams: [],
      channel_group_id: null, tvg_id: null, tvc_guide_stationid: null, epg_data_id: null,
      stream_profile_id: null, uuid: 'existing-44', logo_id: null,
      auto_created: false, auto_created_by: null, auto_created_by_name: null,
    };
    const loaded = [template, { ...template, id: 45, uuid: 'existing-45', channel_number: null }];
    vi.mocked(api.getChannels).mockResolvedValue({ results: loaded, count: 2, next: null, previous: null });
    vi.mocked(api.bulkAssignChannelNumbers).mockClear();

    render(<App />);
    await waitFor(() => expect(testState.channelManagerProps?.channels.map(ch => ch.id)).toEqual([44, 45]));
    expect(testState.channelManagerProps!.isEditMode).toBe(false);
    await act(async () => {
      await testState.channelManagerProps!.onChannelReorder([44, 45], 100);
    });

    expect(api.bulkAssignChannelNumbers).toHaveBeenCalledWith([44, 45], 100, { 44: 80, 45: null });
  });

  it('outside Edit Mode, a refused range renumber shows why the server refused it', async () => {
    const template: Channel = {
      id: 44, name: 'Original', channel_number: 80, streams: [],
      channel_group_id: null, tvg_id: null, tvc_guide_stationid: null, epg_data_id: null,
      stream_profile_id: null, uuid: 'existing-44', logo_id: null,
      auto_created: false, auto_created_by: null, auto_created_by_name: null,
    };
    vi.mocked(api.getChannels).mockResolvedValue({ results: [template], count: 1, next: null, previous: null });
    const message = 'Channel was not renumbered: channel 44 is on channel number 81, and this '
      + 'request expected 80. Somebody else changed it.';
    vi.mocked(api.bulkAssignChannelNumbers).mockRejectedValueOnce(new HttpError(message, 409, {
      message,
      concurrentNumberChanges: [{ channelId: 44, baselineNumber: 80, serverNumber: 81 }],
    }));

    const { container } = render(<App />);
    await waitFor(() => expect(testState.channelManagerProps?.channels.map(ch => ch.id)).toEqual([44]));
    await act(async () => {
      await testState.channelManagerProps!.onChannelReorder([44], 100);
    });

    await waitFor(() => {
      const status = container.querySelector('.service-status');
      expect(status?.getAttribute('title')).toContain('Somebody else changed it');
    });
    expect(container.querySelector('.service-status')?.getAttribute('title')).toContain('channel 44');
  });

  describe('direct PATCH numbering writes carry the displayed baseline', () => {
    const shown: Channel = {
      id: 44, name: 'Original', channel_number: 80, streams: [],
      channel_group_id: null, tvg_id: null, tvc_guide_stationid: null, epg_data_id: null,
      stream_profile_id: null, uuid: 'existing-44', logo_id: null,
      auto_created: false, auto_created_by: null, auto_created_by_name: null,
    };
    const message = 'Channel was not renumbered: channel 44 is on channel number 81, and this '
      + 'request expected 80. Somebody else changed it.';
    const conflict = () => new HttpError(message, 409, {
      message,
      concurrentNumberChanges: [{ channelId: 44, baselineNumber: 80, serverNumber: 81 }],
    });

    beforeEach(() => {
      vi.mocked(api.getChannels).mockResolvedValue({ results: [shown], count: 1, next: null, previous: null });
      vi.mocked(api.updateChannel).mockReset().mockImplementation(async (id, data) => ({
        ...shown, id, ...(data as Partial<Channel>),
      }));
    });

    async function renderGuide() {
      window.history.replaceState({}, '', '#guide');
      const view = render(<App />);
      await waitFor(() => expect(testState.guideProps).not.toBeNull());
      await waitFor(() => expect((testState.guideProps!.channels as Channel[]).map(ch => ch.id)).toEqual([44]));
      return view;
    }

    type GuideUpdate = (channel: Channel, changes: Record<string, unknown>) => Promise<void>;

    it('the Guide edit sends the number the Guide showed', async () => {
      await renderGuide();
      await act(async () => {
        await (testState.guideProps!.onChannelUpdate as GuideUpdate)(shown, { channel_number: 90 });
      });
      expect(api.updateChannel).toHaveBeenCalledWith(44, { channel_number: 90 }, { expectedNumber: 80 });
    });

    it('the Guide edit sends no baseline when the number is not changed', async () => {
      await renderGuide();
      await act(async () => {
        await (testState.guideProps!.onChannelUpdate as GuideUpdate)(shown, { name: 'Renamed' });
      });
      expect(api.updateChannel).toHaveBeenCalledWith(44, { name: 'Renamed' }, undefined);
    });

    it('a refused Guide edit shows why the server refused it', async () => {
      vi.mocked(api.updateChannel).mockRejectedValueOnce(conflict());
      const { container } = await renderGuide();
      await act(async () => {
        await expect(
          (testState.guideProps!.onChannelUpdate as GuideUpdate)(shown, { channel_number: 90 }),
        ).rejects.toThrow(message);
      });
      await waitFor(() => {
        expect(container.querySelector('.service-status')?.getAttribute('title')).toContain('Somebody else changed it');
      });
    });

    it('undo outside Edit Mode expects the number the change recorded, and a refusal reloads', async () => {
      const { container } = render(<App />);
      await waitFor(() => expect(testState.channelManagerProps?.channels.map(ch => ch.id)).toEqual([44]));
      act(() => {
        testState.channelManagerProps!.onChannelUpdate(
          { ...shown, channel_number: 90 },
          { type: 'channel_number_update', description: 'Changed channel number from 80 to 90' },
        );
      });
      await waitFor(() => expect(testState.channelManagerProps!.canUndo).toBe(true));
      vi.mocked(api.updateChannel).mockRejectedValueOnce(conflict());
      const readsBefore = vi.mocked(api.getChannels).mock.calls.length;

      await act(async () => { await testState.channelManagerProps!.onUndo(); });

      expect(api.updateChannel).toHaveBeenCalledWith(
        44, { channel_number: 80, name: 'Original' }, { expectedNumber: 90 },
      );
      await waitFor(() => {
        expect(container.querySelector('.service-status')?.getAttribute('title')).toContain('Somebody else changed it');
      });
      await waitFor(() => expect(vi.mocked(api.getChannels).mock.calls.length).toBeGreaterThan(readsBefore));
      expect(testState.channelManagerProps!.canUndo).toBe(false);
      expect(testState.channelManagerProps!.canRedo).toBe(false);
    });
  });

  it('numbers mixed mapped and unmapped Create groups by visible natural name', async () => {
    const names = ['ZDF alias', 'ABC', 'C-SPAN 10', 'C-SPAN 2', 'C-SPAN'];
    const streams = names.map((name, index) => ({ ...makeStream(index), name }));
    const resolution = new NameResolution(
      new Map(names.map(name => [name, name === 'ZDF alias' ? 'ZDF' : name])),
      false, new Set(['ZDF alias']),
    );
    render(<App />);
    const editButton = await screen.findByRole('button', { name: /edit mode/i });
    await waitFor(() => expect(editButton).toBeEnabled());
    fireEvent.click(editButton);
    await waitFor(() => expect(testState.channelManagerProps?.isEditMode).toBe(true));
    await act(async () => {
      await testState.channelManagerProps!.onBulkCreateFromGroup(
        streams, 100, null, undefined, undefined, undefined, false, undefined,
        false, undefined, undefined, undefined, undefined, undefined, undefined,
        [], false, resolution,
      );
    });
    await waitFor(() => {
      const ledger = JSON.parse(sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)!) as PersistedStagedLedger;
      const channels = ledger.operations.filter(op => op.apiCall.type === 'createChannel')
        .map(op => op.afterSnapshot[0]);
      expect(channels.map(channel => [channel.name, channel.channel_number])).toEqual([
        ['ABC', 100], ['C-SPAN', 101], ['C-SPAN 2', 102], ['C-SPAN 10', 103], ['ZDF', 104],
      ]);
    });
  });

  it.each(['refresh', 'later-batch', 'mixed-receipt'] as const)('does not lose known creates or misreport other work after %s failure', async failure => {
    const mixed = failure === 'mixed-receipt';
    testState.bulkCommit.mockImplementation(async (request: BulkCommitRequest) => {
      const creates = request.operations.filter(op => op.type === 'createChannel');
      if (!creates.length) {
        const ledger = JSON.parse(sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)!) as PersistedStagedLedger;
        expect(ledger.operations.map(op => op.apiCall.type)).toEqual([
          'createChannel', 'updateChannel', ...(mixed ? ['updateChannel'] : []),
        ]);
        expect(ledger.operations[0].description).toContain('Unmapped sibling');
        expect(sessionStorage.getItem(`ecm.created-channel-profiles.v1:${encodeURIComponent(JSON.stringify(['local#7', '']))}`))
          .toContain('"channelId":50000');
        expect(api.getChannelProfiles).not.toHaveBeenCalledWith(recoveryBinding);
        if (failure === 'later-batch') throw new Error('Synthetic later batch reply unavailable');
        return {
          success: false, operationsApplied: mixed ? 1 : 0, operationsFailed: 1, errors: [
            { operationId: 'unrelated', error: 'Unrelated update rejected' },
          ], tempIdMap: {}, groupIdMap: {}, settledOperationIndexes: mixed ? [1] : [],
        };
      }
      expect(creates.map(op => op.name)).toEqual(['Mapped channel', 'Unmapped sibling']);
      const tempId = Number(creates[0].tempId);
      expect(tempId).toBeLessThan(0);
      if (failure !== 'later-batch') vi.mocked(api.getChannels).mockRejectedValue(new Error('Synthetic postcommit refresh failure'));
      return {
        success: false, operationsApplied: 1, operationsFailed: 1, errors: [
          { operationId: 'unmapped', channelName: 'Unmapped sibling', error: 'Create rejected' },
        ], tempIdMap: { [tempId]: 50000 }, groupIdMap: {},
        profileRecoveryBinding: recoveryBinding,
      };
    });
    const existing: Channel = {
      id: 44, name: 'Existing channel', channel_number: 44, streams: [],
      channel_group_id: null, tvg_id: null, tvc_guide_stationid: null, epg_data_id: null,
      stream_profile_id: null, uuid: 'synthetic-existing-channel', logo_id: null,
      auto_created: false, auto_created_by: null, auto_created_by_name: null,
    };
    const loaded = [existing, ...(mixed ? [{ ...existing, id: 45, channel_number: 45 }] : [])];
    vi.mocked(api.getChannels).mockResolvedValue({ results: loaded, count: loaded.length, next: null, previous: null });
    vi.mocked(api.getSettings).mockResolvedValue({
      ...await api.getSettings(), default_channel_profile_ids: [6],
    });
    render(<App />);
    const editButton = await screen.findByRole('button', { name: /edit mode/i });
    await waitFor(() => expect(editButton).toBeEnabled());
    fireEvent.click(editButton);
    await waitFor(() => expect(testState.channelManagerProps?.isEditMode).toBe(true));
    await act(async () => {
      await testState.channelManagerProps!.onCreateChannelManual!('Mapped channel', 100);
      await testState.channelManagerProps!.onCreateChannelManual!('Unmapped sibling', 101);
    });
    act(() => testState.channelManagerProps!.onStageUpdateChannel(44, { name: 'Unrelated update' }, 'Unrelated update'));
    if (mixed) act(() => testState.channelManagerProps!.onStageUpdateChannel(45, { name: 'Confirmed update' }, 'Confirmed update'));
    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(await screen.findByText(/profile assignments unresolved/i)).toBeInTheDocument();
    expect(screen.getByText(/Channel #50000/)).toBeInTheDocument();
    expect(testState.exitDialogProps?.commitFailure).toMatchObject({
      applied: mixed ? 2 : 1, failed: failure === 'later-batch' ? 1 : 2,
    });
    expect((testState.exitDialogProps?.commitFailure as { messages: string[] }).messages).toEqual(
      expect.arrayContaining([
        'Unmapped sibling: Create rejected',
        failure === 'later-batch' ? expect.stringContaining('Check Dispatcharr before retrying') : 'Unrelated update rejected',
      ]),
    );
    const ledger = JSON.parse(sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)!) as PersistedStagedLedger;
    expect(ledger.operations.map(op => op.description)).toEqual(['Create channel "Unmapped sibling"', 'Unrelated update']);
    expect(testState.channelManagerProps?.isEditMode).toBe(true);
    expect(testState.bulkCommit).toHaveBeenCalledTimes(2);
    if (failure !== 'later-batch') {
      // Return to edit mode and retry after the peer is healthy.
      act(() => (testState.exitDialogProps?.onAcknowledgeFailure as () => void)());
      vi.mocked(api.getChannels).mockResolvedValue({ results: loaded, count: loaded.length, next: null, previous: null });
      testState.bulkCommit.mockImplementation(async (request: BulkCommitRequest) => ({
        success: true, operationsApplied: request.operations.length, operationsFailed: 0, errors: [],
        tempIdMap: Object.fromEntries(request.operations.filter(op => op.type === 'createChannel')
          .map(op => [Number(op.tempId), 50001])),
        groupIdMap: {}, profileRecoveryBinding: recoveryBinding,
      }));
      fireEvent.click(screen.getByTitle('Apply changes'));
      fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
      await waitFor(() => expect(testState.bulkCommit).toHaveBeenCalledTimes(4));
      expect(testState.bulkCommit.mock.calls.slice(2).map(([request]) => request.operations)).toEqual([
        [expect.objectContaining({ type: 'createChannel', name: 'Unmapped sibling' })],
        [expect.objectContaining({ type: 'updateChannel', channelId: 44 })],
      ]);
      await waitFor(() => expect(sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull());
    }
  });

  it('retries a rejected rename without reviving its superseded number after refresh fails', async () => {
    const channel: Channel = {
      id: 44, name: 'Original', channel_number: 80, streams: [],
      channel_group_id: null, tvg_id: null, tvc_guide_stationid: null, epg_data_id: null,
      stream_profile_id: null, uuid: 'existing-44', logo_id: null,
      auto_created: false, auto_created_by: null, auto_created_by_name: null,
    };
    const read = { results: [channel], count: 1, next: null, previous: null };
    vi.mocked(api.getChannels).mockResolvedValue(read);
    testState.bulkCommit.mockImplementationOnce(async (request: BulkCommitRequest) => ({
      success: true, operationsApplied: 1, operationsFailed: 0, errors: [],
      tempIdMap: { [Number(request.operations[0].tempId)]: 50000 }, groupIdMap: {},
    })).mockImplementationOnce(async () => {
      vi.mocked(api.getChannels).mockRejectedValue(new Error('Post-Apply refresh unavailable'));
      return {
        success: false, operationsApplied: 1, operationsFailed: 1,
        errors: [{ operationId: 'op-0-updateChannel', error: 'Rename rejected' }],
        tempIdMap: {}, groupIdMap: {}, settledOperationIndexes: [1], settledChannelNumbers: { 44: 80 },
      };
    }).mockResolvedValue({
      success: true, operationsApplied: 1, operationsFailed: 0, errors: [],
      tempIdMap: {}, groupIdMap: {},
    });

    render(<App />);
    const editButton = await screen.findByRole('button', { name: /edit mode/i });
    await waitFor(() => expect(editButton).toBeEnabled());
    fireEvent.click(editButton);
    await waitFor(() => expect(testState.channelManagerProps?.isEditMode).toBe(true));
    await act(async () => testState.channelManagerProps!.onCreateChannelManual!('Mapped channel', 100));
    act(() => {
      testState.channelManagerProps!.onStageUpdateChannel(44, { name: 'Renamed', channel_number: 70 }, 'Rename and number');
      testState.channelManagerProps!.onStageBulkAssignNumbers([44], 80, 'Put channel on 80');
    });
    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(testState.exitDialogProps?.commitFailure).toMatchObject({
      applied: 2, failed: 1, keepEditing: true,
    }));
    expect(testState.bulkCommit.mock.calls[1][0].operations).toEqual([
      expect.objectContaining({ type: 'updateChannel', data: { name: 'Renamed', channel_number: 70 } }),
      expect.objectContaining({ type: 'bulkAssignChannelNumbers', channelIds: [44], startingNumber: 80 }),
    ]);
    const saved = JSON.parse(sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)!) as PersistedStagedLedger;

    act(() => (testState.exitDialogProps?.onAcknowledgeFailure as () => void)());
    act(() => testState.channelManagerProps!.onUndo());
    expect(testState.channelManagerProps!.channels.find(ch => ch.id === 44)).toMatchObject({ name: 'Original', channel_number: 80 });
    act(() => testState.channelManagerProps!.onRedo());
    expect(testState.channelManagerProps!.channels.find(ch => ch.id === 44)).toMatchObject({ name: 'Renamed', channel_number: 80 });
    vi.mocked(api.getChannels).mockResolvedValue(read);
    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(testState.bulkCommit).toHaveBeenCalledTimes(3));
    const retry = testState.bulkCommit.mock.calls[2][0].operations;
    expect(retry).toHaveLength(1);
    expect(retry[0]).toMatchObject({ type: 'updateChannel', channelId: 44 });
    expect(retry[0].data).toEqual({ name: 'Renamed' });
    expect(retry[0]).not.toHaveProperty('expectedNumber');
    expect(saved.operations.map(op => op.apiCall)).toEqual([
      { type: 'updateChannel', channelId: 44, data: { name: 'Renamed' } },
    ]);
    expect(saved.operations[0].afterSnapshot[0].channel_number).toBe(80);
  });

  it.each(['placement', 'deletion'] as const)('preserves both original offsets when a settled %s cuts a rejected range in two', async settled => {
    const template: Channel = {
      id: 44, name: 'Original', channel_number: 80, streams: [],
      channel_group_id: null, tvg_id: null, tvc_guide_stationid: null, epg_data_id: null,
      stream_profile_id: null, uuid: 'existing-44', logo_id: null,
      auto_created: false, auto_created_by: null, auto_created_by_name: null,
    };
    const read = { results: [template, { ...template, id: 45, channel_number: 81 },
      { ...template, id: 46, channel_number: 82 }], count: 3, next: null, previous: null };
    vi.mocked(api.getChannels).mockResolvedValue(read);
    testState.bulkCommit.mockImplementationOnce(async (request: BulkCommitRequest) => ({
      success: true, operationsApplied: 1, operationsFailed: 0, errors: [],
      tempIdMap: { [Number(request.operations[0].tempId)]: 50000 }, groupIdMap: {},
    })).mockImplementationOnce(async () => {
      vi.mocked(api.getChannels).mockRejectedValue(new Error('Post-Apply refresh unavailable'));
      return {
        success: false, operationsApplied: 1, operationsFailed: 2,
        errors: [{ operationId: 'op-1-bulkAssignChannelNumbers', error: 'First range rejected' },
          { operationId: 'op-2-bulkAssignChannelNumbers', error: 'Second range rejected' }],
        tempIdMap: {}, groupIdMap: {}, settledOperationIndexes: [1],
        settledChannelNumbers: settled === 'placement' ? { 45: 81 } : {},
      };
    }).mockResolvedValue({
      success: true, operationsApplied: 2, operationsFailed: 0, errors: [],
      tempIdMap: {}, groupIdMap: {},
    });

    render(<App />);
    const editButton = await screen.findByRole('button', { name: /edit mode/i });
    await waitFor(() => expect(editButton).toBeEnabled());
    fireEvent.click(editButton);
    await waitFor(() => expect(testState.channelManagerProps?.isEditMode).toBe(true));
    await act(async () => testState.channelManagerProps!.onCreateChannelManual!('Mapped channel', 100));
    act(() => {
      testState.channelManagerProps!.onStageBulkAssignNumbers([44, 45, 46], 70, 'Place channels starting at 70');
      if (settled === 'placement') testState.channelManagerProps!.onStageBulkAssignNumbers([45], 81, 'Keep middle channel on 81');
      else testState.channelManagerProps!.onStageDeleteChannel(45, 'Delete middle channel');
    });
    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(testState.exitDialogProps?.commitFailure).toMatchObject({ applied: 2, failed: 2 }));
    const saved = JSON.parse(sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)!) as PersistedStagedLedger;
    act(() => (testState.exitDialogProps?.onAcknowledgeFailure as () => void)());
    vi.mocked(api.getChannels).mockResolvedValue(settled === 'placement'
      ? read : { ...read, results: read.results.filter(ch => ch.id !== 45), count: 2 });
    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(testState.bulkCommit).toHaveBeenCalledTimes(3));
    const expected = [
      { type: 'bulkAssignChannelNumbers', channelIds: [44], startingNumber: 70 },
      { type: 'bulkAssignChannelNumbers', channelIds: [46], startingNumber: 72 },
    ];
    // On the wire each range also carries its channel's session baseline.
    expect(testState.bulkCommit.mock.calls[2][0].operations).toEqual([
      { ...expected[0], expectedNumbers: { 44: 80 } },
      { ...expected[1], expectedNumbers: { 46: 82 } },
    ]);
    expect(saved.operations.map(op => op.apiCall)).toEqual(expected);
    expect(saved.undoGroups).toEqual([saved.operations.map(op => op.id)]);
    expect(saved.operations.flatMap(op => op.beforeSnapshot.map(ch => ch.id))).toEqual([44, 46]);
  });

  it('keeps a late create receipt in the original operator scope without starting recovery', async () => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const originalCommit = testState.bulkCommit.getMockImplementation()!;
    testState.bulkCommit.mockImplementation(async (...args) => {
      await gate;
      return originalCommit(...args);
    });
    vi.mocked(api.getSettings).mockResolvedValue({
      ...await api.getSettings(), default_channel_profile_ids: [6],
    });
    const { rerender } = render(<App />);
    const editButton = await screen.findByRole('button', { name: /edit mode/i });
    await waitFor(() => expect(editButton).toBeEnabled());
    fireEvent.click(editButton);
    await waitFor(() => expect(testState.channelManagerProps?.isEditMode).toBe(true));
    await act(async () => testState.channelManagerProps!.onCreateChannelManual!('Original operator channel', 100));
    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(testState.bulkCommit).toHaveBeenCalledTimes(1));
    expect(testState.bulkCommit.mock.calls[0][0]).toMatchObject({ profileRecoveryOperator: 'local#7' });
    expect(testState.bulkCommit.mock.calls[0][0]).toMatchObject({ profileRecoveryDestination: profileDestination });
    testState.operatorId = 8;
    rerender(<App />);
    await act(async () => release());
    expect(api.getChannelProfiles).not.toHaveBeenCalledWith(recoveryBinding);
    expect(api.bulkUpdateProfileChannels).not.toHaveBeenCalled();
    expect(screen.queryByText(/profile assignments unresolved/i)).not.toBeInTheDocument();
    testState.operatorId = 7;
    rerender(<App />);
    expect(await screen.findByText(/profile assignments unresolved/i)).toBeInTheDocument();
    expect(screen.getByText(/Channel #50000/)).toBeInTheDocument();
    expect(api.getChannelProfiles).not.toHaveBeenCalledWith(recoveryBinding);
  });

  it('does not re-label staged profile IDs when settings refresh to a replacement connection', async () => {
    const read = await api.getSettings();
    vi.mocked(api.getSettings).mockResolvedValue({ ...read, default_channel_profile_ids: [6] });
    render(<App />);
    const editButton = await screen.findByRole('button', { name: /edit mode/i });
    await waitFor(() => expect(editButton).toBeEnabled());
    fireEvent.click(editButton);
    await waitFor(() => expect(testState.channelManagerProps?.isEditMode).toBe(true));
    await act(async () => testState.channelManagerProps!.onCreateChannelManual!('Channel for A', 100));
    vi.mocked(api.getSettings).mockResolvedValue({ ...read, profile_recovery_destination: 'b'.repeat(64), default_channel_profile_ids: [6] });
    await act(async () => testState.onSettingsSaved!());
    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(testState.exitDialogProps?.commitFailure).toMatchObject({
      applied: 0, failed: 0, keepEditing: true,
    }));
    expect(testState.bulkCommit).not.toHaveBeenCalled();
    expect(api.bulkUpdateProfileChannels).not.toHaveBeenCalled();
    expect(sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toContain('Channel for A');

    act(() => (testState.exitDialogProps?.onAcknowledgeFailure as () => void)());
    vi.mocked(api.getSettings).mockResolvedValue({ ...read, default_channel_profile_ids: [6] });
    await act(async () => testState.onSettingsSaved!());
    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(testState.bulkCommit).toHaveBeenCalledTimes(1));
    expect(testState.bulkCommit.mock.calls[0][0]).toMatchObject({ profileRecoveryDestination: profileDestination });
  });

  it('does not infer a channel ID or retire a create whose result never came back', async () => {
    testState.bulkCommit.mockRejectedValue(new Error('Synthetic create result unavailable'));
    vi.mocked(api.getSettings).mockResolvedValue({
      ...await api.getSettings(), default_channel_profile_ids: [6],
    });
    render(<App />);
    const editButton = await screen.findByRole('button', { name: /edit mode/i });
    await waitFor(() => expect(editButton).toBeEnabled());
    fireEvent.click(editButton);
    await waitFor(() => expect(testState.channelManagerProps?.isEditMode).toBe(true));
    await act(async () => testState.channelManagerProps!.onCreateChannelManual!('Unknown create', 100));
    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(testState.exitDialogProps?.commitFailure).toMatchObject({ applied: 0 }));
    expect(sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toContain('Unknown create');
    expect(api.bulkUpdateProfileChannels).not.toHaveBeenCalled();
    expect(screen.queryByText(/profile assignments unresolved/i)).not.toBeInTheDocument();
  });

  it('explicitly retries healthy saved channels and names the one blocked by its missing profile', async () => {
    const blocked = { channelId: 50000, profileIds: [99], binding: recoveryBinding };
    const eligible = { channelId: 50001, profileIds: [6], binding: recoveryBinding };
    sessionStorage.setItem(`ecm.created-channel-profiles.v1:${encodeURIComponent(JSON.stringify(['local#7', '']))}`,
      JSON.stringify([blocked, eligible]));
    const profile = { id: 6, name: 'Synthetic profile', channels: [] as number[] };
    vi.mocked(api.getChannelProfiles).mockResolvedValue([profile]);
    vi.mocked(api.getChannelProfile).mockResolvedValue(profile);
    vi.mocked(api.bulkUpdateProfileChannels).mockImplementation(async (_profile, ids) => {
      profile.channels = ids;
      return { success: true };
    });
    render(<App />);
    await waitFor(() => expect(api.getChannelProfiles).toHaveBeenCalledWith(undefined, profileDestination));
    const retry = await screen.findByRole('button', { name: 'Retry profile assignments' });
    expect(api.bulkUpdateProfileChannels).not.toHaveBeenCalled();
    fireEvent.click(retry);
    expect(await screen.findByText(/Profile assignments unresolved.*1 created channel/)).toBeInTheDocument();
    expect(screen.getByText(/Restore the original profile IDs or set visibility manually/)).toBeInTheDocument();
    expect(screen.getByText('Channel #50000: Profile #99 — missing Profile #99')).toBeInTheDocument();
    expect(api.bulkUpdateProfileChannels).toHaveBeenCalledExactlyOnceWith(6, [50001], true, recoveryBinding);
    expect(testState.bulkCommit).not.toHaveBeenCalled();
  });

  it.each(['success', 'partial', 'undo'] as const)('binds bulk profile intent only to committed IDs after %s', async outcome => {
    const stream = makeStream(1);
    if (outcome === 'undo') {
      vi.mocked(api.getSettings).mockResolvedValue({
        configured: true, default_channel_profile_ids: [99], profile_recovery_destination: profileDestination,
        stream_sort_priority: [], stream_sort_enabled: {}, m3u_account_priorities: {},
      } as unknown as Awaited<ReturnType<typeof api.getSettings>>);
    }
    const profile = { id: 6, name: 'Synthetic profile', channels: [] as number[] };
    vi.mocked(api.getChannelProfiles).mockResolvedValue([profile]);
    vi.mocked(api.getChannelProfile).mockImplementation(async () => ({ ...profile, channels: [...profile.channels] }));
    vi.mocked(api.bulkUpdateProfileChannels).mockImplementation(async (_profile, ids, enabled) => {
      profile.channels = enabled ? [...ids] : [];
      return { success: true };
    });
    const originalCommit = testState.bulkCommit.getMockImplementation()!;
    testState.bulkCommit.mockImplementation(async (...args) => {
      const result = await originalCommit(...args);
      if (outcome === 'partial') {
        result.success = false;
        result.operationsApplied = 1;
        result.operationsFailed = 1;
        result.errors = [{ index: 1, type: 'addStreamToChannel', error: 'Synthetic stream assignment rejected' }];
      }
      const fields = {
        name: 'Created channel', channel_group_id: null, tvg_id: null, tvc_guide_stationid: null,
        epg_data_id: null, streams: [], stream_profile_id: null, uuid: 'synthetic', logo_id: null,
        auto_created: false, auto_created_by: null, auto_created_by_name: null,
      };
      vi.mocked(api.getChannels).mockResolvedValue({
        results: [{ ...fields, id: 50000, channel_number: 999 }, { ...fields, id: 99999, channel_number: 100 }],
        count: 2, next: null, previous: null,
      });
      return result;
    });
    render(<App />);
    const editButton = await screen.findByRole('button', { name: /edit mode/i });
    await waitFor(() => expect(editButton).toBeEnabled());
    fireEvent.click(editButton);
    await waitFor(() => expect(testState.channelManagerProps?.isEditMode).toBe(true));
    if (outcome === 'undo') {
      await act(async () => testState.channelManagerProps!.onCreateChannelManual!('Undone channel', 101));
      act(() => testState.channelManagerProps!.onUndo());
    }
    await act(async () => {
      await testState.channelManagerProps!.onBulkCreateFromGroup(
        [stream], 100, null, undefined, undefined, undefined, false, undefined,
        false, undefined, undefined, undefined, undefined, undefined, undefined,
        [6], false, new NameResolution(new Map([[stream.name, stream.name]]), false),
      );
    });
    fireEvent.click(screen.getByTitle('Apply changes'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(profile.channels).toEqual([50000]));
    expect(vi.mocked(api.bulkUpdateProfileChannels).mock.calls.flatMap(([, ids]) => ids)).not.toContain(99999);
    expect(testState.bulkCommit).toHaveBeenCalledTimes(1);
  });

  it('stages and applies every one-stream channel through the real App callback', async () => {
    const count = 316;
    const streams = Array.from({ length: count }, (_, index) => makeStream(index));
    const nameResolution: ResolvedCreateChannelNames = new NameResolution(
      new Map(streams.map((stream) => [stream.name, stream.name])),
      false,
    );

    render(<StrictMode><App /></StrictMode>);

    const editButton = await screen.findByRole('button', { name: /edit mode/i });
    await waitFor(() => expect(editButton).toBeEnabled());
    fireEvent.click(editButton);
    await waitFor(() => expect(testState.channelManagerProps?.isEditMode).toBe(true));

    await act(async () => {
      await testState.channelManagerProps!.onBulkCreateFromGroup(
        streams,
        1,
        null,
        undefined,
        undefined,
        undefined,
        false,
        undefined,
        false,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
        [],
        false,
        nameResolution,
      );
    });

    await waitFor(() => {
      const raw = sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY);
      expect(raw).not.toBeNull();
      const ledger = JSON.parse(raw!) as PersistedStagedLedger;
      expect(ledger.operations.filter((operation) => operation.apiCall.type === 'createChannel')).toHaveLength(count);
      expect(ledger.operations.filter((operation) => operation.apiCall.type === 'addStreamToChannel')).toHaveLength(count);
      expect(ledger.undoGroups).toHaveLength(1);
      expect(ledger.undoGroups[0]).toHaveLength(count * 2);

      for (let index = 0; index < count; index += 1) {
        const create = ledger.operations[index * 2];
        const assignment = ledger.operations[index * 2 + 1];
        expect(create.apiCall.type).toBe('createChannel');
        expect(create.apiCall).toEqual(expect.objectContaining({
          expectedStreamIds: [streams[index].id],
        }));
        expect(assignment.apiCall).toEqual({
          type: 'addStreamToChannel',
          channelId: create.afterSnapshot[0].id,
          streamId: streams[index].id,
        });
      }
    });

    fireEvent.click(screen.getByTitle('Apply changes'));
    expect(screen.getByText(`${count} channels`)).toBeInTheDocument();
    expect(screen.getByText(`${count} stream assignments`)).toBeInTheDocument();
    expect((testState.exitDialogProps?.summary as EditModeSummary).totalOperations).toBe(count * 2);

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(testState.bulkCommit).toHaveBeenCalledTimes(1));

    const request = testState.bulkCommit.mock.calls[0][0] as BulkCommitRequest;
    expect(request.operations).toHaveLength(count * 2);
    expect(request.operations.filter((operation) => operation.type === 'createChannel')).toHaveLength(count);
    expect(request.operations.filter((operation) => operation.type === 'addStreamToChannel')).toHaveLength(count);
    expect(request.operations.flatMap((operation) =>
      operation.type === 'createChannel' ? [operation.expectedStreamIds] : []
    )).toEqual(streams.map((stream) => [stream.id]));
    const createTempIds = request.operations.flatMap((operation) =>
      operation.type === 'createChannel' ? [operation.tempId!] : []);
    const assignmentTempIds = request.operations.flatMap((operation) =>
      operation.type === 'addStreamToChannel' ? [operation.channelId!] : []);
    expect(new Set(createTempIds).size).toBe(count);
    expect(new Set(assignmentTempIds).size).toBe(count);
    expect(new Set(assignmentTempIds)).toEqual(new Set(createTempIds));
    expect(request.operations.slice(0, 4).map((operation) => operation.type)).toEqual([
      'createChannel',
      'addStreamToChannel',
      'createChannel',
      'addStreamToChannel',
    ]);
    expect(request.continueOnError).toBe(true);
    expect(testState.resolvedAssignmentPairs).toEqual(streams.map((stream, index) => ({
      channelId: 50_000 + index,
      streamId: stream.id,
    })));
    await waitFor(() => expect(testState.exitDialogProps?.isOpen).toBe(false));
    expect(sessionStorage.getItem(STAGED_LEDGER_STORAGE_KEY)).toBeNull();
  });
});
