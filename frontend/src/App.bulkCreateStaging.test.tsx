import { StrictMode, type ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChannelManagerTabProps } from './components/tabs/ChannelManagerTab';
import type { BulkCommitRequest, BulkCommitResponse, ResolvedCreateChannelNames } from './services/api';
import type { EditModeSummary, Stream } from './types';
import { STAGED_LEDGER_STORAGE_KEY, type PersistedStagedLedger } from './utils/stagedLedgerStorage';

const testState = vi.hoisted(() => ({
  channelManagerProps: null as ChannelManagerTabProps | null,
  exitDialogProps: null as Record<string, unknown> | null,
  bulkCommit: vi.fn(),
  resolvedAssignmentPairs: [] as Array<{ channelId: number; streamId: number }>,
}));

vi.mock('./hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 7, auth_provider: 'local', username: 'operator', is_admin: true },
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
    getEPGSources: vi.fn().mockResolvedValue([]),
    getEPGData: vi.fn().mockResolvedValue([]),
    getM3UStreamMetadata: vi.fn().mockResolvedValue({ count: 0, metadata: {} }),
    bulkCommit: (request: BulkCommitRequest, batchId?: string) => testState.bulkCommit(request, batchId),
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
    testState.channelManagerProps = null;
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
        groupIdMap: {},
      } satisfies BulkCommitResponse;
    });
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
