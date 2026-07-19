/**
 * Targeted render tests for ChannelsPane's selection-action-bar integration
 * (bead enhancedchannelmanager-09x38.17).
 *
 * ChannelsPane has no general-purpose test suite (it's mocked out wherever
 * it's used — see ChannelManagerTab.test.tsx). Like the cleanupLink test,
 * this renders it directly with the minimal prop set needed to prove:
 *  - the floating SelectionActionBar renders while channels are selected in
 *    edit mode, and not otherwise;
 *  - right-clicking a channel row no longer spawns the old custom context
 *    menu (deleted in favor of the bar's More menu);
 *  - the header 'More actions' kebab now carries only pane-level items.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChannelsPane } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import type { Channel, ChannelGroup, ChannelListFilterSettings } from '../types';

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api');
  return { ...actual };
});

function makeFilters(): ChannelListFilterSettings {
  return {
    showEmptyGroups: true,
    showNewlyCreatedGroups: true,
    showProviderGroups: true,
    showManualGroups: true,
    showAutoChannelGroups: true,
  };
}

function makeChannel(id: number, name: string): Channel {
  return {
    id,
    channel_number: id,
    name,
    channel_group_id: null,
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
  };
}

const groups: ChannelGroup[] = [
  { id: 10, name: 'News', channel_count: 0 },
];

function renderPane({
  isEditMode = true,
  selectedChannelIds = new Set<number>(),
}: {
  isEditMode?: boolean;
  selectedChannelIds?: Set<number>;
} = {}) {
  const channels = [makeChannel(1, 'Alpha'), makeChannel(2, 'Beta')];
  return render(
    <NotificationProvider>
      <ChannelsPane
        channelGroups={groups}
        channels={channels}
        streams={[]}
        providers={[]}
        selectedChannelId={null}
        onChannelSelect={vi.fn()}
        onChannelUpdate={vi.fn()}
        onChannelDrop={vi.fn()}
        onBulkStreamDrop={vi.fn()}
        onChannelReorder={vi.fn()}
        onCreateChannel={vi.fn()}
        onDeleteChannel={vi.fn()}
        searchTerm=""
        onSearchChange={vi.fn()}
        selectedGroups={[]}
        onSelectedGroupsChange={vi.fn()}
        loading={false}
        autoRenameChannelNumber={false}
        isEditMode={isEditMode}
        selectedChannelIds={selectedChannelIds}
        onClearChannelSelection={vi.fn()}
        channelListFilters={makeFilters()}
        onChannelListFiltersChange={vi.fn()}
      />
    </NotificationProvider>
  );
}

describe('ChannelsPane selection action bar integration', () => {
  it('renders the floating bar when channels are selected in edit mode', () => {
    renderPane({ selectedChannelIds: new Set([1, 2]) });

    const bar = screen.getByRole('toolbar', { name: 'Selection actions' });
    expect(bar).toBeInTheDocument();
    expect(screen.getByTestId('selection-bar-count')).toHaveTextContent('2 selected');
    // 2 selected → Merge tier present
    expect(screen.getByRole('button', { name: /Merge/ })).toBeInTheDocument();
  });

  it('does not render the bar without a selection or outside edit mode', () => {
    const { unmount } = renderPane({ selectedChannelIds: new Set() });
    expect(screen.queryByRole('toolbar', { name: 'Selection actions' })).not.toBeInTheDocument();
    unmount();

    renderPane({ isEditMode: false, selectedChannelIds: new Set([1]) });
    expect(screen.queryByRole('toolbar', { name: 'Selection actions' })).not.toBeInTheDocument();
  });

  it('right-clicking a channel row spawns no custom context menu', () => {
    const { container } = renderPane({ selectedChannelIds: new Set([1]) });

    // Channels live under the collapsed "Uncategorized" group — expand it.
    const groupHeader = container.querySelector('.group-header');
    expect(groupHeader).not.toBeNull();
    fireEvent.click(groupHeader!);

    const row = container.querySelector('.channel-item');
    expect(row).not.toBeNull();
    fireEvent.contextMenu(row!);

    expect(document.querySelector('.context-menu')).toBeNull();
    expect(document.querySelector('.context-menu-submenu')).toBeNull();
  });

  it('header kebab lists only pane-level items even with a selection', async () => {
    const user = userEvent.setup();
    renderPane({ selectedChannelIds: new Set([1, 2]) });

    // The header kebab (PaneToolbarMenu) — distinct from the bar's More button.
    await user.click(screen.getByRole('button', { name: 'More actions' }));

    const dropdown = document.querySelector('.pane-toolbar-menu-dropdown');
    expect(dropdown).not.toBeNull();
    const labels = Array.from(dropdown!.querySelectorAll('.pane-toolbar-menu-item')).map(
      (el) => el.textContent ?? '',
    );
    // Pane-level items only...
    expect(labels.some((l) => l.includes('Channel Profiles'))).toBe(true);
    expect(labels.some((l) => l.includes('Hidden Groups'))).toBe(true);
    expect(labels.some((l) => l.includes('Sort All Streams'))).toBe(true);
    expect(labels.some((l) => l.includes('Renumber All Groups'))).toBe(true);
    expect(labels.some((l) => l.includes('CSV Template'))).toBe(true);
    expect(labels.some((l) => l.includes('Export CSV'))).toBe(true);
    expect(labels.some((l) => l.includes('Import CSV'))).toBe(true);
    // ...selection-dependent items moved to the SelectionActionBar.
    expect(labels.some((l) => l.includes('Assign EPG'))).toBe(false);
    expect(labels.some((l) => l.includes('Probe Streams'))).toBe(false);
    expect(labels.some((l) => l.includes('Normalize Names'))).toBe(false);
    expect(labels.some((l) => l.includes('Enable in Profile'))).toBe(false);
    expect(labels.some((l) => l.includes('Disable in Profile'))).toBe(false);
  });
});
