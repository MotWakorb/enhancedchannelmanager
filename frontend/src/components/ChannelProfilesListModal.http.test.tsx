/** Component → production api/fetchJson → isolated loopback HTTP peer.
 * The peer is synthetic, not Dispatcharr. Recorded membership data is exercised
 * separately below; browser rendering and the backend adapter are other layers.
 */
import { createServer, type Server } from 'node:http';
import { once } from 'node:events';
import recorded from '../../../backend/tests/fixtures/y3m6o1/dispatcharr_channel_profiles_mixed_membership.json';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { ChannelProfilesListModal } from './ChannelProfilesListModal';
import type { Channel } from '../types';

const notices = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn() }));
vi.mock('../contexts/NotificationContext', () => ({ useNotifications: () => notices }));
vi.mock('../utils/logger', () => ({ logger: { debug: vi.fn(), info: vi.fn(), error: vi.fn(), exception: vi.fn() } }));

type Write = { ids: number[]; enabled: boolean; bulk: boolean };
type Fault = 'reject' | 'partial' | 'lost' | 'ignore';
let server: Server;
let rows: Map<number, boolean>;
let writes: Write[];
let events: string[];
let legacy: boolean;
let bulkFault: Fault | undefined;
let faultIds: Set<number>;
let singleFailures: Set<number>;
let readFailures: number;
let failReadsAfterWrite: boolean;
let failReadsAfterSingle: boolean;
let contradictAfterSingle: { id: number; enabled: boolean } | undefined;
let activeWrites: number;
let maxActiveWrites: number;
let releaseWrite: (() => void) | undefined;
let holdWrites: boolean;
let holdReadback: boolean;
let releaseRead: (() => void) | undefined;
let faultHits: number;
let activeFetches: Set<Promise<Response>>;
const nativeFetch = globalThis.fetch;

function profile() {
  return { id: 7, name: 'Synthetic profile', channels: [...rows].filter(([, on]) => on).map(([id]) => id) };
}

function channels(count: number): Channel[] {
  return Array.from({ length: count }, (_, i) => ({
    id: i + 1, name: `Synthetic channel ${i + 1}`, channel_number: i + 1,
    channel_group_id: i < 107 ? 1 : 2, tvg_id: null, tvc_guide_stationid: null,
    epg_data_id: null, streams: [], stream_profile_id: null, uuid: `synthetic-${i + 1}`,
    logo_id: null, auto_created: false, auto_created_by: null, auto_created_by_name: null,
  }));
}

