/**
 * Group-header "Group actions" menu tests (bead enhancedchannelmanager-o88e9).
 *
 * The three-dot menu is the only place ECM offers Rename Group and Delete
 * Group. It used to be gated on the group having channels, so an empty group
 * (including one ECM itself had just created) could not be renamed or
 * deleted without leaving for Dispatcharr's UI.
 *
 * These tests pin the split: the menu is available for an empty group and
 * carries the member-independent actions, while the member-dependent actions
 * (Probe Group, Sort Streams, Sort & Renumber) stay out of it. They also pin
 * that a provider-backed group never offers Delete Group, empty or not.
 *
 * Like the other ChannelsPane suites, this renders the pane directly with the
 * minimal prop set. ChannelsPane has no general-purpose test suite.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChannelsPane } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import type {
  Channel,
  ChannelGroup,
  ChannelListFilterSettings,
  M3UGroupSetting,
} from '../types';

const MANUAL_GROUP_ID = 10;
const PROVIDER_GROUP_ID = 20;

function makeFilters(): ChannelListFilterSettings {
  return {
    showEmptyGroups: true,
    showNewlyCreatedGroups: true,
    showProviderGroups: true,
    showManualGroups: true,
    showAutoChannelGroups: true,
  };
}

function makeChannel(id: number, name: string, groupId: number | null): Channel {
  return {
    id,
    channel_number: id,
    name,
    channel_group_id: groupId,
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
  { id: MANUAL_GROUP_ID, name: 'Drill Empty', channel_count: 0 },
  { id: PROVIDER_GROUP_ID, name: 'Provider Empty', channel_count: 0 },
];

function renderPane(
  paneOverrides: Partial<React.ComponentProps<typeof ChannelsPane>> = {},
) {
  return render(
    <NotificationProvider>
      <ChannelsPane
        channelGroups={groups}
        channels={[]}
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
        selectedGroups={[MANUAL_GROUP_ID]}
        onSelectedGroupsChange={vi.fn()}
        loading={false}
        autoRenameChannelNumber={false}
        isEditMode
        selectedChannelIds={new Set<number>()}
        onClearChannelSelection={vi.fn()}
        channelListFilters={makeFilters()}
        onChannelListFiltersChange={vi.fn()}
        {...paneOverrides}
      />
    </NotificationProvider>,
  );
}

/** Open the Group actions menu on the group header whose name matches. */
async function openGroupMenu(container: HTMLElement, groupName: string) {
  const user = userEvent.setup();
  const header = Array.from(container.querySelectorAll('.group-header')).find((el) =>
    el.querySelector('.group-name')?.textContent?.includes(groupName),
  );
  expect(header, `no group header for "${groupName}"`).toBeTruthy();
  const trigger = header!.querySelector<HTMLButtonElement>('.group-menu-btn');
  expect(trigger, `no Group actions button on "${groupName}"`).toBeTruthy();
  await user.click(trigger!);
  const dropdown = document.querySelector('.group-menu-dropdown');
  expect(dropdown).not.toBeNull();
  return Array.from(dropdown!.querySelectorAll('.group-menu-item')).map(
    (el) => el.textContent ?? '',
  );
}

describe('ChannelsPane group actions menu', () => {
  it('exposes Group actions on an empty group in edit mode', () => {
    const { container } = renderPane();

    const header = Array.from(container.querySelectorAll('.group-header')).find((el) =>
      el.querySelector('.group-name')?.textContent?.includes('Drill Empty'),
    );
    expect(header).toBeTruthy();
    expect(header!.querySelector('.group-empty-badge')).toHaveTextContent('Empty');
    const trigger = header!.querySelector('.group-menu-btn');
    expect(trigger).toHaveAttribute('aria-label', 'Group actions');
  });

  it('offers Rename Group and Delete Group on an empty manual group', async () => {
    const { container } = renderPane();

    const labels = await openGroupMenu(container, 'Drill Empty');
    expect(labels.some((l) => l.includes('Rename Group'))).toBe(true);
    expect(labels.some((l) => l.includes('Delete Group'))).toBe(true);
  });

  it('omits the member-dependent actions on an empty group', async () => {
    const { container } = renderPane();

    const labels = await openGroupMenu(container, 'Drill Empty');
    expect(labels.some((l) => l.includes('Probe Group'))).toBe(false);
    expect(labels.some((l) => l.includes('Sort Streams'))).toBe(false);
    expect(labels.some((l) => l.includes('Sort & Renumber'))).toBe(false);
  });

  it('opens the delete confirmation for an empty group', async () => {
    const user = userEvent.setup();
    const { container } = renderPane();

    await openGroupMenu(container, 'Drill Empty');
    await user.click(screen.getByText('Delete Group'));

    expect(screen.getByRole('heading', { name: 'Delete Group' })).toBeInTheDocument();
  });

  it('keeps the member-dependent actions on a populated group', async () => {
    const { container } = renderPane({
      channels: [makeChannel(1, 'Alpha', MANUAL_GROUP_ID)],
      channelGroups: [{ id: MANUAL_GROUP_ID, name: 'Drill Locals', channel_count: 1 }],
    });

    const labels = await openGroupMenu(container, 'Drill Locals');
    expect(labels.some((l) => l.includes('Probe Group'))).toBe(true);
    expect(labels.some((l) => l.includes('Sort Streams'))).toBe(true);
    expect(labels.some((l) => l.includes('Sort & Renumber'))).toBe(true);
    expect(labels.some((l) => l.includes('Rename Group'))).toBe(true);
    expect(labels.some((l) => l.includes('Delete Group'))).toBe(true);
  });

  it('never offers Delete Group on a provider-backed group, empty or not', async () => {
    const providerGroupSettings = {
      [PROVIDER_GROUP_ID]: {
        enabled: true,
        auto_channel_sync: false,
      } as unknown as M3UGroupSetting,
    };
    const { container } = renderPane({
      selectedGroups: [PROVIDER_GROUP_ID],
      providerGroupSettings,
    });

    const labels = await openGroupMenu(container, 'Provider Empty');
    expect(labels.some((l) => l.includes('Delete Group'))).toBe(false);
    expect(labels.some((l) => l.includes('Rename Group'))).toBe(true);
  });

  it('keeps Group actions out of normal (non-edit) mode', () => {
    const { container } = renderPane({ isEditMode: false });

    expect(container.querySelector('.group-menu-btn')).toBeNull();
  });
});
