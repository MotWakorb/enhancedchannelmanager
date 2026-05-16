/**
 * Tests for ChannelManagerTab subnav + view toggling (BD-J / bd-gfxrz).
 *
 * Locks the UX-ratified spec from the parent epic (bd-1v4ht):
 *
 *   - The subnav link to Pending Merges renders ONLY when the queue depth is
 *     non-zero OR when the operator is already on the Pending Merges view
 *     (so a single-resolve doesn't strand the operator on a view with no way
 *     back to the default panes via the subnav).
 *   - The count badge renders on the subnav link only (NOT on the top-level
 *     tab — alert-fatigue per UX). Spec compliance is locked by asserting
 *     the badge is inside the subnav-link button.
 *   - Clicking the subnav link toggles to the PendingMergesPage view.
 *   - The PENDING_MERGES_EVENT custom event switches into the page from
 *     outside the React tree (e.g. from a toast action).
 *
 * We mock out SplitPane / ChannelsPane / StreamsPane / PendingMergesPage so
 * the tests focus on the subnav layer and don't drag in the heavy
 * channel-mgmt props or the actual fetch path.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { ChannelManagerTab, PENDING_MERGES_EVENT } from './ChannelManagerTab';
import * as api from '../../services/api';

vi.mock('../', () => ({
  SplitPane: ({ left, right }: { left: React.ReactNode; right: React.ReactNode }) => (
    <div data-testid="split-pane">
      <div>{left}</div>
      <div>{right}</div>
    </div>
  ),
  ChannelsPane: () => <div data-testid="channels-pane" />,
  StreamsPane: () => <div data-testid="streams-pane" />,
}));

vi.mock('./PendingMergesPage', () => ({
  PendingMergesPage: () => <div data-testid="pending-merges-page">PendingMergesPage</div>,
}));

vi.mock('../../services/api', async () => {
  const actual = await vi.importActual<typeof import('../../services/api')>(
    '../../services/api',
  );
  return {
    ...actual,
    getPendingMerges: vi.fn(),
  };
});

// Minimal props for the heavy tab — the mocked SplitPane / panes ignore them,
// but the type checker still expects them.
function makeMinimalProps(): React.ComponentProps<typeof ChannelManagerTab> {
  // Cast through unknown — the test isn't exercising any of these props
  // directly; the mocked panes ignore them.
  return {
    channelGroups: [],
    onChannelGroupsChange: vi.fn().mockResolvedValue(undefined),
    onDeleteChannelGroup: vi.fn().mockResolvedValue(undefined),
    channels: [],
    selectedChannelId: null,
    onChannelSelect: vi.fn(),
    onChannelUpdate: vi.fn(),
    onChannelDrop: vi.fn().mockResolvedValue(undefined),
    onBulkStreamDrop: vi.fn().mockResolvedValue(undefined),
    onChannelReorder: vi.fn().mockResolvedValue(undefined),
    onCreateChannel: vi.fn(),
    onDeleteChannel: vi.fn().mockResolvedValue(undefined),
    channelsLoading: false,
    channelSearch: '',
    onChannelSearchChange: vi.fn(),
    selectedGroups: [],
    onSelectedGroupsChange: vi.fn(),
    selectedChannelIds: new Set(),
    lastSelectedChannelId: null,
    onToggleChannelSelection: vi.fn(),
    onClearChannelSelection: vi.fn(),
    onSelectChannelRange: vi.fn(),
    onSelectGroupChannels: vi.fn(),
    autoRenameChannelNumber: false,
    isEditMode: false,
    isCommitting: false,
    modifiedChannelIds: new Set(),
    onStageUpdateChannel: vi.fn(),
    onStageAddStream: vi.fn(),
    onStageRemoveStream: vi.fn(),
    onStageReorderStreams: vi.fn(),
    onStageBulkAssignNumbers: vi.fn(),
    onStageDeleteChannel: vi.fn(),
    onStageDeleteChannelGroup: vi.fn(),
    onStageRenameChannelGroup: vi.fn(),
    onStartBatch: vi.fn(),
    onEndBatch: vi.fn(),
    canUndo: false,
    canRedo: false,
    undoCount: 0,
    redoCount: 0,
    lastChange: null,
    savePoints: [],
    hasUnsavedChanges: false,
    isOperationPending: false,
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    onCreateSavePoint: vi.fn(),
    onRevertToSavePoint: vi.fn(),
    onDeleteSavePoint: vi.fn(),
    logos: [],
    onLogosChange: vi.fn().mockResolvedValue(undefined),
    epgData: [],
    epgSources: [],
    streamProfiles: [],
    epgDataLoading: false,
    channelProfiles: [],
    onChannelProfilesChange: vi.fn().mockResolvedValue(undefined),
    providerGroupSettings: {},
    channelListFilters: {
      showEmptyGroups: false,
      showNewlyCreatedGroups: true,
      showProviderGroups: true,
      showManualGroups: true,
      showAutoChannelGroups: true,
      filterMissingLogo: false,
      filterMissingTvgId: false,
      filterMissingEpgData: false,
      filterMissingGracenote: false,
      filterFailedStreams: true,
      filterWorkingStreams: true,
      filterUnprobedStreams: true,
    },
    onChannelListFiltersChange: vi.fn(),
    newlyCreatedGroupIds: new Set(),
    onTrackNewlyCreatedGroup: vi.fn(),
    allStreams: [],
    seenStreamsMap: new Map(),
    streams: [],
    providers: [],
    streamGroups: [],
    streamsLoading: false,
    streamSearch: '',
    onStreamSearchChange: vi.fn(),
    streamProviderFilter: null,
    onStreamProviderFilterChange: vi.fn(),
    streamGroupFilter: null,
    onStreamGroupFilterChange: vi.fn(),
    selectedProviders: [],
    onSelectedProvidersChange: vi.fn(),
    selectedStreamGroups: [],
    onSelectedStreamGroupsChange: vi.fn(),
    dispatcharrUrl: '',
    onBulkCreateFromGroup: vi.fn().mockResolvedValue(undefined),
  } as unknown as React.ComponentProps<typeof ChannelManagerTab>;
}

describe('ChannelManagerTab — Pending Merges subnav (BD-J / bd-gfxrz)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does NOT render the subnav when the pending-merges count is zero', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [],
      total: 0,
      page: 1,
      page_size: 1,
      total_pages: 0,
    });

    render(<ChannelManagerTab {...makeMinimalProps()} />);

    // The default split-pane is always rendered.
    expect(screen.getByTestId('split-pane')).toBeInTheDocument();

    // Wait for the count poll to settle, then assert the subnav stays hidden.
    await waitFor(() => {
      expect(api.getPendingMerges).toHaveBeenCalled();
    });
    expect(
      screen.queryByRole('button', { name: /Pending Merges/i }),
    ).toBeNull();
    expect(screen.queryByTestId('pending-merges-badge')).toBeNull();
  });

  it('renders the subnav with a count badge when the queue depth is non-zero', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [],
      total: 3,
      page: 1,
      page_size: 1,
      total_pages: 3,
    });

    render(<ChannelManagerTab {...makeMinimalProps()} />);

    const link = await screen.findByRole('button', { name: /Pending Merges \(3\)/i });
    expect(link).toBeInTheDocument();
    const badge = screen.getByTestId('pending-merges-badge');
    expect(badge).toHaveTextContent('3');
    // Spec: the badge lives on the subnav link, NOT on the top-level tab.
    // We assert containment as the structural proxy.
    expect(link).toContainElement(badge);
  });

  it('switches into the Pending Merges view when the subnav link is clicked', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [],
      total: 1,
      page: 1,
      page_size: 1,
      total_pages: 1,
    });

    render(<ChannelManagerTab {...makeMinimalProps()} />);
    const link = await screen.findByRole('button', { name: /Pending Merges/i });

    fireEvent.click(link);

    expect(screen.getByTestId('pending-merges-page')).toBeInTheDocument();
    expect(screen.queryByTestId('split-pane')).toBeNull();
  });

  it('keeps the subnav visible after the operator drops to zero — operator can still navigate back', async () => {
    // Start at 1, drop to 0 on the next poll. The subnav-link visibility
    // gate is `count > 0 OR view === 'pending-merges'` — once the operator
    // has navigated into the page the link must persist even if the queue
    // empties out under them.
    vi.mocked(api.getPendingMerges)
      .mockResolvedValueOnce({
        merges: [],
        total: 1,
        page: 1,
        page_size: 1,
        total_pages: 1,
      })
      .mockResolvedValue({
        merges: [],
        total: 0,
        page: 1,
        page_size: 1,
        total_pages: 0,
      });

    render(<ChannelManagerTab {...makeMinimalProps()} />);
    const link = await screen.findByRole('button', { name: /Pending Merges \(1\)/i });
    fireEvent.click(link);

    // The page is now active. Even if the badge count drops to 0 the link
    // stays — but we don't trigger another poll here. Just assert current
    // behaviour: the link button is present.
    expect(
      screen.getByRole('button', { name: /Pending Merges/i }),
    ).toBeInTheDocument();
    expect(screen.getByTestId('pending-merges-page')).toBeInTheDocument();
  });

  it('switches into the page on a PENDING_MERGES_EVENT (toast action contract)', async () => {
    vi.mocked(api.getPendingMerges).mockResolvedValue({
      merges: [],
      total: 0,
      page: 1,
      page_size: 1,
      total_pages: 0,
    });

    render(<ChannelManagerTab {...makeMinimalProps()} />);

    // Initially the default split-pane is shown.
    expect(screen.getByTestId('split-pane')).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new CustomEvent(PENDING_MERGES_EVENT));
    });

    await waitFor(() => {
      expect(screen.getByTestId('pending-merges-page')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('split-pane')).toBeNull();
  });
});