beforeEach(async () => {
  vi.clearAllMocks();
  rows = new Map([[1, true], [2, true], [3, false]]);
  writes = []; events = []; legacy = false; faultIds = new Set(); singleFailures = new Set();
  bulkFault = undefined; readFailures = 0; failReadsAfterWrite = false; failReadsAfterSingle = false;
  contradictAfterSingle = undefined;
  activeWrites = 0; maxActiveWrites = 0; holdWrites = false; releaseWrite = undefined; faultHits = 0;
  holdReadback = false; releaseRead = undefined;
  activeFetches = new Set();
  server = createServer(async (req, res) => {
    const path = req.url;
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'GET') {
      events.push('read');
      if (holdReadback && writes.length > 0) await new Promise<void>(resolve => { releaseRead = resolve; });
      if (readFailures > 0 || (failReadsAfterWrite && writes.length > 0) ||
          (failReadsAfterSingle && writes.some(w => !w.bulk))) {
        readFailures = Math.max(0, readFailures - 1);
        res.writeHead(503).end(JSON.stringify({ detail: 'Synthetic read unavailable' }));
        return;
      }
      if (path === '/api/channel-profiles') res.end(JSON.stringify([profile()]));
      else if (path === '/api/channel-profiles/7') res.end(JSON.stringify(profile()));
      else res.writeHead(501).end('{}');
      return;
    }
    const single = path?.match(/^\/api\/channel-profiles\/7\/channels\/(\d+)$/);
    const bulk = path === '/api/channel-profiles/7/channels/bulk-update';
    if (req.method !== 'PATCH' || (!single && !bulk)) { res.writeHead(501).end('{}'); return; }
    let body = '';
    for await (const part of req) body += part;
    const data = JSON.parse(body);
    const ids: number[] = bulk ? data.channel_ids : [Number(single![1])];
    writes.push({ ids, enabled: data.enabled, bulk });
    events.push(bulk ? 'bulk' : 'single');
    activeWrites++;
    maxActiveWrites = Math.max(maxActiveWrites, activeWrites);
    await new Promise(resolve => setTimeout(resolve, 2));
    if (holdWrites) await new Promise<void>(resolve => { releaseWrite = resolve; });
    const hit = bulk && ids.some(id => faultIds.has(id)) ? bulkFault : undefined;
    if (hit) faultHits++;
    const singleFailed = !bulk && singleFailures.has(ids[0]);
    if (hit !== 'reject' && hit !== 'ignore' && !singleFailed) {
      for (const id of ids) {
        if (hit === 'partial' && faultIds.has(id)) continue;
        if (!legacy || !bulk || rows.has(id)) rows.set(id, data.enabled);
      }
    }
    // Model another actor contradicting an earlier read, during fallback.
    if (!bulk && contradictAfterSingle !== undefined) rows.set(contradictAfterSingle.id, contradictAfterSingle.enabled);
    activeWrites--;
    if (hit === 'lost') res.destroy();
    else if (hit === 'reject' || hit === 'partial' || singleFailed) {
      res.writeHead(503).end(JSON.stringify({ detail: 'Synthetic write unavailable' }));
    } else res.end(JSON.stringify({ success: true }));
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Expected TCP address');
  const origin = `http://127.0.0.1:${address.port}`;
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    const request = nativeFetch(new URL(String(input), origin), init);
    activeFetches.add(request);
    void request.then(() => activeFetches.delete(request), () => activeFetches.delete(request));
    return request;
  });
});

afterEach(async () => {
  holdWrites = false; releaseWrite?.(); holdReadback = false; releaseRead?.();
  do {
    await Promise.allSettled([...activeFetches]);
    await new Promise(resolve => setTimeout(resolve, 20));
  } while (activeFetches.size > 0);
  server.closeAllConnections();
  await new Promise<void>(resolve => server.close(() => resolve()));
  vi.restoreAllMocks();
});

