/**
 * The two duplicate-check "Merge" buttons stage inside Edit Mode
 * (bead enhancedchannelmanager-kz089).
 *
 * These were not on the bead's list of ten immediate writers. They turned up
 * in the exhaustive sweep the bead asked for ("the ones not yet checked are the
 * risk"), and they are the same defect by a different route: both merge
 * surfaces attach a stream to a channel with `api.addStreamToChannel` the
 * instant the operator clicks, and both are reachable ONLY in Edit Mode.
 *
 *  - `useDedupOnDrop`   — drag a stream onto a channel group. The drag handle
 *    renders only in Edit Mode.
 *  - `useAddStreamDedup` — the "Create in..." menu, itself rendered only in
 *    Edit Mode.
 *
 * The PO's rule for anything found beyond the ten: stage it if it is cheap and
 * local. It is exactly that here — `stageAddStream` already exists and is what
 * every other stream assignment in the pane uses.
 *
 * `useDedupOnDrop` carried a second, quieter fault: after the immediate write it
 * called `reloadChannels()`, refetching the server list mid-session on top of
 * the working copy holding every other staged change.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

type StageAddStream = (channelId: number, streamId: number, description: string) => void;
import { renderHook, act } from '@testing-library/react';
import { useDedupOnDrop } from './useDedupOnDrop';
import { useAddStreamDedup } from './useAddStreamDedup';
import * as api from '../services/api';

const CANDIDATE = {
  channel_id: '42',
  channel_name: 'Alpha',
  score: 0.97,
  reason: 'name match',
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useDedupOnDrop merge', () => {
  async function openModal(stageAddStream?: StageAddStream) {
    vi.spyOn(api, 'getDedupCandidates').mockResolvedValue({
      candidates: [CANDIDATE],
    } as unknown as Awaited<ReturnType<typeof api.getDedupCandidates>>);
    const reloadChannels = vi.fn();
    const view = renderHook(() =>
      useDedupOnDrop({ reloadChannels, stageAddStream }),
    );
    await act(async () => {
      await view.result.current.handleSingleStreamDrop(
        { streamId: 7, streamName: 'Stream 7', targetGroupId: null },
        vi.fn(),
      );
    });
    // Smoke-check the instrument: if the modal did not open, `handleMerge`
    // early-returns and every "did not write" assertion below passes for the
    // wrong reason.
    expect(view.result.current.modalState).not.toBeNull();
    return { view, reloadChannels };
  }

  it('stages the assignment instead of writing it when Edit Mode supplies a stager', async () => {
    const addSpy = vi.spyOn(api, 'addStreamToChannel');
    const stageAddStream = vi.fn();
    const { view } = await openModal(stageAddStream);

    await act(async () => {
      await view.result.current.handleMerge('42');
    });

    expect(stageAddStream).toHaveBeenCalledWith(42, 7, expect.any(String));
    expect(addSpy).not.toHaveBeenCalled();
  });

  it('does not refetch channels while staged work is in the working copy', async () => {
    vi.spyOn(api, 'addStreamToChannel');
    const stageAddStream = vi.fn();
    const { view, reloadChannels } = await openModal(stageAddStream);

    await act(async () => {
      await view.result.current.handleMerge('42');
    });

    expect(reloadChannels).not.toHaveBeenCalled();
  });

  it('still writes immediately with no stager, which is correct outside Edit Mode', async () => {
    const addSpy = vi.spyOn(api, 'addStreamToChannel').mockResolvedValue(
      {} as unknown as Awaited<ReturnType<typeof api.addStreamToChannel>>,
    );
    const { view, reloadChannels } = await openModal(undefined);

    await act(async () => {
      await view.result.current.handleMerge('42');
    });

    expect(addSpy).toHaveBeenCalledWith(42, 7);
    expect(reloadChannels).toHaveBeenCalled();
  });
});

describe('useAddStreamDedup merge', () => {
  async function openModal(stageAddStream?: StageAddStream) {
    vi.spyOn(api, 'getChannelMergeCandidates').mockResolvedValue({
      candidates: [CANDIDATE],
    } as unknown as Awaited<ReturnType<typeof api.getChannelMergeCandidates>>);
    const view = renderHook(() => useAddStreamDedup({ stageAddStream }));
    await act(async () => {
      await view.result.current.requestAddStream(
        { id: 7, name: 'Stream 7' },
        null,
        vi.fn(),
      );
    });
    expect(view.result.current.modalState.isOpen).toBe(true);
    return view;
  }

  it('stages the assignment instead of writing it', async () => {
    const addSpy = vi.spyOn(api, 'addStreamToChannel');
    const stageAddStream = vi.fn();
    const view = await openModal(stageAddStream);

    await act(async () => {
      await view.result.current.handleMerge('42');
    });

    expect(stageAddStream).toHaveBeenCalledWith(42, 7, expect.any(String));
    expect(addSpy).not.toHaveBeenCalled();
  });

  it('still writes immediately with no stager', async () => {
    const addSpy = vi.spyOn(api, 'addStreamToChannel').mockResolvedValue(
      {} as unknown as Awaited<ReturnType<typeof api.addStreamToChannel>>,
    );
    const view = await openModal(undefined);

    await act(async () => {
      await view.result.current.handleMerge('42');
    });

    expect(addSpy).toHaveBeenCalledWith(42, 7);
  });
});
