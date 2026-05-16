/**
 * Unit tests for useAddStreamDedup — BD-I integration hook for the
 * "Add Stream" surface (ADR-008 §D1, trigger_context='add_stream').
 *
 * The hook owns the candidate-lookup + modal-state machine that wires
 * `GET /api/channel-merges/candidates` (BD-D) to `StreamDedupModal` (BD-G)
 * for the single-stream "Add Stream" flow on channel-less stream cards.
 * Drag-drop (BD-H) is intentionally NOT covered by this hook — that
 * surface has its own cancel-bounce animation and lives in ChannelsPane.
 *
 * Covered behaviors:
 *   - No-candidate-falls-through: when the candidates endpoint returns an
 *     empty list, the modal does NOT open and the original `onProceed`
 *     create-channel callback fires with the stream + group context.
 *   - Candidate-found-opens-modal: when a candidate is returned the modal
 *     state flips to `isOpen=true` with the stream name + candidate, and
 *     the original `onProceed` callback is NOT invoked.
 *   - onMerge calls `addStreamToChannel(channel_id, stream_id)`: the
 *     ADR-008 §D8 string→number id conversion is exercised verbatim,
 *     modal closes on success, and the original create-channel callback
 *     is NOT invoked.
 *   - onCreateNew calls the original `onProceed` callback (auto-creation
 *     rules consulted as usual via the caller-supplied path) and closes
 *     the modal.
 *   - onCancel closes the modal without calling either downstream path —
 *     no card to bounce back for the "Add Stream" surface (per bd-1lznl
 *     spec: animation only applies to BD-H drag-drop).
 *   - Lookup failure does not block the operator: on a candidates-endpoint
 *     error the hook falls through to `onProceed` so the operator's
 *     "Add Stream" click is not silently swallowed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useAddStreamDedup } from './useAddStreamDedup';
import * as api from '../services/api';

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api');
  return {
    ...actual,
    getChannelMergeCandidates: vi.fn(),
    addStreamToChannel: vi.fn(),
  };
});

const mockedGetCandidates = vi.mocked(api.getChannelMergeCandidates);
const mockedAddStream = vi.mocked(api.addStreamToChannel);

const STREAM = { id: 42, name: 'ESPN HD' };
const GROUP_ID = 7;

function emptyResponse(streamName: string): api.ChannelMergeCandidatesResponse {
  return {
    stream_name: streamName,
    candidates: [],
    total: 0,
    page: 1,
    page_size: 50,
    total_pages: 0,
  };
}

function responseWith(
  candidate: api.ChannelMergeCandidate,
  streamName = STREAM.name,
): api.ChannelMergeCandidatesResponse {
  return {
    stream_name: streamName,
    candidates: [candidate],
    total: 1,
    page: 1,
    page_size: 50,
    total_pages: 1,
  };
}

describe('useAddStreamDedup', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('no-candidate-falls-through: calls onProceed when candidates list is empty', async () => {
    mockedGetCandidates.mockResolvedValue(emptyResponse(STREAM.name));
    const onProceed = vi.fn().mockResolvedValue(undefined);

    const { result } = renderHook(() => useAddStreamDedup());

    await act(async () => {
      await result.current.requestAddStream(STREAM, GROUP_ID, onProceed);
    });

    expect(mockedGetCandidates).toHaveBeenCalledWith(STREAM.name, GROUP_ID);
    expect(onProceed).toHaveBeenCalledTimes(1);
    expect(result.current.modalState.isOpen).toBe(false);
    expect(result.current.modalState.candidate).toBeNull();
  });

  it('candidate-found-opens-modal: flips state to open and does NOT call onProceed', async () => {
    const candidate: api.ChannelMergeCandidate = {
      channel_id: 'channel-uuid-abc',
      channel_name: 'ESPN HD',
      confidence: 0.92,
    };
    mockedGetCandidates.mockResolvedValue(responseWith(candidate));
    const onProceed = vi.fn().mockResolvedValue(undefined);

    const { result } = renderHook(() => useAddStreamDedup());

    await act(async () => {
      await result.current.requestAddStream(STREAM, GROUP_ID, onProceed);
    });

    expect(result.current.modalState.isOpen).toBe(true);
    expect(result.current.modalState.streamName).toBe(STREAM.name);
    expect(result.current.modalState.candidate).toEqual(candidate);
    expect(onProceed).not.toHaveBeenCalled();
  });

  it('onMerge calls addStreamToChannel with parsed channel id and source stream id', async () => {
    const candidate: api.ChannelMergeCandidate = {
      channel_id: '123',
      channel_name: 'ESPN HD',
      confidence: 0.95,
    };
    mockedGetCandidates.mockResolvedValue(responseWith(candidate));
    mockedAddStream.mockResolvedValue({} as never);
    const onProceed = vi.fn();

    const { result } = renderHook(() => useAddStreamDedup());

    await act(async () => {
      await result.current.requestAddStream(STREAM, GROUP_ID, onProceed);
    });

    await act(async () => {
      await result.current.handleMerge(candidate.channel_id);
    });

    expect(mockedAddStream).toHaveBeenCalledWith(123, STREAM.id);
    expect(onProceed).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(result.current.modalState.isOpen).toBe(false);
    });
  });

  it('onCreateNew calls the original onProceed path (auto-creation rules consulted as usual)', async () => {
    const candidate: api.ChannelMergeCandidate = {
      channel_id: '9',
      channel_name: 'ESPN HD',
      confidence: 0.88,
    };
    mockedGetCandidates.mockResolvedValue(responseWith(candidate));
    const onProceed = vi.fn().mockResolvedValue(undefined);

    const { result } = renderHook(() => useAddStreamDedup());

    await act(async () => {
      await result.current.requestAddStream(STREAM, GROUP_ID, onProceed);
    });

    await act(async () => {
      await result.current.handleCreateNew();
    });

    expect(onProceed).toHaveBeenCalledTimes(1);
    expect(mockedAddStream).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(result.current.modalState.isOpen).toBe(false);
    });
  });

  it('onCancel closes the modal without invoking either downstream path', async () => {
    const candidate: api.ChannelMergeCandidate = {
      channel_id: '5',
      channel_name: 'CNN HD',
      confidence: 0.71,
    };
    mockedGetCandidates.mockResolvedValue(responseWith(candidate));
    const onProceed = vi.fn();

    const { result } = renderHook(() => useAddStreamDedup());

    await act(async () => {
      await result.current.requestAddStream(STREAM, GROUP_ID, onProceed);
    });
    expect(result.current.modalState.isOpen).toBe(true);

    act(() => {
      result.current.handleCancel();
    });

    expect(result.current.modalState.isOpen).toBe(false);
    expect(onProceed).not.toHaveBeenCalled();
    expect(mockedAddStream).not.toHaveBeenCalled();
  });

  it('candidate-lookup failure falls through to onProceed so the operator action is not silently dropped', async () => {
    mockedGetCandidates.mockRejectedValue(new Error('boom'));
    const onProceed = vi.fn().mockResolvedValue(undefined);

    const { result } = renderHook(() => useAddStreamDedup());

    await act(async () => {
      await result.current.requestAddStream(STREAM, GROUP_ID, onProceed);
    });

    expect(onProceed).toHaveBeenCalledTimes(1);
    expect(result.current.modalState.isOpen).toBe(false);
  });

  it('passes through group_id=null (search all groups) to the candidates endpoint', async () => {
    mockedGetCandidates.mockResolvedValue(emptyResponse(STREAM.name));
    const onProceed = vi.fn().mockResolvedValue(undefined);

    const { result } = renderHook(() => useAddStreamDedup());

    await act(async () => {
      await result.current.requestAddStream(STREAM, null, onProceed);
    });

    expect(mockedGetCandidates).toHaveBeenCalledWith(STREAM.name, null);
  });
});
