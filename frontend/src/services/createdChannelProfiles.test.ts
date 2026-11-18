import { beforeEach, describe, expect, it, vi } from 'vitest';
import recorded from '../../../backend/tests/fixtures/y3m6o1/dispatcharr_channel_profiles_mixed_membership.json';
import type { ChannelProfile } from '../types';
import * as api from './api';
import { applyCreatedChannelProfiles } from './createdChannelProfiles';
import { HttpError } from './httpClient';

vi.mock('./api', () => ({
  getChannelProfiles: vi.fn(), getChannelProfile: vi.fn(),
  bulkUpdateProfileChannels: vi.fn(), updateProfileChannel: vi.fn(),
}));

let profiles: ChannelProfile[];
let fault: 'healthy' | 'ignored' | 'rejected' | 'lost';
let active: number;
let maxActive: number;

beforeEach(() => {
  vi.resetAllMocks();
  profiles = structuredClone(recorded.profiles);
  fault = 'healthy';
  active = 0;
  maxActive = 0;
  vi.mocked(api.getChannelProfiles).mockImplementation(async () => structuredClone(profiles));
  vi.mocked(api.getChannelProfile).mockImplementation(async id => structuredClone(profiles.find(profile => profile.id === id)!));
  const write = async (id: number, ids: number[], enabled: boolean) => {
    active++;
    maxActive = Math.max(maxActive, active);
    await Promise.resolve();
    try {
      const profile = profiles.find(profile => profile.id === id)!;
      if (id === 6 && fault === 'rejected') throw new Error('Synthetic rejected write');
      if (id !== 6 || fault !== 'ignored') {
        const members = new Set(profile.channels);
        ids.forEach(channelId => enabled ? members.add(channelId) : members.delete(channelId));
        profile.channels = [...members];
      }
      if (id === 6 && fault === 'lost') throw new Error('Synthetic lost response');
      return { success: true };
    } finally {
      active--;
    }
  };
  vi.mocked(api.bulkUpdateProfileChannels).mockImplementation(write);
  vi.mocked(api.updateProfileChannel).mockImplementation(async (id, channelId, body) => write(id, [channelId], body.enabled!));
});