async function open(count = 8, overrides: Partial<ComponentProps<typeof ChannelProfilesListModal>> = {}) {
  const onSaved = vi.fn(); const onClose = vi.fn();
  render(<ChannelProfilesListModal isOpen onClose={onClose} onSaved={onSaved}
    channels={channels(count)} channelGroups={[
      { id: 1, name: 'Synthetic first', channel_count: Math.min(count, 107) },
      { id: 2, name: 'Synthetic second', channel_count: Math.max(0, count - 107) },
    ]} {...overrides} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Manage channels' }));
  return { onSaved, onClose };
}
function toggle(id: number) { fireEvent.click(screen.getByText(`Synthetic channel ${id}`)); }
function select(id: number) { fireEvent.click(screen.getByLabelText(`Select Synthetic channel ${id} for bulk apply`)); }
function save() { fireEvent.click(screen.getByRole('button', { name: /^Save Changes/ })); }
async function settled() {
  await waitFor(() => expect(screen.queryByRole('button', { name: /Saving|Applying/ })).not.toBeInTheDocument(), { timeout: 10000 });
}

describe('profile membership terminal truth over real HTTP (71imf)', () => {
  it.each([false, true])('bounds mixed 214-channel saves and creates only unapplied rows (legacy=%s)', async (old) => {
    legacy = old; rows = new Map(Array.from({ length: 107 }, (_, i) => [i + 1, true]));
    await open(214);
    const groups = document.querySelectorAll<HTMLInputElement>('.channel-group-header input');
    fireEvent.click(groups[0]); fireEvent.click(groups[1]); save(); await settled();
    expect(profile().channels).toEqual(Array.from({ length: 107 }, (_, i) => i + 108));
    expect(writes.filter(w => w.bulk).map(w => w.ids.length)).toEqual([100, 7, 100, 7]);
    expect(writes.filter(w => !w.bulk)).toHaveLength(old ? 107 : 0);
    expect(writes.filter(w => !w.bulk).every(w => w.enabled && w.ids[0] >= 108)).toBe(true);
    expect(maxActiveWrites).toBe(1);
    expect(events[events.length - 1]).toBe('read');
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => !init?.method)
      .every(([, init]) => init?.cache === 'no-store')).toBe(true);
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.credentials === 'include')).toBe(true);
    expect(screen.getByText('107 / 214 enabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
  }, 30000);

  it.each([true, false])('retains original intent contradicted by terminal readback (initially satisfied=%s)', async (initial) => {
    legacy = true; rows.set(3, initial); contradictAfterSingle = { id: 3, enabled: false };
    await open();
    if (initial) { select(3); select(4); fireEvent.click(screen.getByRole('button', { name: 'Apply to Selected: Enable' })); }
    else { toggle(3); toggle(4); save(); }
    await settled();
    expect(rows.get(3)).toBe(false); expect(rows.get(4)).toBe(true);
    expect(screen.getByText('3 / 8 enabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes (1)' })).toBeEnabled();
    expect(screen.getByRole('status')).toHaveTextContent(/1.*pending/);
    expect(notices.success).not.toHaveBeenCalled();
    contradictAfterSingle = undefined; save(); await settled();
    expect(writes[writes.length - 1]).toEqual({ ids: [3], enabled: true, bulk: true });
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
  });

  it('shows 198 actual, not 102 intended, after 96 of 214 mixed changes fail, then retries only those 96', async () => {
    rows = new Map(Array.from({ length: 214 }, (_, i) => [i + 1, true]));
    bulkFault = 'partial'; faultIds = new Set(Array.from({ length: 96 }, (_, i) => i + 68));
    await open(316);
    act(() => {
      for (let id = 1; id <= 163; id++) toggle(id);
      for (let id = 215; id <= 265; id++) toggle(id);
    });
    expect(screen.getByRole('button', { name: 'Save Changes (214)' })).toBeEnabled();
    expect(screen.getByText('214 / 316 enabled')).toBeInTheDocument();
    save(); await settled();
    expect(faultHits).toBe(2); expect(profile().channels).toHaveLength(198);
    expect(screen.getByText('198 / 316 enabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes (96)' })).toBeEnabled();
    expect(maxActiveWrites).toBe(1); expect(writes.map(w => w.ids.length)).toEqual([51, 100, 63]);
    bulkFault = undefined; save(); await settled();
    expect(writes[writes.length - 1]?.ids).toEqual([...faultIds]);
    expect(profile().channels).toHaveLength(102);
    expect(screen.getByText('102 / 316 enabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
  }, 30000);

  it('retains an explicit disable contradicted by terminal readback, rather than leaving membership unrestricted', async () => {
    legacy = true; rows.set(3, true); contradictAfterSingle = { id: 3, enabled: true };
    await open(); toggle(3); toggle(4); save(); await settled();
    expect(rows.get(3)).toBe(true); expect(rows.get(4)).toBe(true);
    expect(screen.getByText('4 / 8 enabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes (1)' })).toBeEnabled();
    expect(notices.success).not.toHaveBeenCalled();
    contradictAfterSingle = undefined; save(); await settled();
    expect(writes[writes.length - 1]).toEqual({ ids: [3], enabled: false, bulk: true });
    expect(screen.getByText('3 / 8 enabled')).toBeInTheDocument();
  });

  it('does not turn another actor’s untouched-channel changes into writes', async () => {
    await open(); toggle(3); rows.set(1, false); rows.set(4, true); save(); await settled();
    expect(writes).toEqual([{ ids: [3], enabled: true, bulk: true }]);
    expect(profile().channels).toEqual([2, 3, 4]);
    expect(screen.getByText('3 / 8 enabled')).toBeInTheDocument();
  });

  it('reports partial landing and retries only the unconfirmed ID', async () => {
    bulkFault = 'partial'; faultIds.add(4);
    const { onSaved } = await open(); toggle(3); toggle(4); save(); await settled();
    expect(faultHits).toBe(1); expect(profile().channels).toEqual([1, 2, 3]);
    expect(screen.getByText('3 / 8 enabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes (1)' })).toBeEnabled();
    expect(onSaved).toHaveBeenCalledTimes(1);
    bulkFault = undefined; save(); await settled();
    expect(writes.map(w => w.ids)).toEqual([[3, 4], [4]]);
  });

  it('continues explicit disables after an enable batch fails', async () => {
    bulkFault = 'reject'; faultIds.add(3);
    await open(); toggle(3); toggle(1); save(); await settled();
    expect(rows.get(1)).toBe(false); expect(rows.get(3)).toBe(false);
    expect(writes).toEqual([{ ids: [3], enabled: true, bulk: true }, { ids: [1], enabled: false, bulk: true }]);
    expect(screen.getByText('1 / 8 enabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes (1)' })).toBeEnabled();
  });

  it('clears landed intents after a lost response, without duplicate writes', async () => {
    bulkFault = 'lost'; faultIds.add(3);
    await open(); toggle(3); toggle(4); save(); await settled();
    expect(faultHits).toBe(1); expect(writes).toHaveLength(1);
    expect(screen.getByText('4 / 8 enabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
    expect(notices.warning).toHaveBeenCalled();
  });

  it('marks failed readback unknown and reads before retrying possibly landed writes', async () => {
    await open(); toggle(3); toggle(4); failReadsAfterWrite = true; save(); await settled();
    expect(profile().channels).toEqual([1, 2, 3, 4]);
    expect(screen.getByText(/Enabled count unknown/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes (2)' })).toBeEnabled();
    failReadsAfterWrite = false; save(); await settled();
    expect(writes).toHaveLength(1); expect(screen.getByText('4 / 8 enabled')).toBeInTheDocument();
  });

  it('retains all original intents when the terminal fallback read fails', async () => {
    legacy = true; failReadsAfterSingle = true;
    await open(); toggle(3); toggle(4); save(); await settled();
    expect(rows.get(4)).toBe(true);
    expect(screen.getByText(/Enabled count unknown \(last confirmed: 3 \/ 8\)/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes (2)' })).toBeEnabled();
    failReadsAfterSingle = false; save(); await settled();
    expect(writes).toEqual([{ ids: [3, 4], enabled: true, bulk: true }, { ids: [4], enabled: true, bulk: false }]);
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
  });

  it('does not write while preflight is unreadable and preserves an explicit reversal of unknown state', async () => {
    await open(); toggle(3); readFailures = 1; save(); await settled(); expect(writes).toEqual([]);
    failReadsAfterWrite = true; save(); await settled(); toggle(3);
    expect(screen.getByRole('button', { name: 'Save Changes (1)' })).toBeEnabled();
    failReadsAfterWrite = false; save(); await settled();
    expect(writes.map(w => w.enabled)).toEqual([true, false]); expect(rows.get(3)).toBe(false);
  });

  it('keeps a misleading success pending after bounded fallback also fails', async () => {
    bulkFault = 'ignore'; faultIds.add(4); singleFailures.add(4);
    await open(); toggle(4); save(); await settled();
    expect(faultHits).toBe(1);
    expect(writes).toEqual([{ ids: [4], enabled: true, bulk: true }, { ids: [4], enabled: true, bulk: false }]);
    expect(events[events.length - 1]).toBe('read'); expect(screen.getByText('2 / 8 enabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes (1)' })).toBeEnabled();
    expect(notices.success).not.toHaveBeenCalled();
  });

  it.each([false, true])('Apply to Selected preserves unselected pending intent and supports missing rows (legacy=%s)', async (old) => {
    legacy = old;
    await open(); toggle(1); select(3); select(4);
    fireEvent.click(screen.getByRole('button', { name: 'Apply to Selected: Enable' })); await settled();
    expect(profile().channels).toEqual([1, 2, 3, 4]);
    expect(screen.getByRole('button', { name: 'Save Changes (1)' })).toBeEnabled();
    expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
    save(); await settled(); expect(profile().channels).toEqual([2, 3, 4]);
  });

  it('Apply to Selected drops satisfied retries and lets an explicit reversal supersede a failed intent', async () => {
    bulkFault = 'partial'; faultIds.add(4);
    await open(); select(3); select(4);
    fireEvent.click(screen.getByRole('button', { name: 'Apply to Selected: Enable' })); await settled();
    expect(screen.getByText('1 selected')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes (1)' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Apply to Selected: Disable' })); await settled();
    expect(rows.get(4)).toBeUndefined();
    expect(writes).toEqual([{ ids: [3, 4], enabled: true, bulk: true }]);
    expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
  });

  it('bounds Apply to Selected for 214 missing memberships', async () => {
    rows = new Map(); await open(214);
    const boxes = document.querySelectorAll<HTMLInputElement>('.bulk-select-checkbox');
    act(() => { boxes.forEach(box => fireEvent.click(box)); });
    fireEvent.click(screen.getByRole('button', { name: 'Apply to Selected: Enable' })); await settled();
    expect(writes.map(w => w.ids.length)).toEqual([100, 100, 14]); expect(maxActiveWrites).toBe(1);
    expect(rows.size).toBe(214); expect(screen.getByText('214 / 214 enabled')).toBeInTheDocument();
  }, 30000);

  it('stages 214 mixed intents without immediate writes in Edit Mode', async () => {
    rows = new Map(Array.from({ length: 107 }, (_, i) => [i + 1, true]));
    const stage = vi.fn(); const start = vi.fn(); const end = vi.fn();
    await open(214, { isEditMode: true, onStageSetProfileMembership: stage, onStartBatch: start, onEndBatch: end });
    const groups = document.querySelectorAll<HTMLInputElement>('.channel-group-header input');
    fireEvent.click(groups[0]); fireEvent.click(groups[1]);
    fireEvent.click(screen.getByRole('button', { name: 'Stage Changes (214)' }));
    expect(stage.mock.calls.map(([, ids, enabled]) => [ids.length, enabled])).toEqual([[107, true], [107, false]]);
    expect(start).toHaveBeenCalledTimes(1); expect(end).toHaveBeenCalledTimes(1);
    expect(writes).toEqual([]); expect(events).toEqual(['read']);
  });

  it('locks both entry points, navigation and toggles through terminal readback', async () => {
    const { onClose } = await open(); toggle(3); select(4); holdWrites = true; holdReadback = true;
    act(() => { save(); save(); }); await waitFor(() => expect(releaseWrite).toBeTypeOf('function'));
    expect(writes).toHaveLength(1); expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Apply to Selected: Enable' })).toBeDisabled();
    fireEvent.keyDown(document, { key: 'Escape' }); toggle(2); expect(onClose).not.toHaveBeenCalled();
    await act(async () => { holdWrites = false; releaseWrite!(); });
    await waitFor(() => expect(releaseRead).toBeTypeOf('function'));
    expect(screen.getByRole('button', { name: 'Saving...' })).toBeDisabled();
    await act(async () => { holdReadback = false; releaseRead!(); }); await settled();
    expect(writes).toHaveLength(1); expect(screen.getByText('3 / 8 enabled')).toBeInTheDocument();
  });

  it('uses the recorded real enabled-only membership contract through production API parsing', async () => {
    for (const id of [recorded.enabled_profile_ids[0], recorded.disabled_profile_ids[0]]) {
      const real = recorded.profiles.find((p: { id: number }) => p.id === id);
      if (!real) throw new Error(`Recorded profile ${id} missing`);
      const { getChannelProfile } = await import('../services/api');
      // Return the unchanged recorded response, rather than a guessed shape.
      vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(real)));
      const parsed = await getChannelProfile(real.id, { fresh: true });
      expect(parsed.channels.includes(recorded.probe_channel_id)).toBe(recorded.enabled_profile_ids.includes(id));
    }
  });
});
