/**
 * Tests for StreamCreateMenu (bead enhancedchannelmanager-zwhw4).
 *
 * The "Create in…" dropdown replaces the Streams pane's hand-rolled
 * document.body right-click context menu. These tests lock in capability
 * parity (pick an enabled channel group / create in a new group), the
 * type-to-filter chooser, keyboard navigation, and the Escape contract
 * (close panel + refocus trigger, never bubbling to document listeners).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StreamCreateMenu, type StreamCreateMenuGroup } from './StreamCreateMenu';

const GROUPS: StreamCreateMenuGroup[] = [
  { id: 1, name: 'News' },
  { id: 2, name: 'Sports' },
  { id: 3, name: 'Sports Plus' },
];

function renderMenu(overrides: Partial<React.ComponentProps<typeof StreamCreateMenu>> = {}) {
  const onCreateInGroup = vi.fn();
  const onCreateInNewGroup = vi.fn();
  render(
    <StreamCreateMenu
      groups={GROUPS}
      onCreateInGroup={onCreateInGroup}
      onCreateInNewGroup={onCreateInNewGroup}
      {...overrides}
    />
  );
  return { onCreateInGroup, onCreateInNewGroup };
}

function getTrigger() {
  return screen.getByRole('button', { name: 'Create in…' });
}

describe('StreamCreateMenu', () => {
  it('opens the chooser listing every enabled channel group plus the pinned new-group action', async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(getTrigger());

    const panel = screen.getByRole('dialog', { name: /create channels in group/i });
    expect(panel).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'News' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sports' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sports Plus' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create in new group…' })).toBeInTheDocument();
    expect(getTrigger()).toHaveAttribute('aria-expanded', 'true');
  });

  it('fires onCreateInGroup with the chosen group id and closes the panel', async () => {
    const user = userEvent.setup();
    const { onCreateInGroup } = renderMenu();

    await user.click(getTrigger());
    await user.click(screen.getByRole('button', { name: 'Sports' }));

    expect(onCreateInGroup).toHaveBeenCalledTimes(1);
    expect(onCreateInGroup).toHaveBeenCalledWith(2);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('fires onCreateInNewGroup from the pinned action and closes the panel', async () => {
    const user = userEvent.setup();
    const { onCreateInNewGroup, onCreateInGroup } = renderMenu();

    await user.click(getTrigger());
    await user.click(screen.getByRole('button', { name: 'Create in new group…' }));

    expect(onCreateInNewGroup).toHaveBeenCalledTimes(1);
    expect(onCreateInGroup).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('filters groups case-insensitively and announces the match count', async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(getTrigger());
    await user.type(screen.getByRole('textbox', { name: /filter groups/i }), 'sport');

    expect(screen.queryByRole('button', { name: 'News' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sports' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sports Plus' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('2 groups found');
    // The pinned new-group action stays reachable at any filter state.
    expect(screen.getByRole('button', { name: 'Create in new group…' })).toBeInTheDocument();
  });

  it('shows a no-match message when the filter excludes every group', async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(getTrigger());
    await user.type(screen.getByRole('textbox', { name: /filter groups/i }), 'zzz');

    expect(screen.getByText('No groups match "zzz"')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('No groups found');
  });

  it('is keyboard reachable: ArrowDown from the filter focuses the first group, Enter activates it', async () => {
    const user = userEvent.setup();
    const { onCreateInGroup } = renderMenu();

    await user.click(getTrigger());
    const filterInput = screen.getByRole('textbox', { name: /filter groups/i });
    // Panel auto-focuses the filter input on open.
    await waitFor(() => expect(filterInput).toHaveFocus());

    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('button', { name: 'News' })).toHaveFocus();

    await user.keyboard('{ArrowDown}{ArrowDown}{ArrowDown}');
    expect(screen.getByRole('button', { name: 'Create in new group…' })).toHaveFocus();

    await user.keyboard('{ArrowUp}{ArrowUp}');
    expect(screen.getByRole('button', { name: 'Sports' })).toHaveFocus();

    await user.keyboard('{Enter}');
    expect(onCreateInGroup).toHaveBeenCalledWith(2);
  });

  it('Escape closes the panel, refocuses the trigger, and does not reach document listeners', async () => {
    const user = userEvent.setup();
    const documentEscapeSpy = vi.fn();
    const listener = (e: KeyboardEvent) => {
      if (e.key === 'Escape') documentEscapeSpy();
    };
    document.addEventListener('keydown', listener);
    try {
      renderMenu();
      await user.click(getTrigger());
      await waitFor(() =>
        expect(screen.getByRole('textbox', { name: /filter groups/i })).toHaveFocus()
      );

      await user.keyboard('{Escape}');

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(getTrigger()).toHaveFocus();
      // The selection-clearing document shortcut must never see this Escape.
      expect(documentEscapeSpy).not.toHaveBeenCalled();
    } finally {
      document.removeEventListener('keydown', listener);
    }
  });

  it('closes when clicking outside the trigger and panel', async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(getTrigger());
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows the empty state without a filter input when no groups are enabled, keeping New group reachable', async () => {
    const user = userEvent.setup();
    const { onCreateInNewGroup } = renderMenu({ groups: [] });

    await user.click(getTrigger());

    expect(screen.getByText('No enabled channel groups')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Create in new group…' }));
    expect(onCreateInNewGroup).toHaveBeenCalledTimes(1);
  });
});