describe('post-create profile outcomes', () => {
  const intent = [{ channelId: recorded.probe_channel_id, profileIds: [6] }];
  const binding = 'f'.repeat(64);

  it('uses recorded enabled-only membership and removes visibility in unselected profiles', async () => {
    expect(recorded.enabled_profile_ids).not.toContain(6);
    const result = await applyCreatedChannelProfiles(intent, binding);
    expect(result.verified).toBe(true);
    expect(result.remaining).toEqual([]);
    expect(profiles.filter(profile => profile.channels.includes(recorded.probe_channel_id)).map(profile => profile.id)).toEqual([6]);
  });

  it.each(['ignored', 'rejected'] as const)('retains complete intent after %s membership writes', async mode => {
    fault = mode;
    const result = await applyCreatedChannelProfiles(intent, binding);
    expect(result.verified).toBe(true);
    expect(result.remaining).toEqual(intent);
    fault = 'healthy';
    expect((await applyCreatedChannelProfiles(result.remaining, binding)).remaining).toEqual([]);
  });

  it('accepts a lost reply only when fresh readback confirms the requested membership', async () => {
    fault = 'lost';
    expect((await applyCreatedChannelProfiles(intent, binding)).remaining).toEqual([]);
    expect(vi.mocked(api.bulkUpdateProfileChannels).mock.calls.filter(([id]) => id === 6)).toHaveLength(1);
    expect(api.updateProfileChannel).not.toHaveBeenCalled();
  });

  it('retains every original intent when final readback fails, then retries by reading before writing', async () => {
    vi.mocked(api.getChannelProfiles).mockResolvedValueOnce(structuredClone(profiles))
      .mockRejectedValueOnce(new Error('Synthetic final read unavailable'));
    const result = await applyCreatedChannelProfiles(intent, binding);
    expect(result.verified).toBe(false);
    expect(result.remaining).toEqual(intent);
    const writes = vi.mocked(api.bulkUpdateProfileChannels).mock.calls.length;
    expect((await applyCreatedChannelProfiles(result.remaining, binding)).remaining).toEqual([]);
    expect(api.bulkUpdateProfileChannels).toHaveBeenCalledTimes(writes);
  });

  it('rechecks earlier successes against the complete final snapshot', async () => {
    const write = vi.mocked(api.bulkUpdateProfileChannels).getMockImplementation()!;
    vi.mocked(api.bulkUpdateProfileChannels).mockImplementation(async (...args) => {
      const result = await write(...args);
      if (args[0] === 4) profiles.find(profile => profile.id === 6)!.channels = [];
      return result;
    });
    expect((await applyCreatedChannelProfiles(intent, binding)).remaining).toEqual(intent);
  });

  it('preserves an explicit empty selection rather than substituting current defaults', async () => {
    const result = await applyCreatedChannelProfiles([{ channelId: recorded.probe_channel_id, profileIds: [] }], binding);
    expect(result.remaining).toEqual([]);
    expect(profiles.every(profile => !profile.channels.includes(recorded.probe_channel_id))).toBe(true);
  });

  it('fails closed before writes for missing selected profiles or malformed visibility', async () => {
    expect((await applyCreatedChannelProfiles([{ channelId: 12, profileIds: [999] }], binding)).remaining)
      .toEqual([{ channelId: 12, profileIds: [999] }]);
    profiles[0].channels = [0];
    expect((await applyCreatedChannelProfiles(intent, binding)).verified).toBe(false);
    expect(api.bulkUpdateProfileChannels).not.toHaveBeenCalled();
  });

  it('leaves only the affected channel pending and never changes its visibility when a profile is missing', async () => {
    const blocked = { channelId: 50001, profileIds: [999] };
    const result = await applyCreatedChannelProfiles([blocked, ...intent], binding);
    expect(result.remaining).toEqual([blocked]);
    expect(result).toMatchObject({ missingProfiles: { [blocked.channelId]: [999] } });
    expect(vi.mocked(api.bulkUpdateProfileChannels).mock.calls.flatMap(([, ids]) => ids)).not.toContain(blocked.channelId);
    expect(profiles.find(profile => profile.id === 6)?.channels).toContain(intent[0].channelId);
  });

  it('bounds writes to serial 100-channel requests for a large created batch', async () => {
    profiles.forEach(profile => { profile.channels = []; });
    const intents = Array.from({ length: 316 }, (_, index) => ({ channelId: 50_000 + index, profileIds: [6] }));
    expect((await applyCreatedChannelProfiles(intents, binding)).remaining).toEqual([]);
    expect(vi.mocked(api.bulkUpdateProfileChannels).mock.calls.map(([, ids]) => ids.length)).toEqual([100, 100, 100, 16]);
    expect(maxActive).toBe(1);
  });

  it('stops subsequent requests when the operator or destination changes during a write', async () => {
    let current = true;
    const write = vi.mocked(api.bulkUpdateProfileChannels).getMockImplementation()!;
    vi.mocked(api.bulkUpdateProfileChannels).mockImplementation(async (...args) => {
      const result = await write(...args);
      current = false;
      return result;
    });
    const result = await applyCreatedChannelProfiles(intent, binding, () => current);
    expect(result.remaining).toEqual(intent);
    expect(result.verified).toBe(false);
    expect(api.bulkUpdateProfileChannels).toHaveBeenCalledTimes(1);
    expect(api.updateProfileChannel).not.toHaveBeenCalled();
  });

  it('does not stamp unbound saved intent with a new connection or fall back to unbound routes', async () => {
    expect(await applyCreatedChannelProfiles(intent, undefined)).toMatchObject({
      remaining: intent, scopeUnavailable: true,
    });
    expect(api.getChannelProfiles).not.toHaveBeenCalled();
    vi.mocked(api.getChannelProfiles).mockRejectedValue(new HttpError('Different connection', 409));
    expect(await applyCreatedChannelProfiles(intent, binding)).toMatchObject({
      remaining: intent, scopeUnavailable: true,
    });
    expect(api.getChannelProfiles).toHaveBeenCalledExactlyOnceWith(binding);
    expect(api.bulkUpdateProfileChannels).not.toHaveBeenCalled();
    expect(api.updateProfileChannel).not.toHaveBeenCalled();
  });
});
