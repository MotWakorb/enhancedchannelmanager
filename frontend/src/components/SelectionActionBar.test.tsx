/**
 * Tests for the floating selection action bar (bead 09x38.17).
 *
 * Covers the visible button tier (Merge gating on 2+ selection), the
 * upward-opening '⋮ More' menu with its Move / Selection / Profiles
 * sections, keyboard navigation, and the Escape-clears-selection contract.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SelectionActionBar } from './SelectionActionBar';

function makeProps(overrides: Partial<React.ComponentProps<typeof SelectionActionBar>> = {}) {
  return {
    selectedCount: 1,
    onDelete: vi.fn(),
    onProbe: vi.fn(),
    onFindDuplicates: vi.fn(),
    onRenumber: vi.fn(),
    onAssignEPG: vi.fn(),
    onMerge: vi.fn(),
    onClear: vi.fn(),
    groups: [
      { id: 10, name: 'News' },
      { id: 20, name: 'Sports' },
    ],
    onMoveToGroup: vi.fn(),
    onNewGroup: vi.fn(),
    onNormalize: vi.fn(),
    onSetLogoFromM3U: vi.fn(),
    onSetLogoFromEPG: vi.fn(),
    onSortStreams: vi.fn(),
    onFetchGracenote: vi.fn(),
    profiles: [{ id: 1, name: 'Living Room' }],
    onSetProfileVisibility: vi.fn(),
    ...overrides,
  };
}

function renderBar(overrides: Partial<React.ComponentProps<typeof SelectionActionBar>> = {}) {
  const props = makeProps(overrides);
  const utils = render(<SelectionActionBar {...props} />);
  return { props, ...utils };
}

async function openMoreMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'More selection actions' }));
  return screen.getByRole('menu', { name: 'More selection actions' });
}

describe('SelectionActionBar', () => {
  it('shows the selection count and hides Merge for a single selection', () => {
    renderBar({ selectedCount: 1 });

    expect(screen.getByTestId('selection-bar-count')).toHaveTextContent('1 selected');
    expect(screen.getByRole('button', { name: /Delete/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Probe/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Find Duplicates/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Renumber/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Assign EPG/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Merge/ })).not.toBeInTheDocument();
  });

  it('shows Merge once two or more channels are selected', () => {
    renderBar({ selectedCount: 2 });

    expect(screen.getByTestId('selection-bar-count')).toHaveTextContent('2 selected');
    expect(screen.getByRole('button', { name: /Merge/ })).toBeInTheDocument();
  });

  it('renders nothing when the selection is empty', () => {
    renderBar({ selectedCount: 0 });

    expect(screen.queryByRole('toolbar', { name: 'Selection actions' })).not.toBeInTheDocument();
  });

  it('fires the matching callback for each top-tier button', async () => {
    const user = userEvent.setup();
    const { props } = renderBar({ selectedCount: 2 });

    await user.click(screen.getByRole('button', { name: /Delete/ }));
    await user.click(screen.getByRole('button', { name: /Probe/ }));
    await user.click(screen.getByRole('button', { name: /Find Duplicates/ }));
    await user.click(screen.getByRole('button', { name: /Renumber/ }));
    await user.click(screen.getByRole('button', { name: /Assign EPG/ }));
    await user.click(screen.getByRole('button', { name: /Merge/ }));
    await user.click(screen.getByRole('button', { name: 'Clear selection' }));

    expect(props.onDelete).toHaveBeenCalledTimes(1);
    expect(props.onProbe).toHaveBeenCalledTimes(1);
    expect(props.onFindDuplicates).toHaveBeenCalledTimes(1);
    expect(props.onRenumber).toHaveBeenCalledTimes(1);
    expect(props.onAssignEPG).toHaveBeenCalledTimes(1);
    expect(props.onMerge).toHaveBeenCalledTimes(1);
    expect(props.onClear).toHaveBeenCalledTimes(1);
  });

  it('disables Probe while a probe is running', () => {
    renderBar({ probing: true });

    expect(screen.getByRole('button', { name: /Probing/ })).toBeDisabled();
  });

  it('clears the selection on Escape when no menu is open', async () => {
    const user = userEvent.setup();
    const { props } = renderBar();

    await user.keyboard('{Escape}');

    expect(props.onClear).toHaveBeenCalledTimes(1);
  });

  it('does not clear the selection on Escape while a modal is open', async () => {
    const user = userEvent.setup();
    const { props } = renderBar();
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    document.body.appendChild(overlay);

    await user.keyboard('{Escape}');

    expect(props.onClear).not.toHaveBeenCalled();
    overlay.remove();
  });

  it('Escape closes the More menu without clearing the selection', async () => {
    const user = userEvent.setup();
    const { props } = renderBar();
    await openMoreMenu(user);

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('menu', { name: 'More selection actions' })).not.toBeInTheDocument();
    expect(props.onClear).not.toHaveBeenCalled();
    // Focus returns to the trigger so keyboard users are not dropped.
    expect(screen.getByRole('button', { name: 'More selection actions' })).toHaveFocus();
  });

  it('lists Uncategorized, every group, and New group in the Move submenu', async () => {
    const user = userEvent.setup();
    renderBar();
    await openMoreMenu(user);

    await user.click(screen.getByRole('menuitem', { name: /Move to group/ }));

    const submenu = screen.getByRole('menu', { name: 'Move to group' });
    const labels = Array.from(submenu.querySelectorAll('[role="menuitem"]')).map(
      (el) => el.textContent?.replace(/^(folder_off|folder|create_new_folder)/, ''),
    );
    expect(labels).toEqual(['Uncategorized', 'News', 'Sports', 'New group…']);
  });

  it('moving to a group invokes onMoveToGroup with the group id and closes the menu', async () => {
    const user = userEvent.setup();
    const { props } = renderBar();
    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Move to group/ }));

    await user.click(screen.getByRole('menuitem', { name: /Sports/ }));

    expect(props.onMoveToGroup).toHaveBeenCalledWith(20);
    expect(screen.queryByRole('menu', { name: 'More selection actions' })).not.toBeInTheDocument();
  });

  it('moving to Uncategorized passes null and New group invokes onNewGroup', async () => {
    const user = userEvent.setup();
    const { props } = renderBar();
    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Move to group/ }));
    await user.click(screen.getByRole('menuitem', { name: /Uncategorized/ }));
    expect(props.onMoveToGroup).toHaveBeenCalledWith(null);

    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Move to group/ }));
    await user.click(screen.getByRole('menuitem', { name: /New group/ }));
    expect(props.onNewGroup).toHaveBeenCalledTimes(1);
  });

  it('fires the Selection-section actions', async () => {
    const user = userEvent.setup();
    const { props } = renderBar();

    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Normalize Names/ }));
    expect(props.onNormalize).toHaveBeenCalledTimes(1);

    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Set Logo from M3U/ }));
    expect(props.onSetLogoFromM3U).toHaveBeenCalledTimes(1);

    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Set Logo from EPG/ }));
    expect(props.onSetLogoFromEPG).toHaveBeenCalledTimes(1);

    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Fetch Gracenote IDs/ }));
    expect(props.onFetchGracenote).toHaveBeenCalledTimes(1);
  });

  it('offers only enabled sort criteria plus Smart Sort and reports the chosen mode', async () => {
    const user = userEvent.setup();
    const { props } = renderBar({
      sortEnabledCriteria: {
        resolution: true,
        bitrate: false,
        framerate: false,
        m3u_priority: false,
        audio_channels: false,
        custom_streams: false,
      },
    });
    await openMoreMenu(user);

    await user.click(screen.getByRole('menuitem', { name: /Sort Streams/ }));

    const submenu = screen.getByRole('menu', { name: 'Sort streams' });
    expect(submenu.querySelectorAll('[role="menuitem"]')).toHaveLength(2);
    expect(screen.getByRole('menuitem', { name: /Smart Sort/ })).toBeInTheDocument();
    await user.click(screen.getByRole('menuitem', { name: /By Resolution/ }));
    expect(props.onSortStreams).toHaveBeenCalledWith('resolution');
  });

  it('Profile visibility flyout enables and disables per profile', async () => {
    const user = userEvent.setup();
    const { props } = renderBar();
    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Profile visibility/ }));

    await user.click(screen.getByRole('menuitem', { name: 'Enable selected channels in profile Living Room' }));
    expect(props.onSetProfileVisibility).toHaveBeenCalledWith(1, true);

    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Profile visibility/ }));
    await user.click(screen.getByRole('menuitem', { name: 'Disable selected channels in profile Living Room' }));
    expect(props.onSetProfileVisibility).toHaveBeenCalledWith(1, false);
  });

  it('hides the Profiles section when there are no profiles', async () => {
    const user = userEvent.setup();
    renderBar({ profiles: [] });
    await openMoreMenu(user);

    expect(screen.queryByRole('menuitem', { name: /Profile visibility/ })).not.toBeInTheDocument();
  });

  it('supports arrow-key navigation and ArrowRight/ArrowLeft submenu traversal', async () => {
    const user = userEvent.setup();
    renderBar();
    await openMoreMenu(user);

    // Menu opens with focus on the first item.
    const moveItem = screen.getByRole('menuitem', { name: /Move to group/ });
    await waitFor(() => expect(moveItem).toHaveFocus());

    // ArrowDown walks to the next item.
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: /Normalize Names/ })).toHaveFocus();

    // ArrowUp walks back.
    await user.keyboard('{ArrowUp}');
    expect(moveItem).toHaveFocus();

    // ArrowRight opens the submenu and focuses its first item.
    await user.keyboard('{ArrowRight}');
    await waitFor(() =>
      expect(screen.getByRole('menuitem', { name: /Uncategorized/ })).toHaveFocus(),
    );

    // ArrowLeft closes the submenu and restores focus to the trigger.
    await user.keyboard('{ArrowLeft}');
    expect(screen.queryByRole('menu', { name: 'Move to group' })).not.toBeInTheDocument();
    expect(moveItem).toHaveFocus();
  });
});
