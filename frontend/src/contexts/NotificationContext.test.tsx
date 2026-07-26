/**
 * Tests for the toast notification provider (bead
 * enhancedchannelmanager-fi3dq).
 *
 * The M3U Digest retry storm surfaced two provider-level guarantees the
 * app relies on when any caller misbehaves:
 *
 *   - equivalent notifications (same type + title + message) are
 *     deduplicated -- repeat callers get the existing toast's id back
 *     instead of stacking a duplicate;
 *   - the visible toast count stays capped at maxVisible, with the
 *     remainder collapsed into a non-interactive overflow indicator, so
 *     dismiss controls can never stack past the viewport.
 *
 * Layer: component wiring (provider + real ToastContainer/Toast rendered
 * via @testing-library).
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useEffect } from 'react';
import { act, render, screen } from '@testing-library/react';
import { NotificationProvider, useNotifications } from './NotificationContext';

type NotificationsApi = ReturnType<typeof useNotifications>;

let notifications: NotificationsApi;

function Capture() {
  const api = useNotifications();
  // Assign in an effect, not during render (react-hooks/globals).
  useEffect(() => {
    notifications = api;
  }, [api]);
  return null;
}

function renderProvider(maxVisible = 5) {
  return render(
    <NotificationProvider maxVisible={maxVisible}>
      <Capture />
    </NotificationProvider>
  );
}

describe('NotificationProvider — dedup and visible cap (fi3dq)', () => {
  beforeEach(() => {
    notifications = undefined as unknown as NotificationsApi;
  });

  it('deduplicates equivalent notifications and returns the existing toast id', () => {
    renderProvider();

    let firstId = '';
    let secondId = '';
    act(() => {
      firstId = notifications.error('Failed to load digest settings', 'Digest Settings');
    });
    act(() => {
      secondId = notifications.error('Failed to load digest settings', 'Digest Settings');
    });

    expect(secondId).toBe(firstId);
    expect(screen.getAllByText('Failed to load digest settings')).toHaveLength(1);
  });

  it('deduplicates equivalent notifications fired within the same tick', () => {
    renderProvider();

    act(() => {
      for (let i = 0; i < 10; i++) {
        notifications.error('Failed to load digest settings', 'Digest Settings');
      }
    });

    expect(screen.getAllByText('Failed to load digest settings')).toHaveLength(1);
  });

  it('does not deduplicate notifications that differ in message, title, or type', () => {
    renderProvider();

    act(() => {
      notifications.error('Failed to load digest settings', 'Digest Settings');
      notifications.error('Failed to load digest settings', 'Save Failed');
      notifications.warning('Failed to load digest settings', 'Digest Settings');
      notifications.error('Connection refused', 'Digest Settings');
    });

    expect(screen.getAllByRole('alert')).toHaveLength(4);
  });

  it('allows an equivalent notification again after the original is dismissed', () => {
    renderProvider();

    let firstId = '';
    act(() => {
      firstId = notifications.error('Failed to load digest settings', 'Digest Settings');
    });
    act(() => {
      notifications.dismiss(firstId);
    });
    expect(screen.queryByText('Failed to load digest settings')).not.toBeInTheDocument();

    let secondId = '';
    act(() => {
      secondId = notifications.error('Failed to load digest settings', 'Digest Settings');
    });

    expect(secondId).not.toBe(firstId);
    expect(screen.getAllByText('Failed to load digest settings')).toHaveLength(1);
  });

  it('caps visible toasts at maxVisible and collapses the rest into an overflow indicator', () => {
    renderProvider(5);

    act(() => {
      for (let i = 0; i < 8; i++) {
        notifications.error(`Distinct failure ${i}`, 'Errors');
      }
    });

    expect(screen.getAllByRole('alert')).toHaveLength(5);
    const overflow = screen.getByText('+3 more notifications');
    expect(overflow).toBeInTheDocument();
    // The overflow indicator is informational only -- no focusable control
    // that could receive keyboard focus offscreen.
    expect(overflow.querySelector('button')).toBeNull();
  });
});
