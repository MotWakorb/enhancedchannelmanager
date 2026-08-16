/**
 * Apply All's aggregate never reports a cleaner outcome than the responses it
 * summed (bead `enhancedchannelmanager-e9e5o`, fix round 4).
 *
 * Edit Mode posts one bulk-commit request per 200 operations and adds up the
 * counters. `success` was recomputed locally as `totalFailed === 0`, which
 * discards the one case the backend's accounting invariant now names: an
 * operation whose upstream write LANDED and which ECM could not finish
 * recording is counted in `operationsApplied`, never in `operationsFailed`, and
 * the response reports `success: false`. Summing only the counters turned that
 * into a clean Apply All — the same swallowed failure the earlier rounds
 * removed, one level up.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { MockInstance } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useEditMode } from './useEditMode';
import * as api from '../services/api';
import type { Channel } from '../types';

function makeChannel(id: number, name: string): Channel {
  return {
    id,
    channel_number: id,
    name,
    channel_group_id: null,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: `uuid-${id}`,
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };
}

const CHANNELS = [makeChannel(1, 'Alpha')];

let bulkCommitSpy: MockInstance<typeof api.bulkCommit>;

beforeEach(() => {
  vi.spyOn(api, 'getChannels').mockResolvedValue({
    results: CHANNELS, next: null, count: CHANNELS.length,
  } as unknown as Awaited<ReturnType<typeof api.getChannels>>);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function setup() {
  const view = renderHook(() =>
    useEditMode({ channels: CHANNELS, onChannelsChange: vi.fn(), operatorKey: 'test#1' }),
  );
  act(() => view.result.current.enterEditMode());
  return view;
}

async function commitOneStagedUpdate(view: ReturnType<typeof setup>) {
  act(() => {
    view.result.current.stageUpdateChannel(1, { name: 'Renamed' }, 'Rename Alpha');
  });
  let outcome: Awaited<ReturnType<typeof view.result.current.commit>> | undefined;
  await act(async () => {
    outcome = await view.result.current.commit();
  });
  return outcome!;
}

describe('Apply All aggregate accounting', () => {
  it('does not launder an unclean response with a zero failure count into a success', async () => {
    // The reviewer's reproduction as the backend now reports it: the operation
    // APPLIED (so retrying it would duplicate), and the batch is not clean.
    bulkCommitSpy = vi.spyOn(api, 'bulkCommit').mockResolvedValue({
      success: false,
      operationsApplied: 1,
      operationsFailed: 0,
      partial: true,
      errors: [
        {
          operationId: 'op-0-createChannel',
          operationType: 'createChannel',
          applied: true,
          error: 'Dispatcharr accepted the create but returned no usable channel id',
        },
      ],
      tempIdMap: {},
      groupIdMap: {},
    } as unknown as Awaited<ReturnType<typeof api.bulkCommit>>);

    const outcome = await commitOneStagedUpdate(setup());

    expect(bulkCommitSpy).toHaveBeenCalled();
    expect(outcome.operationsApplied).toBe(1);
    expect(outcome.operationsFailed).toBe(0);
    expect(outcome.success).toBe(false);
    // The error the backend named survives the aggregation, so the operator
    // has something to reconcile against.
    expect(outcome.errors).toHaveLength(1);
  });

  it('still reports a genuinely clean batch as a success (pin)', async () => {
    // PIN on already-correct behaviour: the new condition must not make every
    // Apply All look broken.
    vi.spyOn(api, 'bulkCommit').mockResolvedValue({
      success: true,
      operationsApplied: 1,
      operationsFailed: 0,
      partial: false,
      errors: [],
      tempIdMap: {},
      groupIdMap: {},
    } as unknown as Awaited<ReturnType<typeof api.bulkCommit>>);

    const outcome = await commitOneStagedUpdate(setup());

    expect(outcome.success).toBe(true);
    expect(outcome.operationsFailed).toBe(0);
  });
});
