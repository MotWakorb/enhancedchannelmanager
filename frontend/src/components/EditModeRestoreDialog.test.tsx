/**
 * The offer an operator sees on returning to a tab that still holds staged
 * Edit Mode work from a session that died (epic enhancedchannelmanager-r93hq).
 *
 * WHY AN OFFER AND NOT AN AUTOMATIC RESTORE. The staged operations name
 * channel ids, group ids and stream ids that could have moved while the
 * session was dead, so some of them may no longer be applicable at all.
 * Restoring silently would put the operator back into Edit Mode holding a
 * ledger that is quietly smaller than the one they left, with no way to know
 * which changes went or why — and the next thing they do is press Apply. The
 * dialog exists to make the account unmissable BEFORE any of it becomes live
 * staged work.
 *
 * SO THE ACCOUNT IS NOT COLLAPSIBLE AND NOT TRUNCATED. Every dropped operation
 * gets a line naming what it was and what moved. A "3 changes could not be
 * restored" summary behind a disclosure triangle would satisfy a presence
 * assertion and fail the operator.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EditModeRestoreDialog } from './EditModeRestoreDialog';
import type { StagedOperation } from '../types';
import type { DroppedLedgerOperation } from '../utils/stagedLedgerStorage';

function operation(id: string, description: string): StagedOperation {
  return {
    id,
    timestamp: 1,
    description,
    apiCall: { type: 'deleteChannel', channelId: 1 },
    beforeSnapshot: [],
    afterSnapshot: [],
  };
}

const DROPPED: DroppedLedgerOperation[] = [
  {
    id: 'op-9',
    type: 'updateChannel',
    description: 'Renumber "Gone HD"',
    reason: 'channel-missing',
    detail: 'Channel "Gone HD" (id 404) no longer exists.',
  },
  {
    id: 'op-10',
    type: 'reorderChannelStreams',
    description: 'Reorder streams on "Alpha"',
    reason: 'stream-detached',
    detail: 'The streams on channel "Alpha" (id 1) changed, so this reordering would drop or invent one.',
  },
];

const SAVED_AT = new Date('2026-08-16T09:30:00Z').getTime();

function renderDialog(overrides: Partial<React.ComponentProps<typeof EditModeRestoreDialog>> = {}) {
  const onRestore = vi.fn();
  const onDiscard = vi.fn();
  render(
    <EditModeRestoreDialog
      isOpen
      savedAt={SAVED_AT}
      restorable={[operation('op-1', 'Rename "Alpha"'), operation('op-2', 'Delete "Bravo"')]}
      dropped={[]}
      onRestore={onRestore}
      onDiscard={onDiscard}
      {...overrides}
    />,
  );
  return { onRestore, onDiscard };
}

describe('EditModeRestoreDialog', () => {
  it('renders nothing when closed', () => {
    renderDialog({ isOpen: false });
    expect(screen.queryByTestId('edit-mode-restore-dialog')).toBeNull();
  });

  it('says how many staged changes are waiting and offers to restore them', () => {
    const { onRestore } = renderDialog();
    expect(screen.getByTestId('edit-mode-restore-dialog')).toBeTruthy();

    const restore = screen.getByRole('button', { name: /restore 2 changes/i });
    expect((restore as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(restore);
    expect(onRestore).toHaveBeenCalledTimes(1);
  });

  it('names the previous session rather than presenting the work as current', () => {
    renderDialog();
    // The operator has to be able to tell this is old work. The dialog says so
    // in words AND carries the machine-readable timestamp.
    expect(screen.getByTestId('edit-mode-restore-when').getAttribute('dateTime'))
      .toBe(new Date(SAVED_AT).toISOString());
    expect(screen.getByTestId('edit-mode-restore-dialog').textContent)
      .toMatch(/previous session/i);
  });

  it('lists EVERY operation that cannot be restored, with the reason', () => {
    renderDialog({ dropped: DROPPED });

    const account = screen.getByTestId('edit-mode-restore-dropped');
    expect(account.querySelectorAll('li')).toHaveLength(2);
    for (const entry of DROPPED) {
      expect(account.textContent).toContain(entry.description);
      expect(account.textContent).toContain(entry.detail);
    }
    // The count is stated too, so a long list cannot be misread as a short one.
    expect(screen.getByTestId('edit-mode-restore-dialog').textContent)
      .toMatch(/2 changes can no longer be applied/i);
  });

  it('shows no dropped-operation account when nothing was dropped', () => {
    renderDialog();
    expect(screen.queryByTestId('edit-mode-restore-dropped')).toBeNull();
  });

  it('offers no restore at all when nothing survived, and says why', () => {
    const { onDiscard } = renderDialog({ restorable: [], dropped: DROPPED });

    expect(screen.queryByRole('button', { name: /restore/i })).toBeNull();
    expect(screen.getByTestId('edit-mode-restore-dialog').textContent)
      .toMatch(/none of your staged changes can be restored/i);

    fireEvent.click(screen.getByRole('button', { name: /discard/i }));
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it('cannot be dismissed by Escape or by clicking away', () => {
    // Both would discard staged work by accident, which is the exact class of
    // failure the epic's exit guard exists to stop. The only ways out are the
    // two buttons.
    const { onRestore, onDiscard } = renderDialog();
    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.click(screen.getByTestId('edit-mode-restore-dialog').parentElement!);
    expect(onRestore).not.toHaveBeenCalled();
    expect(onDiscard).not.toHaveBeenCalled();
    expect(screen.getByTestId('edit-mode-restore-dialog')).toBeTruthy();
  });

  it('discarding is labelled as destroying the work, not as "cancel"', () => {
    renderDialog();
    const discard = screen.getByRole('button', { name: /discard/i });
    expect(discard.textContent).toMatch(/discard/i);
    expect(screen.queryByRole('button', { name: /^cancel$/i })).toBeNull();
  });
});
