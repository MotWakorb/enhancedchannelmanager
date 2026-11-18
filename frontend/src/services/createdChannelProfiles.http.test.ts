/** Production service/api/fetchJson against a private socket peer, not Dispatcharr. */
import { createServer, type Server } from 'node:http';
import { once } from 'node:events';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import recorded from '../../../backend/tests/fixtures/y3m6o1/dispatcharr_channel_profiles_mixed_membership.json';
import { applyCreatedChannelProfiles } from './createdChannelProfiles';

const nativeFetch = globalThis.fetch;
const binding = 'f'.repeat(64);
let server: Server;
let profiles: typeof recorded.profiles;
let fault: 'healthy' | 'ignored' | 'rejected' | 'lost' | 'read-failed';
let wrote: boolean;
let writes: Array<{ profileId: number; channelIds: number[] }>;
let unexpected: string[];

beforeEach(async () => {
  profiles = structuredClone(recorded.profiles);
  fault = 'healthy'; wrote = false; writes = []; unexpected = [];
  server = createServer(async (req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.setHeader('Connection', 'close');
    const path = req.url;
    const single = path?.match(/^\/api\/created-channel-profiles\/(\d+)\/channels\/(\d+)$/);
    const bulk = path?.match(/^\/api\/created-channel-profiles\/(\d+)\/channels\/bulk-update$/);
    const profileRead = path?.match(/^\/api\/created-channel-profiles\/(\d+)$/);
    if (req.headers['x-ecm-profile-recovery'] !== binding) {
      unexpected.push(`Unbound ${req.method} ${path}`);
      res.writeHead(409).end('{}');
      return;
    }
    if (req.method === 'GET' && (path === '/api/created-channel-profiles' || profileRead)) {
      if (fault === 'read-failed' && wrote) {
        res.writeHead(503).end('{"detail":"Synthetic read unavailable"}');
      } else {
        res.end(JSON.stringify(profileRead ? profiles.find(profile => profile.id === Number(profileRead[1])) : profiles));
      }
      return;
    }
    if (req.method !== 'PATCH' || (!single && !bulk)) {
      unexpected.push(`${req.method} ${path}`);
      res.writeHead(501).end('{}');
      return;
    }
    let raw = '';
    for await (const part of req) raw += part;
    const body = JSON.parse(raw);
    const profileId = Number((single ?? bulk)![1]);
    const channelIds: number[] = bulk ? body.channel_ids : [Number(single![2])];
    writes.push({ profileId, channelIds });
    wrote = true;
    if (fault === 'rejected' && profileId === 6) {
      res.writeHead(400).end('{"detail":"Synthetic rejected write"}');
      return;
    }
    if (fault !== 'ignored' || profileId !== 6) {
      const profile = profiles.find(item => item.id === profileId)!;
      const members = new Set(profile.channels);
      channelIds.forEach(id => body.enabled ? members.add(id) : members.delete(id));
      profile.channels = [...members];
    }
    if (fault === 'lost' && profileId === 6) res.destroy();
    else res.end('{"success":true}');
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Expected private TCP address');
  const origin = `http://127.0.0.1:${address.port}`;
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => nativeFetch(new URL(String(input), origin), init));
});

afterEach(async () => {
  server.closeAllConnections();
  await new Promise<void>(resolve => server.close(() => resolve()));
  vi.restoreAllMocks();
  expect(unexpected).toEqual([]);
});

it.each(['ignored', 'rejected', 'read-failed'] as const)(
  'retains and retries the original intent through real HTTP after %s',
  async mode => {
    fault = mode;
    const intent = [{ channelId: recorded.probe_channel_id, profileIds: [6] }];
    const first = await applyCreatedChannelProfiles(intent, binding);
    expect(first.remaining).toEqual(intent);
    expect(first.verified).toBe(mode !== 'read-failed');
    fault = 'healthy';
    expect((await applyCreatedChannelProfiles(first.remaining, binding)).remaining).toEqual([]);
    expect(profiles.filter(profile => profile.channels.includes(recorded.probe_channel_id)).map(profile => profile.id)).toEqual([6]);
    expect(writes.every(write => write.channelIds.length <= 100)).toBe(true);
  },
);

it('confirms a landed write after a lost HTTP response without repeating the mutation', async () => {
  fault = 'lost';
  const outcome = await applyCreatedChannelProfiles([{ channelId: recorded.probe_channel_id, profileIds: [6] }], binding);
  expect(outcome.remaining).toEqual([]);
  expect(writes.filter(write => write.profileId === 6)).toHaveLength(1);
});
