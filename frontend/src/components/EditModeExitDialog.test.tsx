/**
 * Tests for the Exit Edit Mode dialog.
 *
 * Two drill findings live here, both from run 2026-08-09-run18:
 *
 *  - bead enhancedchannelmanager-udq1j (second half). A commit the backend
 *    reported as `success=False, applied=11, failed=1` closed this dialog
 *    exactly like a clean one. The Notifications panel afterwards held only
 *    two unrelated "Task Completed: M3U Change Monitor" entries. The operator
 *    was never told a channel had been dropped. A partial failure now takes
 *    over the dialog and has to be acknowledged.
 *
 *  - bead enhancedchannelmanager-75k49. The headline is `summary.totalChanges`,
 *    which the hook derives from the very buckets rendered below it, so
 *    "24 pending changes" over lines summing to 26 is no longer expressible.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EditModeExitDialog } from './EditModeExitDialog';
import type { EditModeSummary } from '../types';

function summaryOf(over: Partial<EditModeSummary> = {}): EditModeSummary {
  const base: EditModeSummary = {
    totalOperations: 0,
    totalChanges: 0,
    channelsModified: 0,
    streamsAdded: 0,
    streamsRemoved: 0,
    streamsReordered: 0,
    channelNumberChanges: 0,
    channelNameChanges: 0,
    epgChanges: 0,
    gracenoteIdChanges: 0,
    logoChanges: 0,
    streamProfileChanges: 0,
    groupMoves: 0,
    otherChannelChanges: 0,
    newChannels: 0,
    deletedChannels: 0,
    newGroups: 0,
    deletedGroups: 0,
    renamedGroups: 0,
    profileVisibilityChanges: 0,
    restoredGroups: 0,
    clearedStreamStats: 0,
    automaticRenames: [],
    operationDetails: [],
  };
  return { ...base, ...over };
}

const handlers = {
  onApply: vi.fn(),
  onDiscard: vi.fn(),
  onKeepEditing: vi.fn(),
  onAcknowledgeFailure: vi.fn(),
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('EditModeExitDialog — pending change summary (bd-75k49)', () => {
  it('quotes the same number the bullet list sums to', () => {
    // The drill's own batch: 12 channels, 12 streams, 2 new groups.
    render(
      <EditModeExitDialog
        isOpen
        summary={summaryOf({
          totalOperations: 24,
          totalChanges: 26,
          newChannels: 12,
          streamsAdded: 12,
          newGroups: 2,
        })}
        {...handlers}
      />
    );

    expect(screen.getByText(/You have 26 pending changes:/)).toBeInTheDocument();
    const lines = screen.getAllByRole('listitem').map((li) => li.textContent ?? '');
    const total = lines
      .map((line) => parseInt(line.trim(), 10))
      .reduce((sum, n) => sum + n, 0);
    expect(total).toBe(26);
  });

  it('names a logo change, a stream profile change and a group move', () => {
    render(
      <EditModeExitDialog
        isOpen
        summary={summaryOf({
          totalOperations: 3,
          totalChanges: 3,
          logoChanges: 1,
          streamProfileChanges: 1,
          groupMoves: 1,
        })}
        {...handlers}
      />
    );

    expect(screen.getByText(/1 logo change/)).toBeInTheDocument();
    expect(screen.getByText(/1 stream profile change/)).toBeInTheDocument();
    expect(screen.getByText(/1 channel moved to another group/)).toBeInTheDocument();
  });
});

describe('EditModeExitDialog — a commit that did not fully apply (bd-udq1j)', () => {
  const partial = {
    applied: 11,
    failed: 1,
    messages: ['TX | Dallas | PBS KERA: Channel creation failed: 400 - Invalid pk "-1000"'],
  };

  it('tells the operator what failed instead of closing silently', () => {
    render(
      <EditModeExitDialog
        isOpen
        summary={summaryOf()}
        commitFailure={partial}
        {...handlers}
      />
    );

    expect(screen.getByRole('heading', { name: /some changes were not applied/i })).toBeInTheDocument();
    expect(screen.getByRole('alert').textContent).toMatch(/11 operations applied/);
    expect(screen.getByRole('alert').textContent).toMatch(/1 failed/);
    expect(screen.getByText(/TX \| Dallas \| PBS KERA/)).toBeInTheDocument();
  });

  it('offers only an acknowledgement — not Apply All again', () => {
    render(
      <EditModeExitDialog
        isOpen
        summary={summaryOf({ totalOperations: 24, totalChanges: 26, newChannels: 12 })}
        commitFailure={partial}
        {...handlers}
      />
    );

    expect(screen.queryByRole('button', { name: /apply all/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /discard/i })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(handlers.onAcknowledgeFailure).toHaveBeenCalledTimes(1);
  });

  it('cannot be dismissed with Escape', () => {
    render(
      <EditModeExitDialog
        isOpen
        summary={summaryOf()}
        commitFailure={partial}
        {...handlers}
      />
    );

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(handlers.onKeepEditing).not.toHaveBeenCalled();
    expect(screen.getByRole('heading', { name: /some changes were not applied/i })).toBeInTheDocument();
  });

  it('says nothing applied when every operation failed', () => {
    render(
      <EditModeExitDialog
        isOpen
        summary={summaryOf()}
        commitFailure={{ applied: 0, failed: 3, messages: ['Validation failed'] }}
        {...handlers}
      />
    );

    expect(screen.getByRole('heading', { name: /^changes were not applied$/i })).toBeInTheDocument();
    expect(screen.getByRole('alert').textContent).toMatch(/0 operations applied/);
  });

  it('stays out of the way of a clean commit', () => {
    render(
      <EditModeExitDialog
        isOpen
        summary={summaryOf({ totalOperations: 2, totalChanges: 2, newChannels: 2 })}
        commitFailure={null}
        {...handlers}
      />
    );

    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('button', { name: /apply all/i })).toBeInTheDocument();
  });
});

/**
 * Bead enhancedchannelmanager-ic884.5: a channel name the numbering changed is
 * a change the operator did not type, so it has to be visible — with its
 * before and after — before Apply, not merely counted among the name changes.
 */
describe('automatic renames', () => {
  it('shows every rename a numbering change caused, with before and after', () => {
    render(
      <EditModeExitDialog
        isOpen
        summary={summaryOf({
          totalChanges: 4,
          channelNumberChanges: 2,
          channelNameChanges: 2,
          automaticRenames: [
            { channelId: 1, from: '5 | ESPN', to: '9 | ESPN', fromNumber: 5, toNumber: 9 },
            { channelId: 2, from: '6 | TNT', to: '10 | TNT', fromNumber: 6, toNumber: 10 },
          ],
        })}
        commitFailure={null}
        {...handlers}
      />
    );

    const preview = screen.getByTestId('automatic-rename-preview');
    expect(preview.textContent).toContain('5 | ESPN');
    expect(preview.textContent).toContain('9 | ESPN');
    expect(preview.textContent).toContain('6 | TNT');
    expect(preview.textContent).toContain('10 | TNT');
  });

  it('says nothing when the numbering changed no names', () => {
    render(
      <EditModeExitDialog
        isOpen
        summary={summaryOf({ totalChanges: 1, channelNumberChanges: 1 })}
        commitFailure={null}
        {...handlers}
      />
    );

    expect(screen.queryByTestId('automatic-rename-preview')).toBeNull();
  });
});
