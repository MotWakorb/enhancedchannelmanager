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
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChannelsPane } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import { tabUntil } from '../test/utils/keyboardNav';
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

describe('ChannelsPane keyboard-operable selection (bead enhancedchannelmanager-s8xpd)', () => {
  // Follow-on from bead zwhw4's StreamsPane review: the per-channel row
  // selector (ChannelListItem's channel-select-indicator) and the group
  // header select-all (ChannelsPane's group-checkbox) were bare clickable
  // <span>s -- not focusable, no aria-checked/aria-pressed, no keyboard
  // handler. A keyboard-only user could not select channels, which gated
  // every selection-dependent action including the SelectionActionBar
  // (bead 09x38.17). These tests need real selection state (not the fixed
  // `selectedChannelIds` prop the other tests in this file pass), so they
  // wire the same controlled-selection contract App.tsx uses.
  function StatefulPane({ selectAllGroup = false }: { selectAllGroup?: boolean }) {
    const [selectedChannelIds, setSelectedChannelIds] = useState<Set<number>>(new Set());
    const channels = [makeChannel(1, 'Alpha')];

    const handleToggle = (channelId: number, addToSelection: boolean) => {
      setSelectedChannelIds((prev) => {
        const next = new Set(prev);
        if (addToSelection) {
          if (next.has(channelId)) next.delete(channelId);
          else next.add(channelId);
        } else {
          next.clear();
          next.add(channelId);
        }
        return next;
      });
    };

    const handleSelectGroupChannels = (channelIds: number[], select: boolean) => {
      setSelectedChannelIds((prev) => {
        const next = new Set(prev);
        channelIds.forEach((id) => (select ? next.add(id) : next.delete(id)));
        return next;
      });
    };

    return (
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
          isEditMode={true}
          selectedChannelIds={selectedChannelIds}
          onToggleChannelSelection={handleToggle}
          onClearChannelSelection={() => setSelectedChannelIds(new Set())}
          onSelectGroupChannels={selectAllGroup ? handleSelectGroupChannels : undefined}
          channelListFilters={makeFilters()}
          onChannelListFiltersChange={vi.fn()}
        />
      </NotificationProvider>
    );
  }

  it('renders the channel row selector as a semantic checkbox exposing aria-checked state', () => {
    render(<StatefulPane />);
    // Channels live under the collapsed "Uncategorized" group -- expand it.
    fireEvent.click(document.querySelector('.group-header')!);

    const selector = screen.getByRole('checkbox', { name: 'Select channel Alpha' });
    expect(selector.tagName).toBe('BUTTON');
    expect(selector).not.toBeChecked();

    fireEvent.click(selector);
    expect(selector).toBeChecked();

    fireEvent.click(selector);
    expect(selector).not.toBeChecked();
  });

  it('renders the group select-all as a semantic tri-state checkbox exposing aria-checked state', () => {
    render(<StatefulPane selectAllGroup={true} />);
    fireEvent.click(document.querySelector('.group-header')!);

    const groupSelector = screen.getByRole('checkbox', { name: 'Select all channels in group' });
    expect(groupSelector.tagName).toBe('BUTTON');
    expect(groupSelector).toHaveAttribute('aria-checked', 'false');

    fireEvent.click(groupSelector);
    expect(screen.getByRole('checkbox', { name: 'Deselect all channels in group' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });

  it('renders the group select-all as aria-checked="mixed" when only some channels in the group are selected', () => {
    // Round-2 review of bead enhancedchannelmanager-s8xpd's PR: the first
    // pass rendered the indeterminate glyph for a partial selection but
    // announced it identically to "none selected" via a boolean
    // aria-pressed. Two channels, one selected, proves the tri-state
    // contract distinguishes "some" from "none" and from "all".
    function TwoChannelStatefulPane() {
      const [selectedChannelIds, setSelectedChannelIds] = useState<Set<number>>(new Set([1]));
      const channels = [makeChannel(1, 'Alpha'), makeChannel(2, 'Beta')];

      const handleSelectGroupChannels = (channelIds: number[], select: boolean) => {
        setSelectedChannelIds((prev) => {
          const next = new Set(prev);
          channelIds.forEach((id) => (select ? next.add(id) : next.delete(id)));
          return next;
        });
      };

      return (
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
            isEditMode={true}
            selectedChannelIds={selectedChannelIds}
            onToggleChannelSelection={vi.fn()}
            onClearChannelSelection={() => setSelectedChannelIds(new Set())}
            onSelectGroupChannels={handleSelectGroupChannels}
            channelListFilters={makeFilters()}
            onChannelListFiltersChange={vi.fn()}
          />
        </NotificationProvider>
      );
    }

    render(<TwoChannelStatefulPane />);
    fireEvent.click(document.querySelector('.group-header')!);

    const groupSelector = screen.getByRole('checkbox', { name: 'Select all channels in group' });
    expect(groupSelector).toHaveAttribute('aria-checked', 'mixed');
    expect(groupSelector).toHaveClass('indeterminate');
  });

  it('reaches and activates the group select-all via Tab + Space, zero pointer events', async () => {
    const user = userEvent.setup();
    render(<StatefulPane selectAllGroup={true} />);
    fireEvent.click(document.querySelector('.group-header')!);

    const groupSelector = screen.getByRole('checkbox', { name: 'Select all channels in group' });
    expect(groupSelector).toHaveAttribute('aria-checked', 'false');

    // Tab from the top of the document to the group selector -- no pointer
    // involved. Proves the button is in the real tab order (a sibling of
    // the expand/collapse toggle, not nested inside it), not just
    // programmatically focusable.
    await tabUntil(user, () => document.activeElement === groupSelector, { max: 200 });

    await user.keyboard(' ');
    expect(screen.getByRole('checkbox', { name: 'Deselect all channels in group' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });

  it('supports the full keyboard-only selection flow: Tab to the row selector (real tab order), Space selects, reach and activate a SelectionActionBar action, zero pointer events', async () => {
    const user = userEvent.setup();
    render(<StatefulPane />);
    // Expanding the group to reveal the row is setup, not part of the
    // keyboard-selection proof -- same convention as StreamsPane's zwhw4
    // tests, which click "Expand all groups" before the keyboard-only part.
    fireEvent.click(document.querySelector('.group-header')!);

    // Tab from the top of the document to the row selector -- no pointer
    // involved. Proves the button is in the real tab order, not just
    // programmatically focusable.
    const selector = screen.getByRole('checkbox', { name: 'Select channel Alpha' });
    await tabUntil(user, () => document.activeElement === selector, { max: 200 });
    expect(selector).not.toBeChecked();

    // Space toggles the selection and updates aria-checked; the floating
    // SelectionActionBar mounts now that a channel is selected.
    await user.keyboard(' ');
    expect(selector).toBeChecked();
    const bar = screen.getByRole('toolbar', { name: 'Selection actions' });
    expect(bar).toBeInTheDocument();

    // Keep tabbing forward (still from the selector, still keyboard-only)
    // until the bar's "Clear selection" button receives focus, then
    // activate it with Enter.
    const clearBtn = screen.getByRole('button', { name: 'Clear selection' });
    await tabUntil(user, () => document.activeElement === clearBtn, { max: 200 });
    await user.keyboard('{Enter}');

    // Selection cleared -- the bar unmounts, proving the action fired via
    // a pure keyboard path (Tab + Space + Enter, no click/pointer events).
    expect(screen.queryByRole('toolbar', { name: 'Selection actions' })).not.toBeInTheDocument();
  });
});
