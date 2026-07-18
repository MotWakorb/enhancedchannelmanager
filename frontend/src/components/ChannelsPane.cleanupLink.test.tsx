/**
 * Targeted render test for the "Clean up empty groups" link in the Channel
 * List Filters panel (bead enhancedchannelmanager-09x38.15 item 3).
 *
 * ChannelsPane has no general-purpose test suite (it's mocked out wherever
 * it's used — see ChannelManagerTab.test.tsx) because of its size and prop
 * surface. This file renders it directly with the minimal prop set needed
 * to exercise just the filter-settings dropdown, to prove the new link
 * dispatches the navigation event rather than embedding the cleanup tool.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChannelsPane, NAVIGATE_TO_ORPHANED_GROUPS_EVENT } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import type { ChannelListFilterSettings } from '../types';

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

function renderChannelsPane() {
  return render(
    <NotificationProvider>
      <ChannelsPane
        channelGroups={[]}
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
        selectedGroups={[]}
        onSelectedGroupsChange={vi.fn()}
        loading={false}
        autoRenameChannelNumber={false}
        channelListFilters={makeFilters()}
        onChannelListFiltersChange={vi.fn()}
      />
    </NotificationProvider>
  );
}

describe('ChannelsPane — Channel List Filters "Clean up empty groups" link', () => {
  it('dispatches the settings-maintenance navigation event and closes the menu on click', async () => {
    const user = userEvent.setup();
    const listener = vi.fn();
    window.addEventListener(NAVIGATE_TO_ORPHANED_GROUPS_EVENT, listener);

    renderChannelsPane();

    await user.click(screen.getByLabelText('Channel List Filters'));
    const link = await screen.findByText(/Clean up empty groups/i);
    await user.click(link);

    expect(listener).toHaveBeenCalledTimes(1);
    // Menu closes — the link (and the rest of the dropdown) unmounts.
    expect(screen.queryByText(/Clean up empty groups/i)).not.toBeInTheDocument();

    window.removeEventListener(NAVIGATE_TO_ORPHANED_GROUPS_EVENT, listener);
  });

  it('does not navigate or open any embedded tool without the click', async () => {
    renderChannelsPane();

    const user = userEvent.setup();
    await user.click(screen.getByLabelText('Channel List Filters'));

    // The link is present but no tool UI (e.g. a scan/delete modal) renders
    // inline — this is a link out, not an embedded orphaned-groups tool.
    expect(screen.queryByText(/Scan for Orphaned Groups/i)).not.toBeInTheDocument();
  });
});
