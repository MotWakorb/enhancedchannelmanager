/**
 * Tests for the floating selection action bar (bead 09x38.17).
 *
 * Covers the visible button tier (Merge gating on 2+ selection), the
 * upward-opening '⋮ More' menu with its Move / Selection / Profiles
 * sections, keyboard navigation, and the Escape-clears-selection contract.
 */
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

    const submenu = screen.getByRole('dialog', { name: 'Move to group' });
    const labels = Array.from(submenu.querySelectorAll('.selection-bar-menu-item--sub')).map(
      (el) => el.textContent?.replace(/^(folder_off|folder|create_new_folder)/, ''),
    );
    expect(labels).toEqual(['Uncategorized', 'News', 'Sports', 'New group…']);
  });

  it('moving to a group invokes onMoveToGroup with the group id and closes the menu', async () => {
    const user = userEvent.setup();
    const { props } = renderBar();
    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Move to group/ }));

    await user.click(screen.getByRole('button', { name: /Sports/ }));

    expect(props.onMoveToGroup).toHaveBeenCalledWith(20);
    expect(screen.queryByRole('menu', { name: 'More selection actions' })).not.toBeInTheDocument();
  });

  it('moving to Uncategorized passes null and New group invokes onNewGroup', async () => {
    const user = userEvent.setup();
    const { props } = renderBar();
    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Move to group/ }));
    await user.click(screen.getByRole('button', { name: /Uncategorized/ }));
    expect(props.onMoveToGroup).toHaveBeenCalledWith(null);

    await openMoreMenu(user);
    await user.click(screen.getByRole('menuitem', { name: /Move to group/ }));
    await user.click(screen.getByRole('button', { name: /New group/ }));
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

    // ArrowDown to reach the Sort Streams submenu trigger (a plain menuitem
    // submenu, unaffected by the Move filter input — see the dedicated
    // "Move-to-group type-to-filter" describe block below for that
    // submenu's ArrowRight-focuses-the-input behavior): Normalize Names,
    // Set Logo from M3U, Set Logo from EPG, Sort Streams.
    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}');
    const sortItem = screen.getByRole('menuitem', { name: /Sort Streams/ });
    expect(sortItem).toHaveFocus();

    // ArrowRight opens the submenu and focuses its first item.
    await user.keyboard('{ArrowRight}');
    await waitFor(() =>
      expect(screen.getByRole('menuitem', { name: /Smart Sort/ })).toHaveFocus(),
    );

    // ArrowLeft closes the submenu and restores focus to the trigger.
    await user.keyboard('{ArrowLeft}');
    expect(screen.queryByRole('menu', { name: 'Sort streams' })).not.toBeInTheDocument();
    expect(sortItem).toHaveFocus();
  });

  describe('Move-to-group type-to-filter (bead hzzcv)', () => {
    function manyGroups(count: number) {
      return Array.from({ length: count }, (_, i) => ({ id: i + 1, name: `Group ${i + 1}` }));
    }

    async function openMoveSubmenu(user: ReturnType<typeof userEvent.setup>) {
      await openMoreMenu(user);
      await user.click(screen.getByRole('menuitem', { name: /Move to group/ }));
      return screen.getByRole('dialog', { name: 'Move to group' });
    }

    it('focuses the filter input as soon as the submenu opens (click)', async () => {
      const user = userEvent.setup();
      renderBar();

      await openMoveSubmenu(user);

      await waitFor(() => expect(screen.getByRole('textbox', { name: 'Filter groups' })).toHaveFocus());
    });

    it('focuses the filter input when the submenu opens via ArrowRight, not the first menu item', async () => {
      const user = userEvent.setup();
      renderBar();
      await openMoreMenu(user);
      await waitFor(() => expect(screen.getByRole('menuitem', { name: /Move to group/ })).toHaveFocus());

      await user.keyboard('{ArrowRight}');

      await waitFor(() => expect(screen.getByRole('textbox', { name: 'Filter groups' })).toHaveFocus());
    });

    it('narrows the list to groups whose name contains the query, case-insensitively', async () => {
      const user = userEvent.setup();
      renderBar({ groups: [{ id: 1, name: 'Sports HD' }, { id: 2, name: 'News' }, { id: 3, name: 'Kids Sport' }] });
      await openMoveSubmenu(user);

      await user.type(screen.getByRole('textbox', { name: 'Filter groups' }), 'SPORT');

      expect(screen.getByRole('button', { name: /Sports HD/ })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Kids Sport/ })).toBeInTheDocument();
      expect(screen.queryByRole('menuitem', { name: /^News$/ })).not.toBeInTheDocument();
      // Pinned entries stay reachable regardless of the filter text.
      expect(screen.getByRole('button', { name: /Uncategorized/ })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /New group/ })).toBeInTheDocument();
    });

    it('shows an empty-state message when the filter matches no groups, and clears it on Clear', async () => {
      const user = userEvent.setup();
      renderBar({ groups: [{ id: 1, name: 'Sports' }, { id: 2, name: 'News' }] });
      await openMoveSubmenu(user);

      await user.type(screen.getByRole('textbox', { name: 'Filter groups' }), 'zzz-nomatch');

      expect(screen.getByText('No groups match "zzz-nomatch"')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Sports/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /News/ })).not.toBeInTheDocument();
      // Pinned entries still reachable even with a zero-match filter.
      expect(screen.getByRole('button', { name: /Uncategorized/ })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /New group/ })).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: 'Clear group filter' }));

      expect(screen.getByRole('textbox', { name: 'Filter groups' })).toHaveValue('');
      expect(screen.queryByText('No groups match "zzz-nomatch"')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Sports/ })).toBeInTheDocument();
    });

    it('ArrowDown from the filter input moves focus into the filtered results, and Enter activates the focused group', async () => {
      const user = userEvent.setup();
      const { props } = renderBar({ groups: manyGroups(50) });
      await openMoveSubmenu(user);
      const input = screen.getByRole('textbox', { name: 'Filter groups' });
      await waitFor(() => expect(input).toHaveFocus());

      // "Group 23" is a unique substring among "Group 1".."Group 50" (no
      // "Group 230" etc. exists), isolating a single filtered result so
      // focus movement is deterministic.
      await user.type(input, 'Group 23');
      expect(screen.getAllByRole('button', { name: /^Group 23$/ })).toHaveLength(1);

      // ArrowDown from the input lands on the first reachable item
      // (Uncategorized is pinned ahead of the filtered results).
      await user.keyboard('{ArrowDown}');
      expect(screen.getByRole('button', { name: /Uncategorized/ })).toHaveFocus();

      // Arrow further down onto the sole filtered group and activate it.
      await user.keyboard('{ArrowDown}');
      expect(screen.getByRole('button', { name: /^Group 23$/ })).toHaveFocus();
      await user.keyboard('{Enter}');
      expect(props.onMoveToGroup).toHaveBeenCalledWith(23);
    });

    it('keeps ArrowUp and ArrowDown scoped to the Move-to-group submenu boundaries', async () => {
      const user = userEvent.setup();
      renderBar({ groups: [{ id: 1, name: 'Sports' }] });
      await openMoveSubmenu(user);
      const input = screen.getByRole('textbox', { name: 'Filter groups' });
      const firstItem = screen.getByRole('button', { name: /Uncategorized/ });
      const lastItem = screen.getByRole('button', { name: /New group/ });
      await waitFor(() => expect(input).toHaveFocus());

      await user.keyboard('{ArrowDown}');
      expect(firstItem).toHaveFocus();

      await user.keyboard('{ArrowUp}');
      expect(input).toHaveFocus();

      lastItem.focus();
      await user.keyboard('{ArrowDown}');
      expect(lastItem).toHaveFocus();
    });

    it('preserves normal Left, Right, Home, and End text editing in the filter input', async () => {
      const user = userEvent.setup();
      renderBar();
      await openMoveSubmenu(user);
      const input = screen.getByRole('textbox', { name: 'Filter groups' }) as HTMLInputElement;
      await user.type(input, 'Sports');

      // jsdom does not implement the browser's default caret movement, so
      // verify directly that none of these native editing events is canceled.
      for (const key of ['ArrowLeft', 'ArrowRight', 'Home', 'End']) {
        expect(fireEvent.keyDown(input, { key })).toBe(true);
      }
      expect(input).toHaveFocus();
      expect(screen.getByRole('dialog', { name: 'Move to group' })).toBeInTheDocument();
    });

    it('announces filtered and zero-result counts politely', async () => {
      const user = userEvent.setup();
      renderBar({ groups: [{ id: 1, name: 'Sports HD' }, { id: 2, name: 'News' }, { id: 3, name: 'Kids Sport' }] });
      await openMoveSubmenu(user);
      const input = screen.getByRole('textbox', { name: 'Filter groups' });
      const status = screen.getByRole('status');

      await user.type(input, 'sport');
      expect(status).toHaveTextContent('2 groups found');

      await user.clear(input);
      await user.type(input, 'zzz');
      expect(status).toHaveTextContent('No groups found');
    });

    it('makes the clear control keyboard reachable and restores focus to the input after clearing', async () => {
      const user = userEvent.setup();
      renderBar();
      await openMoveSubmenu(user);
      const input = screen.getByRole('textbox', { name: 'Filter groups' });
      await user.type(input, 'Sports');

      await user.tab();
      const clearButton = screen.getByRole('button', { name: 'Clear group filter' });
      expect(clearButton).toHaveFocus();

      await user.keyboard('{Enter}');
      expect(input).toHaveValue('');
      expect(input).toHaveFocus();
    });

    it('contains chooser navigation and Escape when the clear control has focus', async () => {
      const user = userEvent.setup();
      renderBar();
      await openMoveSubmenu(user);
      const input = screen.getByRole('textbox', { name: 'Filter groups' });
      await user.type(input, 'Sports');
      await user.tab();
      const clearButton = screen.getByRole('button', { name: 'Clear group filter' });

      await user.keyboard('{ArrowUp}');
      expect(input).toHaveFocus();
      clearButton.focus();
      await user.keyboard('{ArrowDown}');
      expect(screen.getByRole('button', { name: /Uncategorized/ })).toHaveFocus();
      clearButton.focus();
      await user.keyboard('{Home}');
      expect(input).toHaveFocus();
      clearButton.focus();
      await user.keyboard('{End}');
      expect(screen.getByRole('button', { name: /New group/ })).toHaveFocus();

      clearButton.focus();
      await user.keyboard('{Escape}');
      expect(screen.queryByRole('dialog', { name: 'Move to group' })).not.toBeInTheDocument();
      expect(screen.getByRole('menuitem', { name: /Move to group/ })).toHaveFocus();
    });

    it('supports Space to clear and Escape from a destination', async () => {
      const user = userEvent.setup();
      renderBar();
      await openMoveSubmenu(user);
      const input = screen.getByRole('textbox', { name: 'Filter groups' });
      await user.type(input, 'Sports');
      await user.tab();
      await user.keyboard(' ');
      expect(input).toHaveValue('');
      expect(input).toHaveFocus();

      await user.keyboard('{ArrowDown}');
      expect(screen.getByRole('button', { name: /Uncategorized/ })).toHaveFocus();
      await user.keyboard('{Escape}');
      expect(screen.queryByRole('dialog', { name: 'Move to group' })).not.toBeInTheDocument();
      expect(screen.getByRole('menuitem', { name: /Move to group/ })).toHaveFocus();
    });

    it('Escape closes the submenu (not the whole menu, not the selection) and returns focus to the trigger', async () => {
      const user = userEvent.setup();
      const { props } = renderBar();
      await openMoveSubmenu(user);
      const input = screen.getByRole('textbox', { name: 'Filter groups' });
      await waitFor(() => expect(input).toHaveFocus());
      await user.type(input, 'Sports');

      await user.keyboard('{Escape}');

      expect(screen.queryByRole('dialog', { name: 'Move to group' })).not.toBeInTheDocument();
      // The outer More menu is still open — Escape only closed the submenu.
      expect(screen.getByRole('menu', { name: 'More selection actions' })).toBeInTheDocument();
      expect(props.onClear).not.toHaveBeenCalled();
      expect(screen.getByRole('menuitem', { name: /Move to group/ })).toHaveFocus();
    });

    it('resets the filter when the submenu is closed and reopened', async () => {
      const user = userEvent.setup();
      renderBar({ groups: [{ id: 1, name: 'Sports' }, { id: 2, name: 'News' }] });
      await openMoveSubmenu(user);
      await user.type(screen.getByRole('textbox', { name: 'Filter groups' }), 'Sports');
      expect(screen.queryByRole('button', { name: /News/ })).not.toBeInTheDocument();

      await user.keyboard('{Escape}'); // closes submenu only
      await user.click(screen.getByRole('menuitem', { name: /Move to group/ }));

      expect(screen.getByRole('textbox', { name: 'Filter groups' })).toHaveValue('');
      expect(screen.getByRole('button', { name: /News/ })).toBeInTheDocument();
    });
  });
});
