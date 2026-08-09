/**
 * Unit tests for the cross-component server-data invalidation channel
 * (bead enhancedchannelmanager-5z7c9).
 *
 * The three staleness instances in drill run 9 share one shape: the component
 * that performs a write is not the component that renders the result, and
 * there was no way for the first to tell the second its copy is stale. This
 * module is that way. The contract it has to hold:
 *
 *   - a publish reaches every subscriber of that key, and only that key;
 *   - a subscriber that unmounts stops being called (no leak, no setState on
 *     an unmounted tree);
 *   - the reload callback invoked is the LATEST one the component rendered,
 *     not the one captured when the subscription was set up — otherwise a
 *     reload closing over stale props/state silently refetches the wrong page;
 *   - publishing with nobody listening is a no-op, not a throw. A mutation
 *     must never fail because the panel that displays it happens to be
 *     unmounted.
 *
 * There is deliberately no timer, no polling and no refetch-on-focus here.
 * Invalidation is driven by mutations only.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, act } from '@testing-library/react';
import {
  invalidateServerData,
  useServerDataInvalidation,
  type ServerDataKey,
} from './useServerDataInvalidation';

function Subscriber({ dataKey, reload }: { dataKey: ServerDataKey; reload: () => void }) {
  useServerDataInvalidation(dataKey, reload);
  return null;
}

describe('useServerDataInvalidation', () => {
  it('calls a subscriber when its key is invalidated', () => {
    const reload = vi.fn();
    render(<Subscriber dataKey="saved-backups" reload={reload} />);

    act(() => invalidateServerData('saved-backups'));

    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('calls every subscriber of the same key', () => {
    const first = vi.fn();
    const second = vi.fn();
    render(
      <>
        <Subscriber dataKey="logos" reload={first} />
        <Subscriber dataKey="logos" reload={second} />
      </>,
    );

    act(() => invalidateServerData('logos'));

    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it('does not call subscribers of a different key', () => {
    const logoReload = vi.fn();
    const settingsReload = vi.fn();
    render(
      <>
        <Subscriber dataKey="logos" reload={logoReload} />
        <Subscriber dataKey="settings" reload={settingsReload} />
      </>,
    );

    act(() => invalidateServerData('settings'));

    expect(settingsReload).toHaveBeenCalledTimes(1);
    expect(logoReload).not.toHaveBeenCalled();
  });

  it('stops calling a subscriber once it unmounts', () => {
    const reload = vi.fn();
    const { unmount } = render(<Subscriber dataKey="logos" reload={reload} />);

    unmount();
    act(() => invalidateServerData('logos'));

    expect(reload).not.toHaveBeenCalled();
  });

  it('invokes the latest reload callback, not the one captured at subscribe time', () => {
    const stale = vi.fn();
    const fresh = vi.fn();
    const { rerender } = render(<Subscriber dataKey="logos" reload={stale} />);

    rerender(<Subscriber dataKey="logos" reload={fresh} />);
    act(() => invalidateServerData('logos'));

    expect(stale).not.toHaveBeenCalled();
    expect(fresh).toHaveBeenCalledTimes(1);
  });

  it('is a no-op when nothing is listening', () => {
    expect(() => invalidateServerData('saved-backups')).not.toThrow();
  });

  /**
   * The channel LIST is a separate key from the channel-GROUP list, and both
   * have to be published by a restore. Drill run 2026-08-09-run18 applied a
   * restore that reported "Channels 12 CREATED"; the group filter refreshed
   * correctly and the pane behind it still read "CHANNELS 0 / Empty", because
   * only `channel-groups` was ever published (bead
   * enhancedchannelmanager-eelgi).
   */
  it('delivers channels and channel-groups independently', () => {
    const channelsReload = vi.fn();
    const groupsReload = vi.fn();
    render(
      <>
        <Subscriber dataKey="channels" reload={channelsReload} />
        <Subscriber dataKey="channel-groups" reload={groupsReload} />
      </>,
    );

    act(() => invalidateServerData('channel-groups'));
    expect(groupsReload).toHaveBeenCalledTimes(1);
    expect(channelsReload).not.toHaveBeenCalled();

    act(() => invalidateServerData('channels'));
    expect(channelsReload).toHaveBeenCalledTimes(1);
    expect(groupsReload).toHaveBeenCalledTimes(1);
  });
});
